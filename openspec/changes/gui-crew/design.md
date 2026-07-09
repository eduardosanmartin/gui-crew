# Design: gui-crew — Web GUI for Complete CrewAI Coverage

## Technical Approach

NiceGUI single-page app with 4 routed views (Builder/Canvas/Observability/Operations) sharing a Pydantic config layer (`models.py`) and a CrewAI execution adapter (`crew_engine.py`). The crew engine runs `kickoff_async()` on a background thread and uses CrewAI's **event bus** (modern `BaseEventListener`, not legacy callbacks) to translate CrewAI events into a flat JSON protocol pushed to the browser via NiceGUI's `Event[T].emit()` over its native WebSocket. Observability renders three layers from one event stream. Crew configs serialize to CrewAI-native JSONC/YAML for round-trip with the CLI.

## Architecture Decisions

| # | Decision | Choice | Alternatives | Rationale |
|---|----------|--------|--------------|-----------|
| 1 | CrewAI runtime bridge | Custom `BaseEventListener` subclass + `crewai_event_bus.on()` | Legacy `step_callback`/`task_callback` callables | Event bus is CrewAI's current API; maps cleanly to GUI websocket protocol; callbacks are deprecated-direction and don't cover tool/memory/knowledge events |
| 2 | UI update transport | `nicegui.events.Event[T].emit()` + `.subscribe()` | `ui.timer` polling / manual websocket | Event is thread-safe, broadcast to all clients, no JS; built for background-thread→UI pattern (verified in `examples/threaded_nicegui`) |
| 3 | Session state | `app.storage.user` (server-side, cookie-keyed) + `app.storage.client` (per-connection) | `ui.state` only / global dict | `user` survives nav + cross-tab; `client` holds live streams/AbortHandles discarded on close; requires `storage_secret` in `ui.run()` |
| 4 | Execution model | `threading.Thread` wrapping `asyncio.run(crew.kickoff_async())` | `background_tasks.create` coroutine | `kickoff_async` is async; thread isolates blocking CrewAI work from NiceGUI loop; Event bridges back. `background_tasks` used only for short async UI work |
| 5 | Config layer | Loose Pydantic v2 models, `extra="allow"` | Tight `extra="forbid"` mirror | CrewAI evolves fast; allow-extras + explicit fields means new params degrade gracefully; adapter migrates on load |
| 6 | DAG canvas (Fase 2) | Custom Vue/Quasar nodes via `ui.html`+element, events to Python | JS DAG lib (Drawflow/jsPlumb) | Keep single 8-file structure; defer external JS deps. n8n-style via `ui.element` q-card proxies + absolutely-positioned SVG edges |
| 7 | Long-run progress visibility | Progress events emitted from tool loops + notification area with live log | Silent cooperative flag only | UX requirement: users must see activity during long tool calls (30s+); notification area shows tool name, elapsed time, partial output; prevents "is it stuck?" confusion |
| 7b | Event broadcast isolation | Per-client `Event[ProtocolEvent]` keyed by `crew_id` + subscriber-side filtering | Global broadcast without filtering | Critical for multi-tab/multi-crew scenarios; each Observability subscriber MUST filter by active crew_id before rendering; protocol includes crew_id in every event; server-side dedup prevents cross-contamination |
| 8 | Pricing/cost | Local YAML table `pricing.yaml`, editable in Operations | Hardcoded / external API | Per non-functional req "configurable per-model pricing"; local + editable keeps offline + zero-transfer |

## Long-Running Tool Progress Visibility

**Problem**: CrewAI tools (e.g., `CodeInterpreterTool`, web scraping, API calls) can take 30+ seconds. During this time, the user sees no activity and may think the GUI is frozen.

**Solution**: Three-layer progress mechanism:

