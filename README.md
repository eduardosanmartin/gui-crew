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

### Current Progress

- [x] PR 1a: Pydantic models with CrewAI serialization (105 tests passing)
- [ ] PR 1b: styles.py + app.py shell
- [ ] PR 2a: crew_engine.py Adapter + BridgeListener
- [ ] ... (13 PRs total)

## License

MIT
