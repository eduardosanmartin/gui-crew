# Design: observability.py — Real-Time Execution Dashboard

## Technical Approach

Single-file `observability.py` (~470 LOC) following the `canvas.py` / `operations.py` pattern: module-level state dicts keyed by `crew_id`, `@ui.refreshable` for discrete panels, direct DOM mutation for token streaming. `app.py` adds a 3-line bridge wiring `CrewEngine` callbacks to a module-level event bus. Implements all 5 observation layers plus crew_id filtering and reconnect buffer per the crew-observability delta spec.

## Architecture Decisions

| Decision | Options | Tradeoff | Choice |
|----------|---------|----------|--------|
| State location | Module dict vs `app.storage.user` | Module survives reconnect; storage is per-tab | Module dicts keyed by `crew_id` |
| Rendering strategy | `@ui.refreshable` vs timer-poll | Refreshable = clean rebuild; timer = polling overhead | Refreshable for macro/meso/resource/error; direct mutation for tokens |
| Reconnect buffer | `deque(maxlen=N)` vs time-filter | Count-based unpredictable across crew sizes | `deque` (unbounded) + ts-eviction on insert (60s window) |
| Event distribution | Raw callback vs event bus | Bus enables multi-subscriber; callback is 1:1 | Module-level `Event[ProtocolEvent]` singleton |
| Error panel scope | All errors vs guardrail-only | Internal retries are noise | Guardrail retries only — filter by event type |

## Data Flow

```
CrewEngine.run(on_event)
    │
    ▼
crew_event_bus.emit(ProtocolEvent)        ← app.py bridge (3 lines)
    │
    ├──→ _buffer_event(evt)              ──→ _replay_buffer(crew_id) on reconnect
    │
    ▼
_dispatch(evt) ──→ crew_id gate ──→ handler
    │
    ├──→ _crew_state[crew_id]            ──→ Macro panel (@ui.refreshable)
    ├──→ _activity_log[crew_id]          ──→ Meso panel (@ui.refreshable)
    ├──→ _token_elements[crew_id]        ──→ Micro panel (direct mutation)
    ├──→ _resources[crew_id]             ──→ Resource panel (@ui.refreshable)
    └──→ _errors[crew_id]                ──→ Error panel (@ui.refreshable)
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `observability.py` | Create | 5 panels + crew_id filter + reconnect buffer (~470 LOC) |
| `crew_engine.py` | Modify | Add 4 guardrail events to `_CREWAI_EVENT_MAP` (~40 LOC) |
| `app.py` | Modify | `crew_event_bus` bridge + swap `_render_observability_placeholder` → `observability.render_observability` |

## Interfaces / Contracts

```python
from collections import deque
from crew_engine import ProtocolEvent

crew_event_bus = ...  # Event[ProtocolEvent] — module singleton, wired in app.py
_crew_state: dict[str, dict] = {}          # crew_id → {tasks, status, progress}
_activity_log: dict[str, list[dict]] = {}  # crew_id → meso card entries
_token_elements: dict[str, list] = {}      # crew_id → streaming UI element refs
_resources: dict[str, dict] = {}           # crew_id → {task: {tokens_in, out, cost, duration, iterations}}
_errors: dict[str, list[dict]] = {}       # crew_id → error entries with tracebacks
_event_buffer: deque = deque()             # unbounded; evict >60s on insert
_BUFFER_WINDOW_S: float = 60.0

def render_observability(crew_id: str | None = None) -> None: ...  # composes 5 panels
def _dispatch(event: ProtocolEvent) -> None: ...   # crew_id gate + type routing
def _buffer_event(event: ProtocolEvent) -> None: ...  # append + evict stale
def _replay_buffer(crew_id: str) -> None: ...      # chronological replay on reconnect

