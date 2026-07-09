# crew-observability Specification

## Purpose

A real-time execution dashboard for CrewAI crews with three observation layers: **Macro**
(crew-level pipeline), **Meso** (agent-level tool calls, delegations, memory), and **Micro**
(token-level streaming). Provides full visibility into what the crew is doing at every level of
granularity.

## Requirements

### Requirement: Crew-Level Execution View (Macro Layer)

The system MUST display a pipeline visualization showing all tasks in the crew, their current state
(pending → running → completed/failed), the currently active task, and overall crew progress.

#### Scenario: Watch a crew execute sequentially

- GIVEN a crew with 3 sequential tasks is executing
- WHEN the crew starts
- THEN the pipeline SHALL show Task 1 as "running", Tasks 2–3 as "pending"
- AND as each task completes, its state SHALL transition to "completed" and the next SHALL become "running"
- AND the overall progress bar SHALL update from 0% to 100%

#### Scenario: Task fails during execution

- GIVEN Task 2 throws an error during execution
- WHEN the error occurs
- THEN Task 2's state SHALL transition to "failed"
- AND the crew pipeline SHALL stop (no further tasks execute)
- AND an error panel SHALL appear with the error message and traceback

### Requirement: Agent-Level Observation (Meso Layer)

The system MUST display, in real-time: the agent currently executing, tool invocations (tool name,
parameters, result, duration, errors), delegations between agents (who delegated to whom,
context, response), and memory operations (reads/writes with type: short_term, long_term, entity,
user).

#### Scenario: Agent invokes a tool

- GIVEN agent "Researcher" is executing a task
- WHEN the agent calls `SerperDevTool.search(query="climate change 2025")`
- THEN a tool invocation card SHALL appear showing: tool name, input parameters, status "running"
- AND when the tool returns, the card SHALL update with: result summary, duration (ms)
- AND if the tool errors, the card SHALL display the error in red

#### Scenario: Agent delegates to another agent

- GIVEN agent "Manager" is executing with delegation enabled
- WHEN Manager delegates a subtask to agent "Analyst"
- THEN a delegation event SHALL appear showing: from=Manager, to=Analyst, context snippet
- AND when Analyst responds, the delegation card SHALL update with the response summary

### Requirement: Token-Level Streaming (Micro Layer)

The system MUST stream tokens in real-time as the agent produces output, with visual distinction
between thinking/reasoning tokens and final answer tokens. MUST display tokens per second and
cumulative token count.

#### Scenario: Real-time token streaming

- GIVEN a crew is executing with streaming enabled
- WHEN the agent produces output
- THEN each token SHALL appear in a streaming text area in real-time
- AND thinking tokens SHALL render in a distinct style (italic, dimmed) from final answer tokens
- AND the tokens-per-second metric SHALL update dynamically

### Requirement: Resource Consumption Dashboard

The system MUST track and display per-task and per-agent: tokens used (input + output), estimated
cost (based on configured LLM pricing), execution duration, and iteration count.

#### Scenario: View resource costs during execution

- GIVEN a crew is running
- WHEN multiple tasks have completed
- THEN a resource panel SHALL show a table with columns: Task, Agent, Duration, Tokens In, Tokens
  Out, Est. Cost, Iterations
- AND the total estimated cost SHALL be prominently displayed

### Requirement: Error, Retry, and Callback Visibility

The system MUST display all errors with stack traces, automatic retries with attempt counters, and
callback invocations (on_task_start, on_task_end, etc.) as timeline events.

#### Scenario: Task retry after failure

- GIVEN a task with `guardrail_max_retries=3` fails
- WHEN the guardrail triggers a retry
- THEN the task state SHALL show "retrying (attempt 1/3)"
- AND each retry attempt SHALL be logged in the execution timeline

### Edge Cases and Error Handling

| Condition | Expected Behavior |
|---|---|
| WebSocket disconnects during execution | Auto-reconnect; buffer missed events for 30s |
| Very fast execution (sub-second tasks) | Timeline SHALL still capture all events |
| Very long output (>10k tokens) | Virtual scrolling in streaming view |
| Crew hangs (no output for 60s) | "No activity" warning with cancel option |
| Multiple crews running simultaneously | Each crew SHALL have its own tab in the dashboard |

### Non-Functional Requirements

- Dashboard MUST update within 200ms of receiving an event from the engine
- Streaming text area SHALL not block the UI thread during rendering
- Token cost estimates MUST use configurable per-model pricing tables
- Execution history SHALL be filterable by date, crew name, and status

### Dependencies

- `crew-engine`: emits all execution events via WebSocket streaming
- `operations-toolkit`: Execution History persists dashboard data
