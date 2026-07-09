# Tasks: gui-crew — Web GUI for Complete CrewAI Coverage

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,600 (impl + tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | 13 PRs (see table below) |
| Delivery strategy | ask-on-risk |
| Chain strategy | **feature-branch-chain** |
| Tracker branch | `feature/gui-crew-fase1` |
| Exceptions | PR 8 (Canvas, ~450 LOC), PR 9 (Fase 2, ~600 LOC) |

Decision needed before apply: **Resolved**
Chained PRs recommended: **Yes**
Chain strategy: **feature-branch-chain**
400-line budget risk: **High** (mitigated by 13-PR subdivision)

### Suggested Work Units (13 PRs — Feature Branch Chain)

| Unit | Goal | PR | Base | Est. LOC | Notes |
|------|------|----|------|----------|-------|
| 1a | `models.py` (Pydantic models + validators) | PR 1a | `main` | ~400 | Foundation. All other PRs depend on this. |
| 1b | `styles.py` + `app.py` shell + routing | PR 1b | `main` | ~270 | Independent. Can parallelize with 1a. |
| 2a | `crew_engine.py` Adapter + BridgeListener | PR 2a | PR 1a | ~350 | Core event bus bridge. |
| 2b | `crew_engine.py` run/stop + ProgressToolWrapper | PR 2b | PR 2a | ~350 | Execution + progress events. |
| 3a | `builder.py` crew/agent/task forms | PR 3a | PR 1a | ~400 | Basic forms. |
| 3b | `builder.py` wizard + advanced sub-forms | PR 3b | PR 3a | ~400 | Guided mode + advanced params. |
| 4 | `builder.py` tool catalog + LLM/memory/knowledge | PR 4 | PR 3b | ~300 | Advanced sub-forms completion. |
| 5a | `observability.py` macro layer | PR 5a | PR 2a | ~300 | Pipeline visualization. |
| 5b | `observability.py` meso layer + crew_id filtering | PR 5b | PR 5a | ~350 | Agent/tool cards + mandatory filtering. |
| 6 | `observability.py` micro + resource + error | PR 6 | PR 5b | ~400 | Token streaming + cost panel. |
| 7 | `operations.py` playground | PR 7 | PR 2a + PR 3a | ~250 | Single-agent testing. |
| 8 | `canvas.py` DAG editor | PR 8 | PR 1a | ~450 | **size:exception** — Fase 2. |
| 9 | Templates + History + Import/Export + sync | PR 9 | PR 3a + PR 7 + PR 8 | ~600 | **size:exception** — Fase 2. |

**Total**: 13 PRs, ~5,470 LOC (implementation + tests)

---

## Fase 1: Builder + Observability + Playground

### File: `models.py` (Foundation — all other files depend on this)

- [x] 1.1 Implement base models: `LLMModel`, `MemoryConfig`, `ToolRef`, `InputVar` (~60 LOC, Risk: Low)
  - **Description**: Create Pydantic v2 base models with `extra="allow"` for forward compatibility.
  - **AC**: GIVEN no models exist, WHEN instantiating `LLMModel(model="gpt-4o", temperature=0.7)`, THEN it validates and serializes to JSON.
  - **Deps**: None

- [x] 1.2 Implement `AgentModel` with all CrewAI agent fields (~80 LOC, Risk: Low)
  - **Description**: Role, goal, backstory, llm, tools, allow_delegation, memory, etc.
  - **AC**: GIVEN a crew config, WHEN adding an agent with role="Researcher" and 2 tools, THEN the model validates and stores tool refs correctly.
  - **Deps**: 1.1

- [x] 1.3 Implement `TaskModel` with context DAG validation (~70 LOC, Risk: Med)
  - **Description**: Description, expected_output, agent_role, context (task names for edges), output_file, guardrails, etc.
  - **AC**: GIVEN tasks A and B, WHEN B.context includes "A", THEN the model validates. WHEN B.context includes itself, THEN validation fails with "Task cannot depend on itself".
  - **Deps**: 1.1

- [x] 1.4 Implement `CrewModel` with hierarchical validator and cycle detection (~90 LOC, Risk: Med)
  - **Description**: Crew-level config with Kahn's algorithm for cycle detection, hierarchical process requires manager_llm or manager_agent_role.
  - **AC**: GIVEN process="hierarchical", WHEN saving without manager_llm or manager_agent_role, THEN validation fails. WHEN tasks form a cycle (A→B→C→A), THEN validation fails with cycle path.
  - **Deps**: 1.2, 1.3

- [x] 1.5 Implement serialization adapters: `to_crewai_jsonc/yaml` and `from_crewai_*` (~80 LOC, Risk: Med)
  - **Description**: Convert Pydantic models to/from CrewAI-native JSONC/YAML. Include `schema_version` for migration.
  - **AC**: GIVEN a `CrewModel` instance, WHEN calling `to_crewai_jsonc()`, THEN output matches CrewAI CLI schema. WHEN round-tripping through `from_crewai_jsonc()`, THEN all fields preserve.
  - **Deps**: 1.4

- [x] 1.6 Implement `RunRecord` model for execution history (~30 LOC, Risk: Low)
  - **Description**: Stores run metadata: crew snapshot, timestamp, duration, token_usage, cost, status.
  - **AC**: GIVEN a completed crew run, WHEN creating `RunRecord`, THEN it serializes all metadata fields.
  - **Deps**: 1.4

- [x] 1.7 Write unit tests for Pydantic validators and serialization (~120 LOC, Risk: Low)
  - **Description**: Test cycle detection, hierarchical validation, variable interpolation warnings, JSONC/YAML round-trip.
  - **AC**: GIVEN test suite, WHEN running `pytest tests/test_models.py`, THEN all assertions pass.
  - **Deps**: 1.1–1.6

### File: `styles.py` (Independent)

- [ ] 1.8 Define theme tokens, CSS classes, and Quasar prop constants (~80 LOC, Risk: Low)
  - **Description**: Color palette, spacing, typography, card/ form styles. Quasar-specific prop mappings.
  - **AC**: GIVEN `styles.py` imported, WHEN accessing `styles.THEME.primary`, THEN a valid color hex is returned.
  - **Deps**: None

- [ ] 1.9 Define thinking-vs-answer token styles and dark-mode tokens (~40 LOC, Risk: Low)
  - **Description**: Distinct CSS classes for reasoning tokens (italic, dimmed) vs final answer tokens.
  - **AC**: GIVEN token type="thinking", WHEN rendering with `styles.TOKEN_THINKING`, THEN CSS applies italic + dimmed color.
  - **Deps**: 1.8

### File: `app.py` (Shell — depends on styles)

- [ ] 1.10 Implement NiceGUI entry point with `storage_secret` and session init (~40 LOC, Risk: Low)
  - **Description**: `ui.run()` with `storage_secret`, title, favicon. Init `app.storage.user` defaults.
  - **AC**: GIVEN the app starts, WHEN loading `/`, THEN `app.storage.user` contains default `crew_model`, `mode`, `ui_prefs`.
  - **Deps**: 1.8

- [ ] 1.11 Implement routing: `/builder`, `/obs`, `/ops`, `/` redirect (~50 LOC, Risk: Low)
  - **Description**: `@ui.page` decorators for each view. Header nav drawer with active state.
  - **AC**: GIVEN the app is running, WHEN navigating to `/obs`, THEN the Observability view renders and nav highlights "Observability".
  - **Deps**: 1.10

- [ ] 1.12 Implement header navigation and theme binding (~30 LOC, Risk: Low)
  - **Description**: Top bar with crew name, run/stop buttons (conditional), theme toggle.
  - **AC**: GIVEN a crew is running, WHEN viewing any page, THEN header shows "Stop" button. WHEN clicked, THEN stop signal fires.
  - **Deps**: 1.11

### File: `crew_engine.py` (Core engine — observability and operations depend on this)

- [ ] 1.13 Implement `Adapter` class: Pydantic → CrewAI object conversion (~100 LOC, Risk: Med)
  - **Description**: Sole importer of `crewai` package. Converts `CrewModel` → `Crew`, `AgentModel` → `Agent`, etc. Applies `ProgressToolWrapper` to tools.
  - **AC**: GIVEN a valid `CrewModel`, WHEN calling `Adapter.build_crewai_object()`, THEN a CrewAI `Crew` instance is returned with all params mapped. WHEN CrewAI v1.16 changes `Memory` constructor, THEN only this file changes.
  - **Deps**: 1.5

- [ ] 1.14 Implement `BridgeListener(BaseEventListener)` with event protocol mapping (~90 LOC, Risk: Med)
  - **Description**: Registers handlers on `crewai_event_bus` for all event types. Translates CrewAI events → `ProtocolEvent` dicts with `crew_id`.
  - **AC**: GIVEN a synthetic `CrewKickoffStartedEvent`, WHEN `BridgeListener` receives it, THEN it emits `{"type":"crew.started","crew_id":"x",...}`.
  - **Deps**: 1.13

- [ ] 1.15 Implement `CrewEngine.run()` with background thread + `kickoff_async` (~70 LOC, Risk: Med)
  - **Description**: Spawns `threading.Thread` wrapping `asyncio.run(crew.kickoff_async())`. Emits events via callback. Checks `flag["stop"]` between chunks.
  - **AC**: GIVEN a crew config and inputs, WHEN calling `engine.run()`, THEN a background thread starts, `kickoff_async` executes, and UI shows "running" without blocking.
  - **Deps**: 1.14

- [ ] 1.16 Implement `CrewEngine.stop()` and cooperative cancellation (~40 LOC, Risk: Med)
  - **Description**: Sets `flag["stop"]=True`, joins thread with 5s timeout. Raises `CancelledError` in `ProgressToolWrapper`.
  - **AC**: GIVEN a running crew, WHEN clicking "Stop", THEN `flag["stop"]` becomes True, thread terminates within 5s, and `crew.stopped` event emits.
  - **Deps**: 1.15

- [ ] 1.17 Implement `ProgressToolWrapper` for long-running tool visibility (~60 LOC, Risk: Med)
  - **Description**: Wraps CrewAI tools. Emits `tool.progress` every 5s with elapsed_ms and status_message. Checks `flag["stop"]` between emissions.
  - **AC**: GIVEN a tool taking 30s, WHEN executing via `ProgressToolWrapper`, THEN `tool.progress` events emit every 5s. WHEN stop is requested, THEN cancellation happens within 5s.
  - **Deps**: 1.15

- [ ] 1.18 Implement token/cost accounting and `pricing.yaml` loader (~50 LOC, Risk: Low)
  - **Description**: Accumulates tokens from `LLMStreamChunkEvent` + completion events. Calculates cost: `tokens_in * price_in + tokens_out * price_out`.
  - **AC**: GIVEN a completed run with 1k input / 2k output tokens on gpt-4o, WHEN calculating cost, THEN result matches `pricing.yaml` rates within ±5%.
  - **Deps**: 1.15

- [ ] 1.19 Implement callback routing (template IDs → callables) (~40 LOC, Risk: Low)
  - **Description**: Expands builder `callbacks` dict (template IDs) to actual Python callables. Errors logged, never re-raised.
  - **AC**: GIVEN a callback template id `"log_to_file"`, WHEN crew executes, THEN the callback runs for each step. WHEN callback raises, THEN crew continues and error is logged.
  - **Deps**: 1.13

- [ ] 1.20 Write engine unit + integration tests (~150 LOC, Risk: Med)
  - **Description**: Monkeypatch `crewai` for Adapter tests. Synthetic event tests for BridgeListener. End-to-end toy crew kickoff with real `crewai` (mock LLM).
  - **AC**: GIVEN test suite, WHEN running `pytest tests/test_engine.py`, THEN all assertions pass including multi-tab event isolation.
  - **Deps**: 1.13–1.19

### File: `builder.py` (Fase 1 — no canvas sync yet)

- [ ] 1.21 Implement crew configuration form (advanced mode) (~100 LOC, Risk: Med)
  - **Description**: All crew fields: name, process, memory, planning, manager_llm, knowledge_sources, etc. Conditional display of hierarchical fields.
  - **AC**: GIVEN advanced mode active, WHEN filling name="Research Crew" and process="sequential", THEN form validates and saves to `app.storage.user["crew_model"]`.
  - **Deps**: 1.4, 1.8

- [ ] 1.22 Implement agent configuration form with tool selection (~120 LOC, Risk: Med)
  - **Description**: Role, goal, backstory, llm, function_calling_llm, tools, memory, allow_delegation, etc. Tool multi-select from catalog.
  - **AC**: GIVEN the builder is open, WHEN adding agent "Researcher" and selecting 2 tools, THEN agent appears in crew agent list with correct tool refs.
  - **Deps**: 1.21

- [ ] 1.23 Implement task configuration form with context dependencies (~100 LOC, Risk: Med)
  - **Description**: Description, expected_output, agent assignment, context (task dependency multi-select), output_file, guardrails, etc.
  - **AC**: GIVEN tasks "Research" and "Write Report", WHEN editing "Write Report" and adding "Research" to context, THEN task model updates and DAG edge is implied.
  - **Deps**: 1.22

- [ ] 1.24 Implement LLM / memory / knowledge sub-forms (~80 LOC, Risk: Low)
  - **Description**: Reusable form components for LLM config, memory settings, knowledge sources.
  - **AC**: GIVEN an agent form, WHEN opening the LLM sub-form and setting model="claude-3-5-sonnet", THEN the LLM config saves to the agent model.
  - **Deps**: 1.21

- [ ] 1.25 Implement guided wizard mode (5 steps) (~120 LOC, Risk: Med)
  - **Description**: Step-by-step: (1) template or blank, (2) goal, (3) add agents, (4) define tasks, (5) review & save. Progress indicator. Sensible defaults. Advanced params hidden.
  - **AC**: GIVEN a first-time user, WHEN selecting "Guided Wizard", THEN 5-step flow appears with progress bar, and advanced fields are hidden.
  - **Deps**: 1.24

- [ ] 1.26 Implement tool catalog display and custom tool form (~80 LOC, Risk: Med)
  - **Description**: Built-in crewai-tools catalog with search/filter. Custom tool form: name, description, Pydantic args_schema editor.
  - **AC**: GIVEN the tool catalog is open, WHEN searching "serper", THEN `SerperDevTool` appears. WHEN adding a custom tool with args_schema, THEN it serializes correctly.
  - **Deps**: 1.22

- [ ] 1.27 Implement variable interpolation preview (~40 LOC, Risk: Low)
  - **Description**: Live preview of `{variable}` interpolation in goal/backstory fields using default values from `crew.inputs`.
  - **AC**: GIVEN crew has input `{topic}="AI"`, WHEN typing goal="Research {topic}", THEN a live preview shows "Research AI".
  - **Deps**: 1.23

- [ ] 1.28 Implement save/load crew to/from `app.storage.user` (~40 LOC, Risk: Low)
  - **Description**: Explicit Save button writes `CrewModel` to `app.storage.user["crew_model"]`. Load on page init.
  - **AC**: GIVEN a configured crew, WHEN clicking "Save", THEN `app.storage.user["crew_model"]` contains the full crew dump. WHEN reloading the page, THEN form populates from storage.
  - **Deps**: 1.21–1.27

### File: `observability.py` (Fase 1 — all 3 layers + resource)

- [ ] 1.29 Implement macro layer: pipeline visualization with task states (~100 LOC, Risk: Med)
  - **Description**: Render all tasks with states (pending→running→completed/failed). Progress bar. Active task highlight. Status bar with spinner + current tool name.
  - **AC**: GIVEN 3 sequential tasks running, WHEN task 1 completes, THEN task 1 shows "completed", task 2 shows "running", progress bar updates to 33%.
  - **Deps**: 1.14, 1.8

- [ ] 1.30 Implement meso layer: agent cards, tool invocations, delegations, memory ops (~120 LOC, Risk: Med)
  - **Description**: Current agent card. Tool invocation cards (name, params, status, duration, error). Delegation events (from→to, context, response). Memory ops (kind, query, time). Tool Activity log (`ui.log`).
  - **AC**: GIVEN agent "Researcher" invokes `SerperDevTool`, WHEN tool starts, THEN a card appears with "running" status. WHEN tool returns, THEN card updates with result and duration.
  - **Deps**: 1.29

- [ ] 1.31 Implement micro layer: token streaming with thinking/answer distinction (~100 LOC, Risk: Med)
  - **Description**: Real-time token streaming text area. Thinking tokens in `styles.TOKEN_THINKING` style. Tokens-per-second metric. Virtual scrolling for >10k tokens.
  - **AC**: GIVEN streaming is enabled, WHEN an agent produces output, THEN tokens appear in real-time. WHEN `is_thinking=True`, THEN tokens render italic+dimmed.
  - **Deps**: 1.29, 1.9

- [ ] 1.32 Implement resource consumption panel (~60 LOC, Risk: Low)
  - **Description**: Table with columns: Task, Agent, Duration, Tokens In, Tokens Out, Est. Cost, Iterations. Total cost prominently displayed.
  - **AC**: GIVEN 2 tasks have completed, WHEN viewing resource panel, THEN both rows appear with accurate tokens and cost.
  - **Deps**: 1.18, 1.30

- [ ] 1.33 Implement error panel with traceback and retry visibility (~60 LOC, Risk: Med)
  - **Description**: Error cards with stack trace. Retry events show "retrying (attempt 1/3)" in timeline. Callback invocations logged.
  - **AC**: GIVEN a task fails with guardrail_max_retries=3, WHEN retry triggers, THEN timeline shows retry attempt counter. WHEN all retries fail, THEN error panel shows final error + traceback.
  - **Deps**: 1.30

- [ ] 1.34 Implement WebSocket event subscription with mandatory `crew_id` filtering (~50 LOC, Risk: High)
  - **Description**: Every subscriber MUST filter `event["crew_id"] == active_crew_id` before rendering. Protocol-level crew_id is mandatory.
  - **AC**: GIVEN two browser tabs run Crew A and Crew B simultaneously, WHEN events emit, THEN Tab A only receives crew_id="a" events and Tab B only receives crew_id="b" events.
  - **Deps**: 1.14, 1.29

- [ ] 1.35 Implement reconnect buffer replay (~40 LOC, Risk: Med)
  - **Description**: Buffer last N events per `crew_id` (deque maxlen). Replay on re-subscription after WebSocket disconnect.
  - **AC**: GIVEN a disconnect during execution, WHEN reconnecting within 30s, THEN missed events replay and dashboard catches up.
  - **Deps**: 1.34

### File: `operations.py` (Fase 1 — Playground only)

- [ ] 1.36 Implement agent playground UI (~80 LOC, Risk: Med)
  - **Description**: Agent dropdown (from crew), prompt textarea, "Run" button. Output panel with streaming (reuse observability micro component).
  - **AC**: GIVEN crew has agent "Researcher", WHEN selecting it in playground, typing "What is CrewAI?", and clicking "Run", THEN agent executes with its tools and output streams in real-time without full crew kickoff.
  - **Deps**: 1.16, 1.31

- [ ] 1.37 Implement playground execution via `CrewEngine.test_agent()` (~40 LOC, Risk: Med)
  - **Description**: Engine method to run single agent with custom prompt. Respects agent's `max_execution_time` and `max_iter`.
  - **AC**: GIVEN agent config, WHEN calling `engine.test_agent(agent_role, prompt)`, THEN the agent executes in isolation and returns output.
  - **Deps**: 1.16

- [ ] 1.38 Implement prompt iteration with side-by-side comparison (~40 LOC, Risk: Low)
  - **Description**: Previous run output remains visible. "Re-run" button clears output and re-executes with updated prompt.
  - **AC**: GIVEN a playground run just completed, WHEN editing the prompt and clicking "Re-run", THEN a new execution starts and previous output stays visible for comparison.
  - **Deps**: 1.36

---

## Fase 2: Canvas + Templates + History + Import/Export + Single-task Testing

### File: `canvas.py`

- [ ] 2.1 Implement canvas rendering: absolute-position Quasar cards + SVG edges (~100 LOC, Risk: Med)
  - **Description**: Custom Vue/Quasar nodes via `ui.element` q-card proxies. SVG edges between nodes. No external JS dependency.
  - **AC**: GIVEN a crew with 2 agents and 2 tasks, WHEN opening canvas, THEN 4 nodes render with distinct shapes and edges connect related nodes.
  - **Deps**: 1.4, 1.8

- [ ] 2.2 Implement node palette and drag-and-drop (~60 LOC, Risk: Med)
  - **Description**: Sidebar palette with Agent and Task node types. Drag onto canvas creates node at drop position.
  - **AC**: GIVEN the canvas is empty, WHEN dragging an "Agent" node from palette onto canvas, THEN a new agent node appears at drop position with placeholder name.
  - **Deps**: 2.1

- [ ] 2.3 Implement edge creation with output/input handles (~60 LOC, Risk: Med)
  - **Description**: Drag from node output handle to another node's input handle. Self-connect rejected with tooltip.
  - **AC**: GIVEN two task nodes "Research" and "Write", WHEN dragging from Research output to Write input, THEN a directed edge appears and Write's context updates.
  - **Deps**: 2.2

- [ ] 2.4 Implement node/edge CRUD with confirmation dialogs (~50 LOC, Risk: Low)
  - **Description**: Select node + Delete key or remove button. Confirmation if node has edges. Deleting agent with assigned tasks shows warning.
  - **AC**: GIVEN a node with 2 connected edges, WHEN clicking remove, THEN a confirmation dialog appears. WHEN confirmed, THEN node and edges are removed.
  - **Deps**: 2.3

- [ ] 2.5 Implement auto-layout algorithm (~60 LOC, Risk: Med)
  - **Description**: Top-down hierarchical layout. Root tasks at top, leaves at bottom. Minimize edge crossings.
  - **AC**: GIVEN scattered nodes, WHEN clicking "Auto-layout", THEN nodes rearrange into top-down flow with clear hierarchy.
  - **Deps**: 2.4

- [ ] 2.6 Implement zoom/pan controls and fit-to-screen (~40 LOC, Risk: Low)
  - **Description**: Zoom 25%–200%. Pan by dragging canvas background. Fit-to-screen button.
  - **AC**: GIVEN 20 nodes on canvas, WHEN zooming to 50%, THEN all nodes scale. WHEN clicking fit-to-screen, THEN all nodes are visible in viewport.
  - **Deps**: 2.1

- [ ] 2.7 Implement DAG validation with visual error highlighting (~50 LOC, Risk: Med)
  - **Description**: Cycle detection on edge creation. Invalid edges highlighted red. Error message shows cycle path.
  - **AC**: GIVEN tasks A→B→C, WHEN adding edge C→A, THEN the edge is highlighted red and tooltip shows "Circular dependency: A → B → C → A".
  - **Deps**: 2.3

- [ ] 2.8 Implement undo/redo stack (minimum 50 steps) (~60 LOC, Risk: Med)
  - **Description**: Record every mutation (add/remove/move/edge). Ctrl+Z / Ctrl+Shift+Z.
  - **AC**: GIVEN a canvas with changes, WHEN pressing Ctrl+Z, THEN last change reverses. WHEN pressing Ctrl+Shift+Z, THEN change re-applies.
  - **Deps**: 2.2

### File: `operations.py` (Fase 2 — Templates, History, Import/Export, Single-task)

- [ ] 2.9 Implement built-in template gallery (5 templates) (~80 LOC, Risk: Low)
  - **Description**: Research Crew, Code Review Crew, Content Writer Crew, Data Analysis Crew, Customer Support Crew. Load into builder.
  - **AC**: GIVEN the template gallery, WHEN selecting "Research Crew" and clicking "Use Template", THEN a new crew appears in Builder with pre-configured agents and tasks.
  - **Deps**: 1.25, 1.28

- [ ] 2.10 Implement custom template save functionality (~40 LOC, Risk: Low)
  - **Description**: "Save as Template" button. Stores current crew to `app.storage.user["templates_custom"]`.
  - **AC**: GIVEN a configured crew, WHEN clicking "Save as Template" and naming it "My Template", THEN it appears in custom templates gallery.
  - **Deps**: 2.9

- [ ] 2.11 Implement execution history persistence to local file (~60 LOC, Risk: Med)
  - **Description**: Save `RunRecord` to `history/<crew_name>/<timestamp>.json`. Load on app start.
  - **AC**: GIVEN a completed run, WHEN checking `history/` directory, THEN a JSON file exists with run metadata. WHEN reloading app, THEN history list populates.
  - **Deps**: 1.6, 1.16

- [ ] 2.12 Implement history list view with filtering (~50 LOC, Risk: Low)
  - **Description**: Table with date, crew name, status, duration, cost. Filters: date range, crew name, status.
  - **AC**: GIVEN 50 history entries, WHEN filtering by crew="Research Crew" and status="failed", THEN only matching 3 entries appear.
  - **Deps**: 2.11

- [ ] 2.13 Implement side-by-side execution comparison (~60 LOC, Risk: Low)
  - **Description**: Select 2 runs, click "Compare". Side-by-side view: inputs, outputs, duration, tokens, cost. Diff highlighting.
  - **AC**: GIVEN two runs with different inputs, WHEN selecting both and clicking "Compare", THEN side-by-side view shows both runs with differences highlighted.
  - **Deps**: 2.12

- [ ] 2.14 Implement export to CrewAI-native JSONC/YAML with download (~50 LOC, Risk: Med)
  - **Description**: Export button uses `CrewModel.to_crewai_jsonc/yaml()`. Browser download prompt.
  - **AC**: GIVEN a configured crew, WHEN clicking "Export" → "JSONC", THEN a file downloads matching CrewAI CLI schema.
  - **Deps**: 1.5

- [ ] 2.15 Implement import from JSONC/YAML with validation (~70 LOC, Risk: Med)
  - **Description**: File upload, parse, validate with line-reference errors. Populate builder forms and canvas.
  - **AC**: GIVEN a valid `crew.jsonc` file, WHEN importing, THEN builder and canvas populate correctly. WHEN importing invalid YAML, THEN error panel shows parse error with line number.
  - **Deps**: 1.5, 2.1

- [ ] 2.16 Implement import round-trip verification (~30 LOC, Risk: Low)
  - **Description**: Test: export crew → import → assert functionally identical.
  - **AC**: GIVEN a crew, WHEN exporting then re-importing, THEN the imported crew matches original (all params preserved).
  - **Deps**: 2.14, 2.15

- [ ] 2.17 Implement single-task test UI and execution (~60 LOC, Risk: Med)
  - **Description**: Right-click task node in canvas → "Test Task". Mock context input textarea. Run button. Output panel.
  - **AC**: GIVEN task "Summarize" depending on "Research", WHEN right-clicking and selecting "Test Task" with mock context, THEN only "Summarize" executes with mock input and result appears in test panel.
  - **Deps**: 1.37, 2.1

### File: `builder.py` (Fase 2 additions — Canvas sync)

- [ ] 2.18 Implement bidirectional builder→canvas sync (~50 LOC, Risk: Med)
  - **Description**: Adding agent in builder emits `Event` → canvas creates node. Deleting edge in canvas updates builder task context.
  - **AC**: GIVEN builder and canvas both open, WHEN adding agent "Editor" in builder, THEN canvas shows "Editor" node at free position. WHEN deleting edge A→B in canvas, THEN task B's context removes A in builder.
  - **Deps**: 2.1, 1.28

### File: `app.py` (Fase 2 addition)

- [ ] 2.19 Add `/canvas` route and navigation link (~20 LOC, Risk: Low)
  - **Description**: New `@ui.page('/canvas')` route. Nav drawer updated.
  - **AC**: GIVEN the app is running, WHEN navigating to `/canvas`, THEN Canvas view renders and nav highlights "Canvas".
  - **Deps**: 2.1, 1.11

---

## Cross-Cutting: Testing & Integration

- [ ] T.1 Write E2E test: load Builder, fill crew, click Run, assert event sequence (~80 LOC, Risk: Med)
  - **Description**: NiceGUI test client or Playwright. Fill forms, trigger run, assert macro/meso/micro events received.
  - **AC**: GIVEN the app is running, WHEN E2E test executes, THEN it completes within 60s and all assertions pass.
  - **Deps**: 1.11, 1.21, 1.29

- [ ] T.2 Write E2E test: canvas node add cancels builder (~60 LOC, Risk: Med)
  - **Description**: Add node in canvas, verify builder updates. Delete edge, verify context updates.
  - **AC**: GIVEN canvas and builder open, WHEN adding a node in canvas, THEN builder reflects the change.
  - **Deps**: 2.18

- [ ] T.3 Write multi-tab event isolation integration test (~60 LOC, Risk: High)
  - **Description**: Simulate two clients. Tab 1 runs Crew A, Tab 2 runs Crew B. Assert each only receives its own crew_id events.
  - **AC**: GIVEN two simulated clients, WHEN both run different crews concurrently, THEN each observability dashboard only shows its own crew's events.
  - **Deps**: 1.34, 1.20

- [ ] T.4 Write pricing and cost calculation tests (~30 LOC, Risk: Low)
  - **Description**: Unit tests for cost math, unknown model fallback.
  - **AC**: GIVEN unknown model "foo-bar", WHEN calculating cost, THEN result shows "Unknown — model not in pricing table".
  - **Deps**: 1.18
