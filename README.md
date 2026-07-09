# GUI-CREW

Web GUI for complete CrewAI coverage.

## Status

🚧 **In Development** — Building a visual interface for CrewAI multi-agent systems.

## Features

- **Builder**: Visual configuration forms for crews, agents, tasks, tools, memory, LLMs
- **Canvas**: DAG editor for visual topology (n8n-style)
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
- [ ] PR 2a: crew_engine.py Adapter + BridgeListener
- [ ] ... (13 PRs total)

## License

MIT