### 1. Tool Loop Hooks (CrewAI-side)
CrewAI's `BaseTool` has a `_run()` method that can be wrapped. The `BridgeListener` registers a custom tool wrapper that emits progress events at key points:
- `tool.call_start` (already in protocol)
- `tool.progress` (NEW) — emitted every 5 seconds during long tool execution, includes:
  - `tool_name`, `elapsed_ms`, `status_message` (e.g., "Executing code...", "Waiting for response...")
  - `partial_output` (if tool supports streaming output)
- `tool.call_end` (already in protocol)

**Implementation**: `crew_engine.py` provides a `ProgressToolWrapper` that wraps CrewAI tools. The wrapper is applied in `Adapter.build_crewai_object()` when converting Pydantic models to CrewAI instances. The wrapper checks `flag["stop"]` between progress emissions (cooperative cancellation).

### 2. Notification Area (UI-side)
Observability's **Meso layer** includes a "Tool Activity" panel that shows:
- Currently executing tool (name + elapsed time, updating live)
- Recent tool calls (last 10, with duration and status)
- Progress messages from `tool.progress` events

**Implementation**: `observability.py` renders a `ui.log` component (auto-scrolling) that subscribes to `tool.progress` events. Each progress event appends a line: `[12:34:56] CodeInterpreterTool: Executing code... (5s elapsed)`.

### 3. Status Bar (Macro layer)
The Macro layer's pipeline view shows a status bar below the active task:
- Task name + agent role
- Current tool (if any) + elapsed time
- "Running..." indicator with spinner

**Implementation**: `render_macro()` includes a `ui.row` with `ui.spinner()` + `ui.label` bound to the latest `tool.progress` event for the active task.

**Cancellation integration**: The `flag["stop"]` is checked:
1. In `ProgressToolWrapper` between progress emissions (every 5s)
2. In `BridgeListener` handlers on every CrewAI event
3. In the main `kickoff_async` loop (if CrewAI exposes hooks)

If `flag["stop"]` is True, the wrapper raises `CancelledError`, which propagates up and triggers `crew.error` event with reason "User cancelled".

**UX benefit**: User always sees activity, even during 30s tool calls. The "Stop" button feels responsive (cancels within 5s max).

## WebSocket Event Protocol — Broadcast Isolation

**Problem**: NiceGUI's `Event[T].emit()` broadcasts to ALL connected clients. If Tab A runs Crew X and Tab B runs Crew Y, both tabs receive all events.

**Solution**: Two-layer isolation:

### Layer 1: Protocol-level crew_id
Every event in the protocol includes `crew_id` (see table above). This is mandatory.

### Layer 2: Subscriber-side filtering (MANDATORY)
Each Observability subscriber MUST filter by `crew_id` before rendering:

```python
def render_observability():
    active_crew_id = app.storage.client.get("active_crew_id")
    
    @ui.refreshable
    def macro_view():
        # ... render pipeline ...
    
    def on_event(event: ProtocolEvent):
        if event.get("crew_id") != active_crew_id:
            return  # CRITICAL: skip events from other crews
        macro_view.refresh()
    
    Event[ProtocolEvent].subscribe(on_event)
```

**Server-side dedup (optional enhancement)**: If two tabs run the SAME crew (crew_id identical), the server can deduplicate by tracking `crew_id → list[client_id]` and only emitting to subscribed clients. This is an optimization, not a correctness requirement.

**Test requirement (Fase 1)**: Integration test that:
1. Opens two browser tabs (or simulates two clients)
2. Tab 1 runs Crew A (crew_id = "a")
3. Tab 2 runs Crew B (crew_id = "b")
4. Assert Tab 1's Observability only receives events with crew_id = "a"
5. Assert Tab 2's Observability only receives events with crew_id = "b"

**Risk if not implemented**: Events from Crew A appear in Crew B's dashboard. User confusion, incorrect metrics, broken UX.

## Data Flow — Real-time Execution

