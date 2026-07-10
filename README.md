# GUI-CREW

Web GUI for complete CrewAI coverage.

## Status

🚧 **In Development** — Building a visual interface for CrewAI multi-agent systems.

## Features

- **Builder**: Visual configuration forms for crews, agents, tasks, tools, memory, LLMs
- **Canvas**: DAG editor for visual crew topology editing
  - Agent (circle) and task (rectangle) nodes with SVG edge connections
  - Node palette for adding agents/tasks to the canvas
  - Click-based edge creation with cycle detection
  - Node/edge CRUD with delete confirmation dialogs
  - Auto-layout: top-down hierarchical algorithm via topological sort
  - Zoom controls (25%–200%) with fit-to-screen button
  - Real-time DAG validation with cycle highlighting in red
  - Undo/redo stack (50-step) with snapshot-based state management
  - Bidirectional sync with Builder via shared `CrewModel`
- **Observability**: Real-time execution dashboard with 3 layers (macro/meso/micro)
- **Operations**: Playground, templates, history, import/export

## Tech Stack

- **Backend**: Python + NiceGUI
- **Models**: Pydantic v2
- **Integration**: CrewAI

## Development

This project uses a Feature Branch Chain strategy with 13 PRs.

### Setup

```bash
# Install dependencies
pip install nicegui pydantic pytest

# Run tests
pytest tests/ -v

# Run the app
python app.py
# Then open http://localhost:8080
```

The app requires a `STORAGE_SECRET` for session management.  A dev default
is baked in — set `STORAGE_SECRET` in your environment for production:

```bash
export STORAGE_SECRET="your-secure-random-string"
python app.py
```

### Current Progress

- [x] PR 1a: Pydantic models with CrewAI serialization (105 tests passing)
- [x] PR 1b: styles.py + app.py shell (69 tests passing)
- [x] PR 2a: crew_engine.py Adapter + BridgeListener (92 tests passing)
- [x] PR 2b: crew_engine.py run/stop + ProgressToolWrapper + callbacks
- [x] PR 8 / PR 9: canvas.py DAG editor (76 tests passing)
- [ ] ... (13 PRs total)

## License

MIT
