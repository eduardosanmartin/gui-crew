# Exploration: Web GUI for Complete CrewAI Coverage

## Current State

Greenfield project. No existing code. The `gui-crew` project aims to build a full-featured Web GUI that exposes **every** CrewAI capability through a clean, minimalist interface. Target audience spans both developers who know CrewAI and non-technical users building crews without code.

## CrewAI Feature Surface — What the GUI Must Cover

Research based on CrewAI v1.15+ / Edge docs, the `crewai` skill, and the `crewaiinc/crewai` source. CrewAI's surface area is **large and deep** — a "simple" GUI must still expose serious complexity.

### Category 1: Crew Configuration
| Parameter | Type | GUI Implication |
|---|---|---|
| `name`, `description` | `str` | Text inputs |
| `process` | `Process.sequential` \| `Process.hierarchical` | Toggle/select |
| `memory` | `bool` \| `Memory(...)` | Toggle + advanced panel for weights |
| `planning` | `bool` | Toggle |
| `verbose` | `bool` | Toggle |
| `cache` | `bool` | Toggle |
| `max_rpm` | `int` | Number input |
| `stream` | `bool` | Always enabled for live execution tab |
| `manager_llm` / `manager_agent` | `LLM` \| `Agent` | LLM selector + agent dropdown (hierarchical only) |
| `knowledge_sources` | `list[BaseKnowledgeSource]` | File upload + preview |
| `embedder` | `dict` | Provider selector + model input |
| `output_log_file` | `str \| bool` | Path input |
| `step_callback`, `task_callback` | `callable` | Advanced — script injection or predefined hooks |
| `before_kickoff`, `after_kickoff` | `callable` | Advanced — script injection or predefined hooks |
| `planning_llm` | `LLM` | LLM selector |

### Category 2: Agent Configuration
| Parameter | Type | GUI Implication |
|---|---|---|
| `role`, `goal`, `backstory` | `str` | Textarea with `{variable}` interpolation preview |
| `llm` | `str \| LLM` | Provider/model selector with advanced config (temperature, top_p, max_tokens) |
| `function_calling_llm` | `str \| LLM` | Separate LLM selector |
| `tools` | `list[BaseTool]` | Multi-select + custom tool editor |
| `memory` | `bool` | Toggle |
| `verbose` | `bool` | Toggle |
| `allow_delegation` | `bool` | Toggle |
| `allow_code_execution` | `bool` | Toggle |
| `max_iter` | `int` | Number input |
| `max_rpm` | `int` | Number input |
| `max_execution_time` | `int` | Number input |
| `step_callback` | `callable` | Advanced panel |
| `cache` | `bool` | Toggle |
| `system_template`, `response_template` | `str` | Advanced textarea (prompt engineering) |
| `knowledge_sources` | `list[BaseKnowledgeSource]` | File upload |
| `embedder` | `dict` | Provider + model |
| `multimodal` | `bool` | Toggle |

### Category 3: Task Configuration
| Parameter | Type | GUI Implication |
|---|---|---|
| `description`, `expected_output` | `str` | Textarea with variable preview |
| `agent` | `Agent` | Dropdown from crew agents |
| `context` | `list[Task]` | Multi-select dependency graph (DAG visualization?) |
| `output_file`, `output_json`, `output_pydantic` | `str \| type` | Output format selector + path |
| `human_input` | `bool` | Toggle |
| `async_execution` | `bool` | Toggle |
| `guardrails` | `list[str \| Guardrail]` | Add/remove guardrail rules |
| `guardrail_max_retries` | `int` | Number input |
| `callback` | `callable` | Advanced panel |
| `tools` (task-level) | `list[BaseTool]` | Multi-select |
| `markdown` | `bool` | Toggle |
| `create_directory` | `bool` | Toggle |

### Category 4: Tools
- Built-in crewai-tools: SerperDevTool, ScrapeWebsiteTool, FileReadTool, CSVSearchTool, DirectoryReadTool, CodeInterpreterTool, etc.
- Custom BaseTool: name, description, args_schema (Pydantic model editor?), _run implementation
- Tool configuration parameters per tool (e.g., n_results, country for SerperDev)

### Category 5: Memory System
- Enable/disable with optional custom weights: recency_weight, semantic_weight, importance_weight, recency_half_life_days
- Storage backend: lancedb (default)
- Operations within GUI: explore memory tree, recall with search, forget by scope