```
 User[Run Crew] ──▶ crew_engine.run() ──spawn──▶ Thread
       │                                        │ async loop: kickoff_async(inputs)
       │                                        │ CrewAI event bus emits ─┐
       │  Event[ProtocolEvent].emit(payload) ◀──┘                        │
       ▼                                                                 │
 Observability ──subscribe──▶ Macro / Meso / Micro refreshables ────────┘
       │
 app.storage.user['crew_model'] ◀── Builder (shared source of truth)
```

### Sequence: "Run Crew" → tokens stream

```
UI        crew_engine         Thread      CrewAI(bus)      Event[T]     Observability
 │─run──▶│                    │           │                 │             │
 │       │─spawn──────────────▶           │                 │             │
 │       │                   │─kickoff_async(inputs)       │             │
 │       │                   │           │─CrewKickoff──▶  │             │
 │       │                   │           │                 │─emit──────▶│ macro: 0%
 │       │                   │           │─TaskStarted──▶  │             │
 │       │                   │           │                 │─emit──────▶│ task→running
 │       │                   │           │─LLMStreamChunk▶ │             │
 │       │                   │           │                 │─emit──────▶│ micro: token
 │       │                   │           │─ToolUsageStart▶ │             │
 │       │                   │           │                 │─emit──────▶│ meso: tool card
 │       │                   │           │─CrewKickoffComp▶             │
 │       │                   │           │                 │─emit──────▶│ macro 100%+cost
 │◀─done─│                   │─join◀─────│                 │             │
```

## WebSocket Event Protocol

All events shipped over one `Event[ProtocolEvent]`. `ProtocolEvent = dict` with JSON-serializable values.

| `type` | payload keys | source CrewAI event | layer |
|--------|--------------|---------------------|-------|
| `crew.started` | crew_id, crew_name, task_count, ts | CrewKickoffStartedEvent | Macro |
| `crew.completed` | crew_id, status, token_usage, cost, ts | CrewKickoffCompletedEvent | Macro+Resource |
| `task.state_change` | task_name, old_state, new_state, ts | TaskStartedEvent / TaskCompletedEvent | Macro |
| `agent.started`/`.completed` | agent_role, task, ts | AgentExecutionStarted/CompletedEvent | Meso |
| `tool.call_start` | agent_role, tool_name, params, ts | ToolUsageStartedEvent | Meso |
| `tool.progress` | tool_name, elapsed_ms, status_message, partial_output?, ts | derived from tool loop hooks | Meso |
| `tool.call_end` | result_summary, duration_ms, error?, ts | ToolUsageFinishedEvent | Meso |
| `token.stream` | agent_role, is_thinking, text, ts | LLMStreamChunkEvent | Micro |
| `memory.op` | kind, query, query_time_ms, ts | MemoryQueryCompletedEvent | Meso |
| `knowledge.op` | kind, query, chunks, ts | KnowledgeRetrievalStarted/CompletedEvent | Meso |
| `llm.call_started`/`.completed` | model, ts | LLMCallStarted/CompletedEvent | Micro |
| `resource.update` | task, agent, tokens_in/out, cost, dur, iters | derived from completion events | Resource |
| `crew.error` | error, traceback, failed_task, ts | caught in thread | Macro+Error |
| `crew.stopped` | reason, ts | user stop | Macro |

**Connection mgmt**: NiceGUI auto-reconnects websocket; the event bus lives server-side so missed events during <30s reconnect are tolerant (last N buffered per `crew_id` and replayed on reconnect subscription). Heartbeat: a `ui.timer(15s)` emits `crew.heartbeat` only while running to detect dead sessions (60s no-activity → offer force-stop, matching the "no activity" non-functional spec).

## State Management — `app.storage.user` structure

