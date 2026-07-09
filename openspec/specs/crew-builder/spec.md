# crew-builder Specification

## Purpose

Configuration forms for all CrewAI entities — crews, agents, tasks, tools, memory, knowledge, LLMs,
flows, variables, and output. Supports two modes: **advanced** (full 80+ parameter access) and
**guided wizard** (step-by-step for non-technical users).

## Requirements

### Requirement: Crew Configuration Form

The system MUST provide a form to configure a CrewAI crew with all parameters: name, description,
process (sequential/hierarchical), memory, planning, verbose, cache, max_rpm, stream,
manager_llm/agent, knowledge_sources, embedder, output_log_file, step_callback, task_callback,
before_kickoff, after_kickoff, and planning_llm.

#### Scenario: Configure a sequential crew in advanced mode

- GIVEN the user is on the crew configuration form in advanced mode
- WHEN the user fills all required fields (`name`, `process=sequential`) and clicks "Save"
- THEN the crew config is validated and stored in `app.storage.user`
- AND all optional fields retain their defaults when not explicitly set

#### Scenario: Switch to hierarchical process

- GIVEN a crew configured with `process=sequential`
- WHEN the user changes `process` to `hierarchical`
- THEN the `manager_llm` and `manager_agent` fields SHALL appear as required
- AND the user MUST fill at least one of them before saving

### Requirement: Agent Configuration Form

The system MUST provide a form for agent configuration covering: role, goal, backstory (with
`{variable}` interpolation), llm, function_calling_llm, tools, memory, verbose,
allow_delegation, allow_code_execution, max_iter, max_rpm, max_execution_time, step_callback,
cache, system_template, response_template, knowledge_sources, embedder, and multimodal.

#### Scenario: Add an agent with tools

- GIVEN the user is editing a crew
- WHEN the user clicks "Add Agent", fills `role="Researcher"`, selects 2 tools from the catalog,
  and saves
- THEN the agent SHALL appear in the crew's agent list
- AND the selected tools SHALL be serialized correctly in the crew config

#### Scenario: Variable interpolation preview

- GIVEN the crew has `{topic}` and `{audience}` input variables defined
- WHEN the user types `Research {topic} for {audience}` in the agent goal field
- THEN the UI SHALL render a live preview showing the interpolated text with default values

### Requirement: Task Configuration Form

The system MUST provide a form for task configuration covering: description, expected_output, agent,
context (task dependency list), output_file, output_json, output_pydantic, human_input,
async_execution, guardrails, guardrail_max_retries, callback, tools, markdown, and create_directory.

#### Scenario: Define task with context dependencies

- GIVEN two tasks exist: "Research" and "Write Report"
- WHEN the user edits "Write Report" and adds "Research" to its `context` list
- THEN the task dependency graph SHALL update to show "Write Report" depends on "Research"
- AND "Write Report" cannot execute until "Research" completes

### Requirement: Guided Wizard Mode

The system MUST provide a step-by-step wizard that guides non-technical users through crew creation
with sensible defaults, hiding advanced parameters.

#### Scenario: Complete wizard flow

- GIVEN a first-time user opens the builder
- WHEN the user selects "Guided Wizard" mode
- THEN the UI SHALL present steps: (1) Choose template or blank, (2) Define goal, (3) Add agents
  with role/goal/backstory, (4) Define tasks, (5) Review & save
- AND each step SHALL show a progress indicator
- AND advanced parameters SHALL be hidden with sensible defaults applied

### Requirement: Tool Catalog and Custom Tools

The system MUST display the crewai-tools built-in catalog and SHALL allow users to define custom
BaseTool instances with name, description, and args_schema.

#### Scenario: Add a custom tool

- GIVEN the user is configuring an agent's tools
- WHEN the user selects "Custom Tool", enters a name, description, and Pydantic args_schema
- THEN the custom tool SHALL appear in the agent's tool list
- AND it SHALL serialize correctly for CrewAI kickoff

### Edge Cases and Error Handling

| Condition | Expected Behavior |
|---|---|
| User saves with missing required field | Validation error with field highlight |
| Agent references deleted LLM config | Warn on save; fallback to crew default LLM |
| Task context references itself | Validation error: "Task cannot depend on itself" |
| Cycle in task context dependencies | Validation error: "Circular dependency detected" |
| Very long backstory/system_template | Auto-truncate to CrewAI limit with warning |
| Unsupported custom tool arg type | Validation error listing supported types |

### Non-Functional Requirements

- Form state MUST survive page navigation within the app (session persistence)
- Field validation MUST happen client-side with server-side revalidation on save
- The builder SHALL load an existing crew config (JSONC/YAML) and populate all forms
- All forms MUST be keyboard-navigable

### Dependencies

- `crew-engine`: serialization/deserialization of CrewAI configs
- `models.py`: Pydantic models for validation