### Category 6: Knowledge Sources
- Types: String, TextFile, PDF, CSV, Excel, JSON
- File upload or inline text entry
- Per-agent vs crew-level assignment
- Custom embedder configuration per source

### Category 7: Streaming (Live Execution)
- `crew.kickoff_async(inputs={...})` returns `CrewStreamingOutput`
- Chunk types: `TEXT` (agent output), `TOOL_CALL` (tool invocation)
- Per-chunk metadata: `task_name`, `agent_role`, `content`
- Final result access: `streaming.result`, `result.raw`, `result.token_usage`

### Category 8: Flows (Advanced)
- `@start`, `@listen`, `@router` decorators
- `and_()`, `or_()` combinators for parallel execution
- `@persist()` for state persistence
- Pydantic `BaseModel` for flow state
- `kickoff(restore_from_state_id=...)` for forking

### Category 9: LLM Configuration
- Multi-provider: OpenAI, Ollama, Anthropic, custom base_url
- Per-model: model name, temperature, top_p, max_tokens, api_key_env
- `LLM(model="openai/gpt-4o", temperature=0.1)` constructor

### Category 10: CLI Integration
- `crewai train`, `crewai test`, `crewai replay` — expose as GUI actions
- `crewai create crew` / `crewai create flow` — alternative to visual builder
- `crewai run` — equivalent to live execution tab

### Category 11: Input Variables
- Variables in `crew.jsonc` inputs: `{type, description, default}`
- Interpolation in role, goal, backstory, description, expected_output
- GUI must show preview with variable substitution

### Category 12: Output Configuration
- Text output (default)
- JSON output (`output_json` with Pydantic model or dict)
- File output (`output_file="path/to/result.md"`)
- Markdown rendering

### Summary: Total editable parameters across the surface exceeds **80+ distinct fields**, not counting dynamic tool configs, guardrails, or flow state models.

---

## Python Web Framework Evaluation

### Framework Comparison Matrix

| Framework | Single-file? | Real-time Streaming | Form Builder UX | UI Customization | Community | Async | CrewAI Integration |
|---|---|---|---|---|---|---|---|
| **NiceGUI** | ✅ Excellent | ⭐ Native WebSocket | ⭐ Excellent (Quasar) | ⭐ Full CSS/Vue | 🔶 12k stars | ⭐ Async native | ✅ Import & run |
| **Mesop** | ✅ Excellent | ⭐ Generator yield | ✅ Good (Material 3) | 🔶 Material only | 🔶 5k stars | ⭐ Async/gen | ✅ Import & run |
| **Streamlit** | ✅ Good | 🔶 st.write_stream | 🔶 Clunky (full rerun) | 🔶 Limited | ⭐ 36k stars | ❌ No async handlers | ✅ Import & run |
| **Gradio** | ✅ Good | ✅ Generators | 🔶 Limited builder UX | ❌ Hard to customize | ⭐ 35k stars | ✅ Generators | ✅ Import & run |
| **Reflex** | ✅ Good | ✅ WebSocket reactive | ✅ Good (Radix) | ✅ Full control | 🔶 20k stars | ✅ Async | ✅ Import & run |
| **FastAPI+HTMX** | ❌ Multi-file needed | ✅ SSE/WS | ✅ Full control | ✅ Full HTML/CSS | ⭐ 80k+ stars | ⭐ Async native | ✅ Import & run |
| **Dash (Plotly)** | ✅ Good | ❌ Not streaming-friendly | 🔶 Callback-heavy | 🔶 Grid-based | ⭐ Large | ❌ Sync callbacks | ✅ Import & run |
| **Panel (HoloViz)** | ✅ Good | ❌ Not streaming-first | 🔶 Dashboard-oriented | 🔶 Limited | 🔶 7k stars | ❌ Limited | ✅ Import & run |

### Detailed Analysis

#### 🥇 NiceGUI — RECOMMENDED
- **Why it wins**: WebSocket-native architecture is a PERFECT match for CrewAI's streaming execution. Background threads with `Event.emit()` + `ui.refreshable` + `ui.timer` give you a clean pattern for `crew.kickoff_async()`. The Quasar component library (Vue-based) gives polished, responsive, minimalist UI out of the box — cards, dialogs, tabs, forms, tree views, all without JavaScript.
- **Visual Builder fit**: Quasar's `q-expansion-item`, `q-card`, `q-select`, `q-input`, and dynamic element creation make building a form-based crew constructor natural. You can add/remove agents and tasks dynamically without full page rerenders.
- **Live Execution fit**: `crew.kickoff_async()` returns async generator → feed chunks into a `ui.log` or streaming text area via `label.set_text()` or `ui.markdown().set_content()`. Token usage displayed via a card in the sidebar. Background thread handles kickoff, UI thread stays responsive.
- **State management**: `app.storage.user` (per-session, server-side dict) or `ui.state()` inside `@ui.refreshable` components.
- **Single-file feasibility**: MVP (~800-1200 lines) fits easily in one file. Full coverage pushes 2000-3000 lines — still technically one file but better split into 5-8 organized files.
- **Risk**: Smaller community than Streamlit. But excellent docs, active maintainers, and Quasar foundation mitigates this.