```python
app.storage.user = {
  "crew_model": CrewModel(...).model_dump(),     # current edited crew (Builder←→Canvas shared)
  "mode": "builder"|"canvas"|"obs"|"ops",        # active pilar
  "wizard_state": {"step": int, "data": {...}},  # guided wizard progress
  "ui_prefs": {"advanced": bool, "theme": str},
  "history": [RunRecord(model_dump(), ...)],      # recent runs (Fase 2)
  "templates_custom": {...},                      # user-saved templates (Fase 2)
  "crewai_version": "1.15.3",                     # for adapter migration
}
app.storage.client = {                 # per-connection, dies on reload
  "execution": ExecutionHandle(...),   # thread, event subscription token, flag
  "stream_buffer": deque(maxlen=5000), # token micro-layer virtualization
}
```

- Persisted crew configs: serialize `CrewModel` → CrewAI-native JSONC/YAML (local file `crews/<name>.jsonc`); CLI-importable. `user["crew_model"]` is the editable draft; file is written on explicit Save/Export.
- Concurrent crews: one `execution` handle per client connection → one concurrent run per browser tab. Second "Run" while running is rejected ("A crew is already running") per engine spec edge case. Multi-crew tabs (observability edge case) map to multiple browser tabs, each its own `client`.

## Pydantic Model Layer (`models.py`)

```python
class LLMModel(BaseModel):
    model: str = "openai/gpt-4o"
    temperature: float | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict = {}  # extra="allow" forward-compat

class MemoryConfig(BaseModel):
    enabled: bool = False
    recency_weight: float | None = None
    semantic_weight: float | None = None
    importance_weight: float | None = None
    recency_half_life_days: int | None = None
    embedder: dict | None = None

class ToolRef(BaseModel):
    kind: Literal["builtin","custom"]
    name: str
    params: dict = {}
    # custom only
    args_schema: dict | None = None

class AgentModel(BaseModel):
    role: str; goal: str; backstory: str = ""
    llm: LLMModel | None = None
    function_calling_llm: LLMModel | None = None
    tools: list[ToolRef] = []
    allow_delegation: bool = False
    allow_code_execution: bool = False
    max_iter: int | None = None
    memory: bool | MemoryConfig = False
    system_template: str | None = None
    multimodal: bool = False
    extra: dict = {}  # verbose, cache, max_rpm, step_callback(stored as template id), etc.

class TaskModel(BaseModel):
    description: str; expected_output: str
    agent_role: str | None = None
    context: list[str] = []          # task names (DAG edges)
    output_file: str | None = None
    output_json: str | None = None
    human_input: bool = False
    async_execution: bool = False
    guardrails: list[str] = []
    guardrail_max_retries: int = 3
    tools: list[ToolRef] = []
    markdown: bool = False

class CrewModel(BaseModel):
    name: str; description: str = ""
    process: Literal["sequential","hierarchical"] = "sequential"
    agents: list[AgentModel] = []
    tasks: list[TaskModel] = []
    memory: bool | MemoryConfig = False
    planning: bool = False
    manager_llm: LLMModel | None = None
    manager_agent_role: str | None = None
    knowledge_sources: list[dict] = []
    embedder: dict | None = None
    inputs: list[InputVar] = []        # {name,type,description,default}
    callbacks: dict = {}               # template ids→ {before_kickoff,after_kickoff,...}
    extra: dict = {}
    @field_validator("context")
    def _no_cycle(self): ...   # topological check
    @model_validator(mode="after")
    def _hierarchical_requires_manager(self): ...
```

