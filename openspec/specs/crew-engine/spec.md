# crew-engine Specification

## Purpose

The CrewAI integration and execution layer. Manages `kickoff_async`, event streaming via
WebSockets, background thread execution, callback routing, and token/cost accounting. Serves as
the adapter between the GUI and CrewAI's Python API.

## Requirements

### Requirement: Asynchronous Crew Kickoff

The system MUST execute crews using CrewAI's `kickoff_async()` in a background thread so the
NiceGUI UI thread remains responsive. The engine SHALL emit execution events to the observability
dashboard in real-time.

#### Scenario: Start crew execution

- GIVEN a valid crew configuration is loaded in the app
- WHEN the user clicks "Run Crew"
- THEN the engine SHALL spawn a background thread
- AND `crew.kickoff_async()` SHALL be called with the current inputs
- AND the UI SHALL immediately show "running" state without blocking
- AND the "Run Crew" button SHALL be replaced with a "Stop" button

#### Scenario: Stop crew execution

- GIVEN a crew is running via `kickoff_async()`
- WHEN the user clicks "Stop"
- THEN the engine SHALL terminate the background thread
- AND any in-progress agent SHALL receive a stop signal
- AND the UI SHALL transition to "stopped" state

### Requirement: WebSocket Event Streaming

The system MUST stream execution events from the background thread to the frontend via NiceGUI's
WebSocket-based event system. Events SHALL include: task state changes, agent output tokens, tool
invocations, delegations, memory operations, errors, and resource metrics.

#### Scenario: Stream task state change event

- GIVEN a crew is executing in background
- WHEN a task transitions from "pending" to "running"
- THEN the engine SHALL emit a `task_state_change` event with payload: `{task_name, old_state,
  new_state, timestamp}`
- AND the observability dashboard SHALL receive and render it within 200ms

#### Scenario: Stream tool invocation event

- GIVEN an agent invokes a tool during execution
- WHEN the tool call begins
- THEN the engine SHALL emit a `tool_call_start` event: `{agent_role, tool_name, params}`
- AND when the tool returns, SHALL emit `tool_call_end`: `{result_summary, duration_ms, error?}`
- AND the observability meso layer SHALL render each as a card

### Requirement: Token and Cost Accounting

The system MUST track token usage per task and per agent from CrewAI's `token_usage` response.
The engine SHALL calculate estimated cost based on configurable per-model pricing tables.

#### Scenario: Track tokens per task

- GIVEN a crew executes 3 tasks
- WHEN all tasks complete
- THEN `token_usage` SHALL contain per-task breakdowns: input tokens, output tokens, model used
- AND the engine SHALL calculate estimated cost using the pricing table for each model

#### Scenario: Unknown model pricing

- GIVEN the user's LLM model is not in the pricing table
- WHEN execution completes
- THEN token counts SHALL still be displayed
- AND cost SHALL show "Unknown — model not in pricing table"

### Requirement: Callback Routing

The system MUST support CrewAI callbacks (`step_callback`, `task_callback`, `before_kickoff`,
`after_kickoff`) defined in the Builder's advanced panels. The engine SHALL route these to
user-defined Python callables or predefined hook templates.

#### Scenario: User-defined step callback

- GIVEN the user configured a `step_callback` that logs each step to a file
- WHEN the crew executes
- THEN the engine SHALL invoke the callback for every agent step
- AND callback errors SHALL be logged but SHALL NOT halt crew execution

### Requirement: Adapter Layer for API Evolution

The system MUST isolate CrewAI API calls behind a Pydantic-based adapter layer so that CrewAI
version upgrades only require changes to the adapter, not UI code.

#### Scenario: CrewAI API changes in a minor version

- GIVEN CrewAI v1.16 changes the `Memory` class constructor signature
- WHEN the adapter is updated to match the new API
- THEN all UI code (Builder, Canvas, Observability) SHALL continue working without changes
- AND existing serialized crew configs SHALL be auto-migrated on load

### Edge Cases and Error Handling

| Condition | Expected Behavior |
|---|---|
| `kickoff_async` raises before streaming starts | Error state in UI with full traceback |
| Background thread crashes mid-execution | Thread exception caught; UI shows "Execution failed" |
| CrewAI import fails (wrong version) | Startup error: "CrewAI v1.15+ required. Found: X.Y.Z" |
| Streaming chunk parsing error | Log warning; skip malformed chunk; continue streaming |
| Multiple concurrent kickoffs from same user | Queue or reject second kickoff: "A crew is already running" |
| CrewAI process hangs (no chunks for 120s) | Timeout detection; offer force-stop |

### Non-Functional Requirements

- Background thread SHALL NOT block the NiceGUI event loop
- Event emission latency SHALL be under 100ms from chunk receipt
- Token accounting MUST be accurate to ±5% of CrewAI's reported usage
- The adapter layer MUST be version-pinned (`crewai>=1.15,<2.0`)
- All engine errors MUST be logged with structured metadata (timestamp, crew_id, task_name)

### Dependencies

- `models.py`: Pydantic models for crew/agent/task config validation
- `crew-observability`: engine emits events; dashboard consumes them
- `crew-builder`: engine receives validated crew config from builder
- CrewAI `>=1.15` (Python package)