#### 🥈 Mesop — STRONG ALTERNATIVE
- **Why it's close**: Google-backed, elegant generator-based streaming maps directly to CrewAI's `kickoff_async()`. Material Design 3 gives a modern, clean look. Typed state management (`@me.stateclass`) is clean and Pythonic. Single-file is natural.
- **Visual Builder fit**: Components are good but less flexible than NiceGUI's Quasar. Material's rigid layout may constrain a complex builder.
- **Risk**: Very young project (2024). Fewer examples, less battle-tested. Community is small. Lock-in to Material Design.

#### 🥉 Reflex — SOLID BUT HEAVIER
- **Why it's good**: Compiles to React + FastAPI. Reactive state is elegant. Rich component library via Radix UI. Full control over look and feel.
- **Why not #1**: The compile step adds friction. Development cycle is slower (compile → reload). Overkill for what is essentially a single-page config+execute app. 20k stars but younger than Streamlit/Gradio.

#### Streamlit / Gradio — LIMITATIONS FOR THIS USE CASE
- Streamlit's full-rerun-on-every-interaction model makes a **visual builder** painful. Every dropdown change reloads the page. For 80+ configurable parameters across nested structures, the UX degrades rapidly.
- Gradio's native look feels like a demo tool, not a polished product. Customizing appearance is difficult. The "builder" experience (dynamic form sections, drag-drop task ordering) is not Gradio's strength.

#### FastAPI+HTMX — POWERFUL BUT TOO MUCH WORK
- Gives complete control, but you're **building a framework**, not using one. WebSocket management, state sync, real-time updates — all manual. The "single-file" goal is impossible for full CrewAI coverage with this approach.

---

## Architecture Approach

### Can Full Coverage Fit in a Single File?

**Technically yes, practically no.** A single Python file covering all 80+ CrewAI parameters with a clean, usable UI would be 2500-3500 lines of dense UI code. That's maintainable by one person for a few weeks, then becomes a liability. Bugs become harder to isolate, and future CrewAI API changes require hunting through a monolith.

### Recommended Minimal Multi-File Structure

```
gui-crew/
├── app.py                    # Entry point: NiceGUI app, routing, theme (~50 lines)
├── models/
│   ├── crew_config.py        # Pydantic models mirroring CrewAI config (~150 lines)
│   └── llm_config.py         # LLM/embedder model definitions (~80 lines)
├── ui/
│   ├── crew_builder.py       # Crew+Agent+Task form builder (~400 lines)
│   ├── live_execution.py     # Streaming output + token usage dashboard (~250 lines)
│   ├── tool_editor.py        # Built-in tools selector + custom tool builder (~200 lines)
│   ├── flow_editor.py        # Flow graph: start/listen/router/state (~300 lines)
│   ├── knowledge_panel.py    # Knowledge source upload + management (~150 lines)
│   ├── llm_config.py         # LLM provider/model configuration panel (~150 lines)
│   └── components.py         # Shared reusable UI components (~100 lines)
├── state/
│   ├── app_state.py          # Global app state (current crew, active session) (~100 lines)
│   └── store.py              # Persistence: save/load crew JSONC configs (~150 lines)
├── executor/
│   └── crew_runner.py        # Kickoff wrapper, streaming handler, background thread (~150 lines)
└── utils/
    └── export.py             # Export to crew.jsonc / agents/*.jsonc (~80 lines)

Total: ~15 files, ~2300 lines, clean separation of concerns
```

### Live Execution Architecture