Validation rules: required-field presence (role/goal/name), hierarchical process requires `manager_llm` OR `manager_agent_role`, no task self-context, no cycle in task context DAG (Kahn's algorithm), variable `{name}` must exist in `crew.inputs` (warning-level). Serialization: `to_crewai_jsonc()` / `to_crewai_yaml()` / `from_crewai_*()` adapters. `schema_version` field drives `crewai_version`-aware migration on load.

## CrewAI Integration (`crew_engine.py`)

```python
class CrewEngine:
    def run(self, crew_model: CrewModel, inputs: dict[str,Any], on_event) -> ExecutionHandle:
        crew = Adapter.build_crewai_object(crew_model)           # Pydantic→CrewAI instances
        listener = BridgeListener(crew_id, on_event)            # BaseEventListener subclass
        listener.register()                                      # crewai_event_bus.on(...)
        flag = {"stop": False}
        def _worker():
            try:
                async def _main():
                    out = await crew.kickoff_async(inputs=inputs)
                    async for chunk in out:                      # also covered by LLMStreamChunkEvent
                        if flag["stop"]: raise CancelledError
                asyncio.run(_main())
            except Exception as e:
                on_event({"type":"crew.error","error":str(e),"traceback":...})
            finally:
                listener.unregister()
        thread = threading.Thread(target=_worker, daemon=True); thread.start()
        return ExecutionHandle(thread=thread, flag=flag, listener=listener)

    def stop(self, handle): handle.flag["stop"]=True; handle.thread.join(timeout=5)
    def test_task(self, crew_model, task_name, mock_context): ...  # single-task (Fase 2)
    def test_agent(self, crew_model, agent_role, prompt): ...     # playground
```

`BridgeListener(BaseEventListener)` registers handlers for every event in the protocol table above, each translating `event` attrs → dict and invoking `on_event(dict)`. Token accounting: accumulate from `LLMStreamChunkEvent`/completion, finalize from `CrewKickoffCompletedEvent.output.token_usage`; cost = `tokens_in*in_price + tokens_out*out_price` from `pricing.yaml`. Adapter layer `Adapter.build_crewai_object()` is the ONLY place importing `crewai` classes — version upgrades touch this file only. Callback routing: builder `callbacks` stores *template ids* (e.g. `"log_to_file"`); adapter expands ids to callables; handler errors logged, never re-raised (per spec).

**ProgressToolWrapper**: Wraps CrewAI tools to emit `tool.progress` events every 5s during long executions. Applied in `Adapter.build_crewai_object()` when converting Pydantic `ToolRef` to CrewAI tool instances. The wrapper:
1. Starts a background timer that emits `tool.progress` with `elapsed_ms` and `status_message`
2. Checks `flag["stop"]` between emissions (cooperative cancellation)
3. Stops the timer when `_run()` completes
4. Captures partial output if the tool supports streaming (e.g., `CodeInterpreterTool` stdout)

This ensures users see activity during 30s+ tool calls and can cancel within 5s max.

## File Responsibilities

| File | LOC est | Public surface | Depends on |
|------|---------|----------------|------------|
| `app.py` | ~120 | `ui.run(storage_secret=...)`, `@ui.page` routes (`/builder`,`/canvas`,`/obs`,`/ops` + `/` redirect), header/nav drawer, theme bind | styles, all views |
| `builder.py` | ~600 | `render_builder()`, `render_crew_form/agent_form/task_form/tool_form/llm_form/memory_form/knowledge_form`, `render_wizard()`, `render_tool_catalog()`, variable-interpolation preview | models, crew_engine(Adapter for serialization), styles |
| `canvas.py` | ~450 | `render_canvas()`, node/edge CRUD, auto-layout, bidirectional sync via `Event` ↔ Builder, DAG validation | models, styles |
| `observability.py` | ~550 | `render_observability()`, `render_macro/meso/micro/resource/error`, `@ui.refreshable` consumers subscribing to `Event[ProtocolEvent]` with **mandatory crew_id filtering**, reconnect buffer replay, tool activity log (Meso), status bar with progress (Macro) | crew_engine(`Event` source), models, styles |
| `operations.py` | ~450 | `render_playground()`, `render_templates()`, `render_history()`, `render_import_export()`, `render_single_task_test()`; pricing editor | crew_engine, models, observability(reuses micro), styles |
| `crew_engine.py` | ~400 | `CrewEngine`, `BridgeListener(BaseEventListener)`, `Adapter` (Pydantic↔CrewAI, the ONLY `crewai` importer), `ProgressToolWrapper` (tool progress events + cancellation), `pricing.yaml` loader | models |
| `models.py` | ~400 | `CrewModel/AgentModel/TaskModel/ToolRef/LLMModel/MemoryModel/InputVar/RunRecord`, validators, `to/from_crewai_jsonc/yaml`, `schema_version` | pydantic |
| `styles.py` | ~150 | theme tokens, CSS classes, quasar prop constants, thinking-vs-answer token styles | nicegui |

Import boundary: `crew_engine` is the sole importer of `crewai`. `models` imports only pydantic. UI files import `models` + `styles`; `observability` imports `crew_engine` for the `Event` handle only — never CrewAI types. This is the adapter isolation the spec requires.

## Testing Strategy

| Layer | What | How |
|-------|------|-----|
| Unit | Pydantic validators (cycles, hierarchical, var refs), `to/from_crewai_jsonc` round-trip, pricing calc, Adapter Pydantic→CrewAI (mocked CrewAI classes), BridgeListener event→dict translation (synthetic events) | pytest; no CrewAI server needed for models; monkeypatch `crewai` in adapter tests |
| Integration | End-to-end `kickoff_async` of a 2-agent/2-task toy crew wired through BridgeListener→Event→asserted payloads; playground single-agent kick; import JSONC round-trip populating Builder; **multi-tab event isolation** (two crews in parallel, assert each Observability only receives its own crew_id events) | pytest + real `crewai` (mock LLM via `litellm` fake or Ollama local) |
| E2E | NiceGUI page via `httpx`/playwright: load Builder, fill crew, click Run, assert Event sequence received; canvas node add cancels builder; history compare | NiceGUI test client + playwright; skip if no LLM key (env gate) |

## Migration / Rollout

No data migration — greenfield. Crew configs persist as CrewAI-native JSONC/YAML, CLI-importable (and CLI-exported files load into GUI) — this is the forwards/backwards compatibility boundary. Fase 1→Fase 2: `canvas.py` is additive (new route), reuses `models.py` + shares the same `crew_model` via Event sync; no schema migration needed. Adapter `schema_version` handles CrewAI minor-version config migration on load. Rollback = delete project files; each Fase independent.

## Phasing Strategy

**Fase 1** — Builder(`builder.py` minus canvas-triggered node ops) + Observability(all 3 layers + resource) + Playground(ops). Shared: `models.py` full, `crew_engine.py` full event-bus bridge + Adapter, `app.py` 3 routes, `styles.py`. Surface NOT in Fase 1: canvas route, templates gallery, history persistence UI, import/export menu items, single-task test (left as playground adjacency). Migration path: Fase 2 adds `canvas.py` + new ops views + canvas↔builder Event sync; everything else already built.

**Fase 2** — Canvas node/edge/autolayout/validation + bidirectional sync; Templates gallery + custom template save; History persistence + side-by-side compare; full Import/Export UI; single-task test triggered from canvas context menu. Reuses Fase 1 engine + models + micro-layer component.

**Shared between phases**: `models.py`, `crew_engine.py` (Adapter+BridgeListener), `app.py` shell, `styles.py`, observability micro/token component, `Event[ProtocolEvent]` protocol.

## Open Questions

- [ ] Canvas rendering: pure Quasar absolute-position cards + SVG edges (no JS dep) vs. embedding a small JS DAG lib via `ui.html` — tradeoff is maintainability vs. UX polish for 100 nodes (Fase 2 decision)
- [ ] Pricing table source: ship defaults for common (gpt-4o, claude, ollama=free) and let users extend, or require full manual entry from day one

## Resolved Risks

- ✅ **Long-run cancellation**: Mitigated via `ProgressToolWrapper` that emits `tool.progress` events every 5s and checks `flag["stop"]` between emissions. Cancellation happens within 5s max. Users see live activity in Meso layer (tool activity log) and Macro layer (status bar with progress).
- ✅ **Event broadcast isolation**: Mitigated via mandatory `crew_id` filtering in every Observability subscriber. Protocol includes `crew_id` in every event. Integration test required in Fase 1 to verify multi-tab isolation.