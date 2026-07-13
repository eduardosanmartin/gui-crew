# Delta for crew-observability

## ADDED Requirements

### Requirement: Per-Connection crew_id Filtering

Every handler MUST gate on `event.crew_id == observer.crew_id`. Non-matching events SHALL be silently dropped.

#### Scenario: Two connections isolated

- GIVEN Connection A observes crew-X, B observes crew-Y
- WHEN crew-X emits `task.state_change`
- THEN A SHALL render it; B SHALL NOT receive it

#### Scenario: Cross-crew gate

- GIVEN `event.crew_id != observer.crew_id`
- WHEN handler processes the event
- THEN event SHALL be discarded before any UI mutation

### Requirement: Reconnect Buffer (60s Window)

Buffer all events in a `deque`; on reconnect, replay events within the 60s window chronologically. Evict events older than 60s on each insert.

#### Scenario: Reconnect replays missed events

- GIVEN crew running, observer disconnects 30s
- WHEN observer reconnects
- THEN gap events SHALL replay in order

#### Scenario: Stale eviction

- GIVEN buffer: t=0, t=30, t=61; new event at t=62
- WHEN inserted
- THEN t=0 evicted (older than 60s)

## MODIFIED Requirements

### Requirement: Crew-Level Execution View (Macro Layer)

Pipeline with task states (pending/running/completed/failed/retrying), progress bar, status bar (running/completed/stopped/error). Cold load: blank panel with history stub.

(Previously: no status bar, retrying state, or empty-state behavior.)

#### Scenario: Sequential execution

- GIVEN crew with 3 tasks starts
- WHEN tasks execute
- THEN pipeline transitions pending→running→completed per task; progress bar 0%→100%

#### Scenario: Task failure

- GIVEN Task 2 throws error
- WHEN error fires
- THEN Task 2 shows "failed"; pipeline stops; error panel appears with traceback

#### Scenario: Empty state

- GIVEN no active crew
- WHEN navigating to /observability
- THEN blank panel renders with history stub

### Requirement: Agent-Level Observation (Meso Layer)

SHALL display: tool invocations (name, input, result, duration, errors), delegations (from/to, context, response), memory/knowledge ops (read/write with type).

(Previously: memory only; now includes knowledge operations.)

#### Scenario: Tool invocation

- GIVEN "Researcher" calls a tool
- WHEN call starts
- THEN card shows name, input, status "running"; on completion: result, duration; on error: red display

#### Scenario: Delegation

- GIVEN "Manager" delegates to "Analyst"
- WHEN delegation fires
- THEN card shows from, to, context; on response: summary

#### Scenario: Knowledge operation

- GIVEN agent queries knowledge
- WHEN `knowledge.op` fires
- THEN card shows operation type and result summary

### Requirement: Token-Level Streaming (Micro Layer)

SHALL stream tokens: thinking (`Token.THINKING`, italic/dimmed) vs answer (`Token.ANSWER`, normal). SHALL display tokens/sec and cumulative count.

(Previously: thinking/answer without concrete style constants.)

#### Scenario: Styled streaming

- GIVEN crew streaming enabled
- WHEN agent produces output
- THEN thinking tokens render italic/dimmed, answer normal; tokens/sec updates dynamically

### Requirement: Resource Consumption Dashboard

SHALL track per-task: tokens (in+out), exact cost from `resource.update`, duration, iterations. Cost shows "—" during streaming — no live approximation.

(Previously: estimated cost during execution; now exact only with "—" placeholder.)

#### Scenario: Completion update

- GIVEN task completes, emits `resource.update`
- WHEN panel renders
- THEN row shows Task, Agent, Duration, Tokens, Cost (exact), Iterations; crew total increments

#### Scenario: Streaming placeholder

- GIVEN task streaming
- WHEN panel renders
- THEN cost displays "—"; SHALL NOT estimate from token count

### Requirement: Error Visibility (Guardrail Retries Only)

SHALL display errors with tracebacks. Only guardrail retries (`guardrail_max_retries`) SHALL be visible; internal CrewAI tool retries SHALL NOT appear.

(Previously: all retries visible; now guardrail-only.)

#### Scenario: Guardrail retry counter

- GIVEN task with `guardrail_max_retries=3` fails validation
- WHEN guardrail retries
- THEN task shows "retrying (attempt 1/3)"; each attempt logged in error panel

#### Scenario: Internal retry suppressed

- GIVEN tool call fails, CrewAI retries internally
- WHEN internal retry occurs
- THEN panel SHALL NOT show retry card/counter; only final outcome renders