@ui.refreshable
def _render_macro(crew_id: str) -> None: ...   # pipeline, progress, status bar
@ui.refreshable
def _render_meso(crew_id: str) -> None: ...    # agent/tool/delegation/memory/knowledge cards
def _render_micro(crew_id: str) -> None: ...  # direct mutation — token streaming
@ui.refreshable
def _render_resource(crew_id: str) -> None: ...  # per-task table
@ui.refreshable
def _render_error(crew_id: str) -> None: ...   # traceback + guardrail retry counters
```

```python
# app.py bridge (3 lines):
import observability
crew_event_bus = observability.crew_event_bus
# engine.run(model, inputs, on_event=lambda e: crew_event_bus.emit(e))
```

Empty state: no active `crew_id` → macro renders blank panel with history stub (proposal decision 2).

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | crew_id filtering gate | Synthesize ProtocolEvent dicts with mismatched `crew_id`; assert zero state mutation |
| Unit | Buffer eviction + replay | Inject events with controlled `ts` values; assert >60s evicted; assert replay order chronological |
| Unit | State accumulation | Feed synthetic event sequences; assert correct task states, resource rows, error entries |
| Unit | Token style selection | Feed `is_thinking=True/False` tokens; assert `Token.THINKING` vs `Token.ANSWER` style applied |
| Unit | Guardrail retry counter | Simulate repeated `guardrail.failed` events; assert counter increments; assert internal retries suppressed |
| Unit | Guardrail event mapping | Mock CrewAI guardrail events; assert BridgeListener emits correct `guardrail.*` ProtocolEvents |
| Integration | app.py bridge wiring | Mock `CrewEngine.run`; assert events reach `crew_event_bus` and `_dispatch` |
| E2E | (deferred) | Manual verification: run crew, observe real-time rendering across panels |

Tests use synthetic `ProtocolEvent` dicts — no CrewAI or NiceGUI server needed. Matches `test_canvas.py` monkeypatch pattern.

## Migration / Rollout

No migration required. Delete `observability.py`; revert `app.py` to placeholder. Observability is stateless — no persisted data.

## Resolved Questions

### Q1: Guardrail Retry Detection ✅
**Resolution**: CrewAI emits `LLMGuardrailStartedEvent`, `LLMGuardrailFailedEvent`, `LLMGuardrailCompletedEvent` from `crewai.events`. These are importable the same way as already-mapped events.

**Implementation**: Add 4 new entries to `_CREWAI_EVENT_MAP` in `crew_engine.py`:
- `LLMGuardrailStartedEvent` → `guardrail.started`
- `LLMGuardrailCompletedEvent` → `guardrail.completed`
- `LLMGuardrailFailedEvent` → `guardrail.failed`
- `TaskFailedEvent` → `task.failed`

**Risk**: Exact field names on `LLMGuardrailFailedEvent` unconfirmed (CrewAI not installed in venv). Use `getattr` with defaults — graceful degradation at runtime.

### Q2: Per-Task Resource Tracking ✅
**Resolution**: Two-tier approach:
1. **Live**: Enrich `LLMCallCompletedEvent` extraction with `agent_role`, `task_name`, token counts, `response_time` → accumulate per-task in `observability.py`
2. **Fallback**: After `crew.completed`, iterate `result.tasks_output` in `_kickoff` and emit per-task `resource.update` events

**Risk**: Per-task resource tracking may degrade to post-hoc only if field names differ. Mitigated by defensive `getattr` with defaults.

### Q3: NiceGUI Event API ✅
**Resolution**: NiceGUI 3.14.0 installed. `Event` class at `nicegui.event`, generic `Event[P]`.

**Exact API**:
```python
from nicegui.event import Event
crew_event_bus = Event()
crew_event_bus.subscribe(callback, expect_args=True)
crew_event_bus.emit(payload)
crew_event_bus.unsubscribe(callback)
```

**Benefit**: Subscribers auto-unsubscribe on UI client deletion — handles multi-tab filtering automatically. No custom wrapper needed.