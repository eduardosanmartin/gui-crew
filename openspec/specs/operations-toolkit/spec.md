# operations-toolkit Specification

## Purpose

Operational tools for testing, reusing, and managing CrewAI configurations. Includes: individual
agent playground, pre-built crew templates, execution history with comparison, import/export to
CrewAI-native formats, and single-task testing (n8n-style node testing).

## Requirements

### Requirement: Agent Playground

The system MUST provide an interactive playground where users select a single agent, provide a
prompt, and execute it in isolation — without running the full crew. The user SHALL be able to
iterate on the agent's prompt, tools, and LLM configuration and re-execute in real time.

#### Scenario: Test an agent in isolation

- GIVEN a crew has an agent "Researcher" with tools [SerperDevTool, ScrapeWebsiteTool]
- WHEN the user opens the Playground, selects "Researcher", enters "What is CrewAI?", and clicks
  "Run"
- THEN the agent SHALL execute with its configured tools and LLM
- AND the output SHALL stream in real-time (same as observability micro layer)
- AND the crew SHALL NOT be kicked off

#### Scenario: Iterate on agent prompt

- GIVEN the Playground just ran agent "Researcher" with a weak prompt
- WHEN the user edits the prompt to be more specific and clicks "Re-run"
- THEN the agent SHALL execute again with the updated prompt
- AND the previous run's output SHALL remain visible for comparison

### Requirement: Crew Templates

The system SHALL ship with pre-defined crew templates: Research Crew, Code Review Crew, Content
Writer Crew, Data Analysis Crew, and Customer Support Crew. Users MUST be able to load a template
as a starting point.

#### Scenario: Load a template crew

- GIVEN the user is on the template gallery
- WHEN the user selects "Research Crew" and clicks "Use Template"
- THEN a new crew SHALL be created with pre-configured agents and tasks
- AND the crew SHALL appear in the Builder ready for customization
- AND the template source SHALL be noted so users know it's a derivative

### Requirement: Execution History

The system MUST persist past crew executions with metadata: what ran, when, duration, output
summary, token usage, estimated cost, and final status. The user MUST be able to compare two past
executions side by side.

#### Scenario: Compare two executions

- GIVEN the same crew was executed twice with different input variables
- WHEN the user opens History, selects both runs, and clicks "Compare"
- THEN a side-by-side view SHALL show: inputs, outputs, duration, tokens, cost, and status for both runs
- AND differences SHALL be highlighted

#### Scenario: Filter execution history

- GIVEN history contains 50+ executions across multiple crews
- WHEN the user filters by crew name "Research Crew" and status "failed"
- THEN only failed runs of "Research Crew" SHALL be displayed

### Requirement: Import and Export

The system MUST export crew configurations to YAML and JSONC formats compatible with CrewAI
native CLI. The system MUST import existing `crew.jsonc` / `agents.yaml` files and visualize them
in the GUI.

#### Scenario: Export crew to JSONC

- GIVEN a crew is fully configured in the Builder
- WHEN the user clicks "Export" and selects "CrewAI JSONC"
- THEN a JSONC file SHALL be generated matching CrewAI's native `crew.jsonc` schema
- AND the file SHALL be downloaded to the user's machine

#### Scenario: Import existing crew YAML

- GIVEN the user has a valid `crew.yaml` file from a previous CrewAI project
- WHEN the user clicks "Import", selects the file, and confirms
- THEN the crew SHALL be loaded into the Builder with all agents, tasks, and configs populated
- AND the Canvas SHALL render the imported topology
- AND validation errors in the imported file SHALL be surfaced with line references

#### Scenario: Import round-trip

- GIVEN a crew is exported as JSONC and then re-imported
- WHEN the import completes
- THEN the imported crew SHALL be functionally identical to the original (all params preserved)

### Requirement: Single-Task Testing

The system MUST allow users to execute a single task with test input data without running the full
crew pipeline — equivalent to n8n's node testing feature.

#### Scenario: Test a single task

- GIVEN a crew with task "Summarize" that depends on task "Research"
- WHEN the user right-clicks "Summarize" in the Canvas and selects "Test Task", providing mock
  input data for the context
- THEN only "Summarize" SHALL execute with the provided mock context
- AND the result SHALL display in a test output panel
- AND the full crew SHALL NOT be kicked off

### Edge Cases and Error Handling

| Condition | Expected Behavior |
|---|---|
| Import file has invalid YAML/JSON syntax | Parse error with line number |
| Import file references unknown tool | Warning: "Tool X not available in current environment" |
| Playground agent has no LLM configured | Error: "Agent requires an LLM to execute" |
| Single-task test with missing context data | Warning: "Mock context is empty; task may fail" |
| History storage exceeds local disk quota | Auto-prune oldest runs; warn user |
| Template references deprecated CrewAI features | Warn on load; auto-migrate to current API |

### Non-Functional Requirements

- History SHALL persist across browser sessions (local file storage)
- Import/export SHALL support files up to 5MB
- Template gallery SHALL be extensible (users can save custom templates)
- Playground SHALL respect the agent's `max_execution_time` and `max_iter` limits

### Dependencies

- `crew-builder`: templates load into Builder; export serializes Builder state
- `crew-engine`: playground and single-task execution use the engine
- `crew-observability`: playground streaming reuses micro-layer components
- `canvas-editor`: single-task testing triggered from Canvas context menu
