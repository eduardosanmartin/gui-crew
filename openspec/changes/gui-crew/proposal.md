# Proposal: gui-crew — Web GUI for Complete CrewAI Coverage

## Intent

CrewAI is powerful but only accessible through code or CLI. Developers who know CrewAI want a visual surface to configure and observe crews; non-technical users want to build crews without writing Python. Today there is no polished, full-coverage visual tool covering all 80+ CrewAI parameters with real-time execution observability. gui-crew fills that gap.

## Scope

### In Scope
- **Pilar 1 — Builder**: full-feature config forms for crews, agents, tasks, tools, memory, knowledge, LLMs, flows, variables, output. Advanced mode (all params) + guided wizard mode.
- **Pilar 2 — Canvas**: n8n-style DAG editor — drag-and-drop nodes/edges, visual topology, complements Builder.
- **Pilar 3 — Observability**: real-time dashboard with 3 layers:
  - **Macro layer** (crew-level): pipeline visualization, current active task, task states (pending → running → completed/failed), overall progress
  - **Meso layer** (agent-level): agent currently executing, tool invocations (what tool, parameters, result, duration, errors), delegations between agents (who delegated to whom, context, response), memory operations (reads/writes, type: short_term, long_term, entity, user)
  - **Micro layer** (token-level): streaming tokens in real-time, agent thinking/reasoning chain, tokens per second metric, differentiate thinking vs final answer
- **Pilar 4 — Operations**: playground (single-agent), templates, execution history, import/export (YAML/JSON CrewAI-native), single-task testing.
- **Crew engine**: CrewAI integration — kickoff_async, streaming, callbacks, token accounting.
- **App shell**: NiceGUI routing, theme, session state.

### Out of Scope (Future Versions)
These features are outside the scope of the current implementation but will be addressed in future versions:
- **Deployment/hosting/packaging** (planned for future release — priority)
- Multi-user auth / RBAC / collaboration
- Cloud persistence backends (current version uses local files + optional lancedb)
- Custom tool execution sandboxing beyond CrewAI's own (Docker for CodeInterpreter is user's responsibility)
- Visual flow editor beyond the canvas (flow decorators configured via Builder)

## Capabilities

> Greenfield — no existing specs. Each becomes a new `openspec/specs/<name>/spec.md`.

### New Capabilities
- `crew-builder`: Pilar 1 — configuration forms for all CrewAI entities, advanced + guided modes
- `canvas-editor`: Pilar 2 — DAG visual topology editor (nodes, edges, layout)
- `crew-observability`: Pilar 3 — live execution dashboard (macro/meso/micro layers)
- `operations-toolkit`: Pilar 4 — playground, templates, history, import/export, single-task testing
- `crew-engine`: CrewAI integration layer — kickoff, streaming, callbacks, token/cost accounting

### Modified Capabilities
None — greenfield.

## Approach

**Stack**: NiceGUI + Quasar (WebSocket-native, Pythonic, no JS). **8 files**: `app.py` (entry/routing), `builder.py`, `canvas.py`, `observability.py`, `operations.py`, `crew_engine.py`, `models.py`, `styles.py`. Pydantic models in `models.py` mirror CrewAI config surface; UI stays loosely coupled for API evolution.

State: `app.storage.user` per session; crew defs serialize to CrewAI-native JSONC/YAML. Execution: background thread runs `kickoff_async()`, pushes chunks to UI via `Event` over WebSocket.

**Phasing** (two delivery slices of the COMPLETE product — NOT MVP):
- **Fase 1**: Builder + Observability + Playground.
- **Fase 2**: Canvas + Templates + History + Import/Export + Single-task testing.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `app.py` | New | NiceGUI entry, routing, theme |
| `builder.py` | New | Pilar 1 forms |
| `canvas.py` | New | Pilar 2 DAG editor |
| `observability.py` | New | Pilar 3 dashboard |
| `operations.py` | New | Pilar 4 tools |
| `crew_engine.py` | New | CrewAI execution/streaming |
| `models.py` | New | Pydantic config models |
| `styles.py` | New | theme/colors |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| CrewAI API evolution breaks models | Med | Loose Pydantic models; version pin; adapter layer in `crew_engine.py` |
| 80+ params overwhelm non-technical users | Med | Guided wizard mode + sensible defaults; advanced tucked away |
| DAG canvas complexity (Fase 2) | Med | Defer to Fase 2; start with form-based task context; incremental node types |
| NiceGUI smaller community | Low | Quasar is mature; pin stable NiceGUI version |
| Tool execution needs Docker | Low | Document requirement; graceful fallback messaging |

## Rollback Plan

Greenfield — rollback = delete project files. Per-phase: each Fase is independent; Fase 2 can be reverted without touching Fase 1. No data migration: persisted crew configs remain CrewAI-native JSONC/YAML, importable by CrewAI CLI directly.

## Dependencies

- CrewAI v1.15+ (kickoff_async, streaming chunks, callbacks)
- NiceGUI (stable, with Quasar components)
- crewai-tools (built-in tools catalog)
- Pydantic (config models)
- lancedb (optional, memory backend)
- Docker (optional, CodeInterpreterTool)

## Success Criteria

### Fase 1
- [ ] Configure a full crew (80+ params) via Builder, advanced + guided modes
- [ ] Run crew live; see tokens stream, agent states, tool calls, delegations in Observability
- [ ] Test a single agent in Playground without full crew kickoff
- [ ] Export config as CrewAI-native JSONC/YAML; re-import round-trips

### Fase 2
- [ ] Build crew topology by dragging nodes/edges on Canvas
- [ ] Load a template, run it, observe full execution
- [ ] Compare two past executions in History
- [ ] Run a single task with test input (n8n-node-style)