```
┌──────────────────────────────────────────────────────┐
│                    NiceGUI App                        │
│                                                      │
│  Frontend (Browser)    │    Backend (Python)          │
│  ═══════════════════    │    ═══════════════════       │
│                         │                             │
│  [Execute Button] ──────┼──→ Background Thread        │
│                         │    ├── crew.kickoff_async()  │
│                         │    └── for chunk in stream:  │
│  [Stream Output] ◄──────┼────── Event.emit(chunk)     │
│  [Token Counter] ◄──────┼────── Event.emit(usage)     │
│  [Agent Status]  ◄──────┼────── Event.emit(state)     │
│                         │                             │
│  WebSocket connection maintained by NiceGUI           │
└──────────────────────────────────────────────────────┘
```

### State Management

- **Session state**: `app.storage.user` dict (per-browser-session, survives page navigation)
- **Crew definition**: Pydantic model serialized to `app.storage.user["crew_model"]`
- **Execution state**: "idle" | "running" | "complete" | "error" — emitted via `Event`
- **Persistence**: Save/load as JSONC files (CrewAI native format) — round-trips perfectly

---

## Recommendation

### Primary: **NiceGUI + Quasar**

**Architecture**: Single-file MVP, multi-file production (~15 files). WebSocket-native, Quasar Material-like components, clean Python API.

**Why NiceGUI**:
1. **Streaming is native**: The WebSocket connection is automatic. `crew.kickoff_async()` chunks are pushed to the browser in real-time via `Event.emit()` and `ui.refreshable`. No polling, no SSE setup, no manual WebSocket management.
2. **Visual builder UX**: Quasar's component library (cards, expansion panels, selects, tabs, dialogs) gives the polished, minimal UI the user wants. Dynamic form construction (add/remove agents/tasks) is smooth without full page rerenders.
3. **Single-file MVP possible**: The core crew builder + live execution fits in one file for the first iteration.
4. **Pythonic**: Everything is Python. No JavaScript, no HTML templates, no compilation step.
5. **Background threading**: `threading.Thread` + `Event.emit()` is the cleanest pattern for non-blocking crew execution.

### Alternative: **Mesop** (if NiceGUI proves problematic)

Mesop's generator-based event handlers map elegantly to CrewAI streaming. Material Design 3 is clean and modern. Google backing is a plus for long-term maintenance. The main risk is maturity — Mesop is very young and the component library is less rich than Quasar.

### What NOT to Use
- **Streamlit**: The full-rerun model kills the visual builder experience. Complex forms with many interdependent fields become slow and janky.
- **Gradio**: Hard to make look polished. The "app" feel doesn't match the "visual builder + live dashboard" dual-mode requirement.
- **FastAPI+HTMX**: Too much manual work. You'd spend more time building infrastructure than delivering value.

---

## Risks

1. **NiceGUI community size**: At 12k stars, it's more niche than Streamlit/Gradio. Breaking changes could affect the project. Mitigation: Quasar (the underlying component library) is mature and heavily used.
2. **CrewAI API evolution**: CrewAI is evolving rapidly. The GUI's model layer must be designed for extensibility. Mitigation: Pydantic models loosely coupled to UI.
3. **Single-file aspiration**: Users may resist the multi-file structure. Mitigation: Start single-file, split only when clearly justified (at ~800+ lines).
4. **Non-technical user complexity**: Even with a visual builder, 80+ parameters can be overwhelming. Mitigation: Wizard mode (guided flow) + advanced mode (full config) — two-tier UX.
5. **Flow editor complexity**: Building a drag-and-drop flow editor (similar to n8n/LangFlow) is a massive undertaking. Mitigation: Start with form-based flow config, defer visual DAG editor to v2.
6. **Tool execution within GUI**: Running crewai-tools like CodeInterpreterTool requires Docker. This adds infrastructure complexity. Mitigation: Document Docker requirement clearly, graceful fallback.

---

## Ready for Proposal

**Yes** — This exploration provides sufficient foundation for a proposal. The recommended stack is **NiceGUI + Quasar** with a Pydantic model layer mirroring CrewAI's configuration surface. The next step is `sdd-propose` with clear scope boundaries (MVP: Crew+Agent+Task builder + Live Execution; Full: + Flows + Knowledge + Tools + Memory management).

### For the Orchestrator

The user should decide:
1. **Scope**: MVP (Crew/Agent/Task + Live Execution) first, or full coverage from the start?
2. **Single-file threshold**: Accept ~800-1000 line single file for MVP, or embrace multi-file from the start (~15 files for clean architecture)?
3. **Flow editor**: Form-based (simpler) or visual DAG (complex but powerful) for the Flow feature?
4. **Two-tier UX**: Wizard mode for non-technical users + Advanced mode for developers?
