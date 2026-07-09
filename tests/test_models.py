"""Unit tests for gui-crew Pydantic models and validators."""

from __future__ import annotations

import json
import warnings

import pytest
from pydantic import ValidationError

from models import (
    AgentModel,
    CrewModel,
    InputVar,
    LLMModel,
    MemoryConfig,
    RunRecord,
    TaskModel,
    TokenUsage,
    ToolRef,
)


# ═══════════════════════════════════════════════
#  LLMModel
# ═══════════════════════════════════════════════

class TestLLMModel:
    def test_default_model(self):
        llm = LLMModel()
        assert llm.model == "openai/gpt-4o"
        assert llm.temperature is None
        assert llm.base_url is None
        assert llm.api_key_env is None

    def test_custom_model(self):
        llm = LLMModel(model="anthropic/claude-3-5-sonnet", temperature=0.7)
        assert llm.model == "anthropic/claude-3-5-sonnet"
        assert llm.temperature == 0.7

    def test_extra_fields_via_model_extra(self):
        """Forward-compat: unknown fields go to model_extra."""
        llm = LLMModel(model="gpt-4o", top_p=0.9, max_tokens=2000)
        assert llm.model == "gpt-4o"
        assert llm.model_extra == {"top_p": 0.9, "max_tokens": 2000}

    def test_serialize_to_json(self):
        llm = LLMModel(model="gpt-4o", temperature=0.5)
        data = llm.model_dump()
        assert data["model"] == "gpt-4o"
        assert data["temperature"] == 0.5


# ═══════════════════════════════════════════════
#  MemoryConfig
# ═══════════════════════════════════════════════

class TestMemoryConfig:
    def test_defaults(self):
        mem = MemoryConfig()
        assert mem.enabled is False
        assert mem.embedder is None

    def test_with_embedder(self):
        mem = MemoryConfig(
            enabled=True,
            embedder={"provider": "openai", "model": "text-embedding-3-small"},
        )
        assert mem.enabled is True
        assert mem.embedder["model"] == "text-embedding-3-small"

    def test_extra_fields_allowed(self):
        mem = MemoryConfig(enabled=True, custom_backend="lancedb")
        assert mem.enabled is True


# ═══════════════════════════════════════════════
#  ToolRef
# ═══════════════════════════════════════════════

class TestToolRef:
    def test_builtin_tool(self):
        tool = ToolRef(kind="builtin", name="SerperDevTool")
        assert tool.kind == "builtin"
        assert tool.params == {}
        assert tool.args_schema is None

    def test_builtin_tool_with_params(self):
        tool = ToolRef(
            kind="builtin",
            name="FileReadTool",
            params={"base_path": "/tmp"},
        )
        assert tool.params == {"base_path": "/tmp"}

    def test_custom_tool_with_args_schema(self):
        tool = ToolRef(
            kind="custom",
            name="WeatherTool",
            args_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        )
        assert tool.kind == "custom"
        assert tool.args_schema["properties"]["city"]["type"] == "string"

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            ToolRef(kind="unknown", name="Bad")  # type: ignore[arg-type]

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            ToolRef(kind="builtin", name="  ")
        with pytest.raises(ValidationError):
            ToolRef(kind="builtin", name="")


# ═══════════════════════════════════════════════
#  InputVar
# ═══════════════════════════════════════════════

class TestInputVar:
    def test_minimal(self):
        v = InputVar(name="topic")
        assert v.name == "topic"
        assert v.type == "str"
        assert v.description is None
        assert v.default is None

    def test_full(self):
        v = InputVar(
            name="audience",
            type="str",
            description="Target audience",
            default="developers",
        )
        assert v.default == "developers"

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            InputVar(name="   ")
        with pytest.raises(ValidationError):
            InputVar(name="")


# ═══════════════════════════════════════════════
#  AgentModel
# ═══════════════════════════════════════════════

class TestAgentModel:
    def test_minimal_valid(self):
        agent = AgentModel(role="Researcher", goal="Research the topic")
        assert agent.role == "Researcher"
        assert agent.backstory == ""

    def test_missing_role_fails(self):
        with pytest.raises(ValidationError):
            AgentModel(goal="Do stuff")  # type: ignore[arg-type]

    def test_missing_goal_fails(self):
        with pytest.raises(ValidationError):
            AgentModel(role="Writer")  # type: ignore[arg-type]

    def test_empty_role_fails(self):
        with pytest.raises(ValidationError):
            AgentModel(role="  ", goal="Write")

    def test_with_tools(self):
        agent = AgentModel(
            role="Researcher",
            goal="Find info",
            tools=[
                ToolRef(kind="builtin", name="SerperDevTool"),
                ToolRef(kind="builtin", name="FileReadTool"),
            ],
        )
        assert len(agent.tools) == 2
        assert agent.tools[0].name == "SerperDevTool"

    def test_with_llm(self):
        agent = AgentModel(
            role="Analyst",
            goal="Analyze data",
            llm=LLMModel(model="anthropic/claude-3-5-sonnet", temperature=0.3),
        )
        assert agent.llm is not None
        assert agent.llm.model == "anthropic/claude-3-5-sonnet"

    def test_with_memory_config(self):
        agent = AgentModel(
            role="Rememberer",
            goal="Remember things",
            memory=MemoryConfig(enabled=True),
        )
        assert isinstance(agent.memory, MemoryConfig)
        assert agent.memory.enabled is True

    def test_memory_bool_false(self):
        agent = AgentModel(role="X", goal="Y", memory=False)
        assert agent.memory is False

    def test_memory_bool_true(self):
        agent = AgentModel(role="X", goal="Y", memory=True)
        assert agent.memory is True

    def test_negative_max_iter_rejected(self):
        with pytest.raises(ValidationError):
            AgentModel(role="R", goal="G", max_iter=-1)


# ═══════════════════════════════════════════════
#  TaskModel
# ═══════════════════════════════════════════════

class TestTaskModel:
    def test_minimal_valid(self):
        task = TaskModel(
            name="research",
            description="Research the topic",
            expected_output="A report",
        )
        assert task.name == "research"
        assert task.context == []

    def test_missing_name_fails(self):
        with pytest.raises(ValidationError):
            TaskModel(description="D", expected_output="E")  # type: ignore[arg-type]

    def test_empty_name_fails(self):
        with pytest.raises(ValidationError):
            TaskModel(name="  ", description="D", expected_output="E")

    def test_with_context_dependencies(self):
        task = TaskModel(
            name="write",
            description="Write the report",
            expected_output="Report",
            context=["research", "analyze"],
        )
        assert task.context == ["research", "analyze"]

    def test_self_context_allowed_at_model_level(self):
        """Self-context is caught at CrewModel level, not TaskModel level."""
        task = TaskModel(
            name="self-ref",
            description="D",
            expected_output="E",
            context=["self-ref"],
        )
        assert task.context == ["self-ref"]

    def test_with_guardrails(self):
        task = TaskModel(
            name="safe_task",
            description="Do something safe",
            expected_output="Result",
            guardrails=["no_pii", "max_length"],
            guardrail_max_retries=5,
        )
        assert task.guardrails == ["no_pii", "max_length"]
        assert task.guardrail_max_retries == 5

    def test_output_file_relative_path_allowed(self):
        task = TaskModel(
            name="t",
            description="d",
            expected_output="e",
            output_file="reports/output.md",
        )
        assert task.output_file == "reports/output.md"

    def test_output_file_absolute_path_rejected(self):
        with pytest.raises(ValidationError, match="relative path"):
            TaskModel(
                name="t",
                description="d",
                expected_output="e",
                output_file="/etc/passwd",
            )

    def test_output_file_parent_dir_traversal_rejected(self):
        with pytest.raises(ValidationError, match="parent-dir"):
            TaskModel(
                name="t",
                description="d",
                expected_output="e",
                output_file="../../etc/passwd",
            )

    def test_output_file_none_allowed(self):
        task = TaskModel(
            name="t",
            description="d",
            expected_output="e",
            output_file=None,
        )
        assert task.output_file is None

    def test_context_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            TaskModel(
                name="t",
                description="d",
                expected_output="e",
                context=["valid", ""],
            )

    def test_context_whitespace_only_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            TaskModel(
                name="t",
                description="d",
                expected_output="e",
                context=["valid", "   "],
            )

    def test_context_empty_list_allowed(self):
        task = TaskModel(
            name="t",
            description="d",
            expected_output="e",
            context=[],
        )
        assert task.context == []

    def test_negative_guardrail_max_retries_rejected(self):
        with pytest.raises(ValidationError):
            TaskModel(
                name="t",
                description="d",
                expected_output="e",
                guardrail_max_retries=-1,
            )

    def test_zero_guardrail_max_retries_allowed(self):
        task = TaskModel(
            name="t",
            description="d",
            expected_output="e",
            guardrail_max_retries=0,
        )
        assert task.guardrail_max_retries == 0


# ═══════════════════════════════════════════════
#  CrewModel — validators
# ═══════════════════════════════════════════════

class TestCrewModelBasic:
    def test_minimal_valid(self):
        crew = CrewModel(name="MyCrew")
        assert crew.name == "MyCrew"
        assert crew.process == "sequential"
        assert crew.tasks == []
        assert crew.agents == []

    def test_empty_name_fails(self):
        with pytest.raises(ValidationError):
            CrewModel(name="   ")

    def test_verbose_defaults_to_false(self):
        crew = CrewModel(name="C")
        assert crew.verbose is False

    def test_verbose_true(self):
        crew = CrewModel(name="C", verbose=True)
        assert crew.verbose is True


class TestCrewModelHierarchical:
    """Hierarchical process MUST have manager_llm or manager_agent_role."""

    def test_fails_without_manager(self):
        with pytest.raises(ValidationError, match="Hierarchical"):
            CrewModel(
                name="HC",
                process="hierarchical",
            )

    def test_passes_with_manager_llm(self):
        crew = CrewModel(
            name="HC",
            process="hierarchical",
            manager_llm=LLMModel(model="gpt-4o"),
        )
        assert crew.process == "hierarchical"

    def test_passes_with_manager_agent_role(self):
        crew = CrewModel(
            name="HC",
            process="hierarchical",
            manager_agent_role="Project Manager",
        )
        assert crew.manager_agent_role == "Project Manager"

    def test_passes_with_both(self):
        crew = CrewModel(
            name="HC",
            process="hierarchical",
            manager_llm=LLMModel(model="gpt-4o"),
            manager_agent_role="PM",
        )
        assert crew.manager_llm is not None
        assert crew.manager_agent_role == "PM"

    def test_manager_agent_role_whitespace_rejected(self):
        with pytest.raises(ValidationError, match="manager_agent_role"):
            CrewModel(
                name="HC",
                process="hierarchical",
                manager_agent_role="   ",
            )

    def test_manager_agent_role_stripped(self):
        crew = CrewModel(
            name="HC",
            process="hierarchical",
            manager_agent_role="  PM  ",
        )
        assert crew.manager_agent_role == "PM"


class TestCrewModelTaskSelfContext:
    """A task cannot list itself in its own context."""

    def test_self_context_fails(self):
        with pytest.raises(ValidationError, match="cannot depend on itself"):
            CrewModel(
                name="C",
                tasks=[
                    TaskModel(
                        name="A",
                        description="Task A",
                        expected_output="Out",
                        context=["A"],  # self-reference!
                    ),
                ],
            )

    def test_non_self_context_passes(self):
        crew = CrewModel(
            name="C",
            tasks=[
                TaskModel(
                    name="A",
                    description="Task A",
                    expected_output="Out",
                ),
                TaskModel(
                    name="B",
                    description="Task B",
                    expected_output="Out",
                    context=["A"],  # B depends on A — fine
                ),
            ],
        )
        assert len(crew.tasks) == 2


class TestCrewModelCycleDetection:
    """Kahn's-algorithm cycle detection in task context DAG."""

    def test_simple_chain_passes(self):
        crew = CrewModel(
            name="Chain",
            tasks=[
                TaskModel(name="A", description="A", expected_output="A"),
                TaskModel(
                    name="B",
                    description="B",
                    expected_output="B",
                    context=["A"],
                ),
                TaskModel(
                    name="C",
                    description="C",
                    expected_output="C",
                    context=["B"],
                ),
            ],
        )
        assert len(crew.tasks) == 3

    def test_diamond_dag_passes(self):
        """A → B, A → C, B → D, C → D — valid DAG."""
        crew = CrewModel(
            name="Diamond",
            tasks=[
                TaskModel(name="A", description="A", expected_output="A"),
                TaskModel(
                    name="B",
                    description="B",
                    expected_output="B",
                    context=["A"],
                ),
                TaskModel(
                    name="C",
                    description="C",
                    expected_output="C",
                    context=["A"],
                ),
                TaskModel(
                    name="D",
                    description="D",
                    expected_output="D",
                    context=["B", "C"],
                ),
            ],
        )
        assert len(crew.tasks) == 4

    def test_simple_cycle_fails(self):
        """A → B → C → A is a cycle."""
        with pytest.raises(ValidationError, match="Circular dependency"):
            CrewModel(
                name="Cycle",
                tasks=[
                    TaskModel(
                        name="A",
                        description="A",
                        expected_output="A",
                        context=["C"],
                    ),
                    TaskModel(
                        name="B",
                        description="B",
                        expected_output="B",
                        context=["A"],
                    ),
                    TaskModel(
                        name="C",
                        description="C",
                        expected_output="C",
                        context=["B"],
                    ),
                ],
            )

    def test_cycle_error_message_shows_path(self):
        """The error message should contain the cycle path."""
        with pytest.raises(ValidationError) as exc_info:
            CrewModel(
                name="Cycle",
                tasks=[
                    TaskModel(
                        name="Research",
                        description="R",
                        expected_output="O",
                        context=["Write"],
                    ),
                    TaskModel(
                        name="Analyze",
                        description="A",
                        expected_output="O",
                        context=["Research"],
                    ),
                    TaskModel(
                        name="Write",
                        description="W",
                        expected_output="O",
                        context=["Analyze"],
                    ),
                ],
            )
        msg = str(exc_info.value)
        # The cycle path should mention at least two of the task names
        assert "Research" in msg or "Analyze" in msg or "Write" in msg

    def test_self_loop_is_caught_by_self_context_first(self):
        """A → A is caught by self-context before cycle detection."""
        with pytest.raises(ValidationError, match="cannot depend on itself"):
            CrewModel(
                name="SelfLoop",
                tasks=[
                    TaskModel(
                        name="A",
                        description="A",
                        expected_output="A",
                        context=["A"],
                    ),
                ],
            )


# ═══════════════════════════════════════════════
#  CrewModel — agent_role validation
# ═══════════════════════════════════════════════

class TestCrewModelAgentRoleValidation:
    def test_valid_agent_role_passes(self):
        crew = CrewModel(
            name="C",
            agents=[AgentModel(role="Researcher", goal="G")],
            tasks=[
                TaskModel(
                    name="research",
                    description="Do research",
                    expected_output="Report",
                    agent_role="Researcher",
                ),
            ],
        )
        assert crew.tasks[0].agent_role == "Researcher"

    def test_invalid_agent_role_fails(self):
        with pytest.raises(ValidationError, match="agent_role"):
            CrewModel(
                name="C",
                agents=[AgentModel(role="Researcher", goal="G")],
                tasks=[
                    TaskModel(
                        name="research",
                        description="Do research",
                        expected_output="Report",
                        agent_role="NonExistentRole",
                    ),
                ],
            )

    def test_none_agent_role_allowed(self):
        """Tasks without an explicit agent_role are valid."""
        crew = CrewModel(
            name="C",
            agents=[AgentModel(role="Researcher", goal="G")],
            tasks=[
                TaskModel(
                    name="research",
                    description="Do research",
                    expected_output="Report",
                    agent_role=None,
                ),
            ],
        )
        assert crew.tasks[0].agent_role is None

    def test_no_agents_no_tasks_passes(self):
        crew = CrewModel(name="C")
        assert len(crew.agents) == 0
        assert len(crew.tasks) == 0


# ═══════════════════════════════════════════════
#  CrewModel — variable-reference warnings
# ═══════════════════════════════════════════════

class TestCrewModelVariableRefs:
    def test_no_warning_when_input_exists(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                inputs=[InputVar(name="topic", default="AI")],
                agents=[
                    AgentModel(
                        role="R",
                        goal="Research {topic}",
                    )
                ],
            )
        assert len(w) == 0

    def test_warning_when_input_missing(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                agents=[
                    AgentModel(
                        role="R",
                        goal="Research {missing_var}",
                    )
                ],
            )
        assert len(w) >= 1
        warning_text = str(w[0].message)
        assert "{missing_var}" in warning_text

    def test_warning_for_multiple_missing(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                agents=[
                    AgentModel(
                        role="R",
                        goal="Research {x} and {y}",
                    )
                ],
            )
        assert len(w) >= 2

    def test_warning_in_task_description(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                tasks=[
                    TaskModel(
                        name="T",
                        description="Do {missing}",
                        expected_output="Out",
                    )
                ],
            )
        assert len(w) >= 1
        assert "{missing}" in str(w[0].message)

    def test_no_variables_no_warnings(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                agents=[AgentModel(role="R", goal="Plain goal")],
                tasks=[
                    TaskModel(
                        name="T",
                        description="Plain description",
                        expected_output="Plain output",
                    )
                ],
            )
        assert len(w) == 0

    def test_escaped_braces_do_not_trigger_warning(self):
        """Double braces {{topic}} should not match as a variable reference."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CrewModel(
                name="C",
                agents=[
                    AgentModel(
                        role="R",
                        goal="Use {{topic}} in template",
                    )
                ],
            )
        # Escaped braces should not produce variable warnings
        ref_warnings = [x for x in w if "variable" in str(x.message).lower()]
        assert len(ref_warnings) == 0


# ═══════════════════════════════════════════════
#  CrewModel — serialization round-trip
# ═══════════════════════════════════════════════

def _make_sample_crew() -> CrewModel:
    """Build a non-trivial crew for round-trip tests."""
    return CrewModel(
        schema_version=1,
        name="Sample Crew",
        description="A sample crew for testing",
        process="sequential",
        agents=[
            AgentModel(
                role="Researcher",
                goal="Research {topic}",
                backstory="Expert researcher",
                llm=LLMModel(model="openai/gpt-4o", temperature=0.3),
                tools=[
                    ToolRef(kind="builtin", name="SerperDevTool"),
                ],
                allow_delegation=True,
                max_iter=5,
            ),
            AgentModel(
                role="Writer",
                goal="Write about {topic}",
                backstory="Skilled writer",
                allow_code_execution=True,
            ),
        ],
        tasks=[
            TaskModel(
                name="research_task",
                description="Research {topic} thoroughly",
                expected_output="Research findings document",
                agent_role="Researcher",
                guardrails=["no_pii"],
            ),
            TaskModel(
                name="write_task",
                description="Write a report based on research",
                expected_output="Final report",
                agent_role="Writer",
                context=["research_task"],
                output_file="report.md",
                markdown=True,
            ),
        ],
        memory=MemoryConfig(enabled=True, embedder={"provider": "openai"}),
        planning=True,
        embedder={"provider": "openai", "model": "text-embedding-3-small"},
        inputs=[
            InputVar(name="topic", type="str", description="Research topic"),
        ],
    )


class TestCrewModelSerialization:
    """JSON / YAML round-trip tests."""

    def test_json_round_trip_preserves_fields(self):
        original = _make_sample_crew()
        json_str = original.to_crewai_json()
        restored = CrewModel.from_crewai_json(json_str)

        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.process == original.process
        assert len(restored.agents) == len(original.agents)
        assert len(restored.tasks) == len(original.tasks)
        assert restored.schema_version == original.schema_version

    def test_json_round_trip_agent_fields(self):
        original = _make_sample_crew()
        restored = CrewModel.from_crewai_json(original.to_crewai_json())

        r_agent = restored.agents[0]
        assert r_agent.role == "Researcher"
        assert r_agent.goal == "Research {topic}"
        assert r_agent.llm is not None
        assert r_agent.llm.model == "openai/gpt-4o"
        assert len(r_agent.tools) == 1
        assert r_agent.tools[0].name == "SerperDevTool"
        assert r_agent.allow_delegation is True
        assert r_agent.max_iter == 5

    def test_json_round_trip_task_fields(self):
        original = _make_sample_crew()
        restored = CrewModel.from_crewai_json(original.to_crewai_json())

        r_task = restored.tasks[1]
        assert r_task.name == "write_task"
        assert r_task.context == ["research_task"]
        assert r_task.output_file == "report.md"
        assert r_task.markdown is True

    def test_json_round_trip_crew_level_fields(self):
        original = _make_sample_crew()
        restored = CrewModel.from_crewai_json(original.to_crewai_json())

        assert isinstance(restored.memory, MemoryConfig)
        assert restored.memory.enabled is True
        assert restored.planning is True
        assert restored.embedder is not None
        assert restored.embedder["model"] == "text-embedding-3-small"
        assert len(restored.inputs) == 1
        assert restored.inputs[0].name == "topic"

    def test_verbose_round_trip(self):
        crew = CrewModel(
            name="C",
            verbose=True,
            agents=[AgentModel(role="R", goal="G")],
            tasks=[TaskModel(name="T", description="D", expected_output="E")],
        )
        restored = CrewModel.from_crewai_json(crew.to_crewai_json())
        assert restored.verbose is True

    def test_yaml_round_trip_preserves_fields(self):
        original = _make_sample_crew()
        yaml_str = original.to_crewai_yaml()
        restored = CrewModel.from_crewai_yaml(yaml_str)

        assert restored.name == original.name
        assert len(restored.agents) == len(original.agents)
        assert len(restored.tasks) == len(original.tasks)
        assert restored.schema_version == original.schema_version

    def test_yaml_round_trip_agent_detail(self):
        original = _make_sample_crew()
        restored = CrewModel.from_crewai_yaml(original.to_crewai_yaml())

        r_agent = restored.agents[0]
        assert r_agent.role == "Researcher"
        assert r_agent.llm.model == "openai/gpt-4o"
        assert r_agent.llm.temperature == 0.3

    def test_json_output_is_valid_json(self):
        original = _make_sample_crew()
        json_str = original.to_crewai_json()
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed["name"] == "Sample Crew"

    def test_schema_version_preserved(self):
        original = CrewModel(
            name="V",
            schema_version=5,
            agents=[AgentModel(role="R", goal="G")],
            tasks=[TaskModel(name="T", description="D", expected_output="E")],
        )
        json_str = original.to_crewai_json()
        restored = CrewModel.from_crewai_json(json_str)
        assert restored.schema_version == 5

    def test_from_crewai_dict_handles_agent_key(self):
        """``manager_agent`` in the dict maps to ``manager_agent_role``."""
        crew = CrewModel._from_crewai_dict({
            "name": "Test",
            "manager_agent": "PM",
            "process": "hierarchical",
        })
        assert crew.manager_agent_role == "PM"

    def test_minimal_crew_round_trips(self):
        original = CrewModel(
            name="Minimal",
            agents=[AgentModel(role="R", goal="G")],
            tasks=[TaskModel(name="T", description="D", expected_output="E")],
        )
        restored = CrewModel.from_crewai_json(original.to_crewai_json())
        assert restored.name == "Minimal"
        assert len(restored.agents) == 1
        assert len(restored.tasks) == 1

    # -- Serialization format tests (CRITICAL 1 & 2) -----------------------

    def test_agents_serialized_as_role_names(self):
        """External format: agents should be a list of role name strings."""
        crew = _make_sample_crew()
        json_str = crew.to_crewai_json()
        parsed = json.loads(json_str)
        assert parsed["agents"] == ["Researcher", "Writer"]

    def test_agent_config_separate_structure(self):
        """External format: agent details in agent_config dict keyed by role."""
        crew = _make_sample_crew()
        json_str = crew.to_crewai_json()
        parsed = json.loads(json_str)
        assert "agent_config" in parsed
        assert parsed["agent_config"]["Researcher"]["role"] == "Researcher"
        assert parsed["agent_config"]["Writer"]["role"] == "Writer"
        assert parsed["agent_config"]["Researcher"]["goal"] == "Research {topic}"

    def test_inputs_serialized_as_dict(self):
        """External format: inputs should be a dict of {name: default}."""
        crew = _make_sample_crew()
        json_str = crew.to_crewai_json()
        parsed = json.loads(json_str)
        assert isinstance(parsed["inputs"], dict)
        assert "topic" in parsed["inputs"]
        assert parsed["inputs"]["topic"] is None  # no default in sample

    def test_inputs_with_default_serialized_as_dict_value(self):
        crew = CrewModel(
            name="C",
            inputs=[InputVar(name="topic", default="AI")],
        )
        json_str = crew.to_crewai_json()
        parsed = json.loads(json_str)
        assert parsed["inputs"] == {"topic": "AI"}

    def test_extra_fields_via_model_extra_serialized(self):
        """model_extra fields should appear at top level in JSON output."""
        crew = CrewModel(
            name="C",
            agents=[AgentModel(role="R", goal="G")],
            tasks=[TaskModel(name="T", description="D", expected_output="E")],
            full_output=True,  # unknown field → goes to model_extra
        )
        json_str = crew.to_crewai_json()
        parsed = json.loads(json_str)
        assert parsed.get("full_output") is True


# ═══════════════════════════════════════════════
#  TokenUsage
# ═══════════════════════════════════════════════

class TestTokenUsage:
    def test_defaults(self):
        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0
        assert tu.total_tokens == 0

    def test_with_values(self):
        tu = TokenUsage(input_tokens=100, output_tokens=250, total_tokens=350)
        assert tu.input_tokens == 100
        assert tu.output_tokens == 250
        assert tu.total_tokens == 350


# ═══════════════════════════════════════════════
#  RunRecord
# ═══════════════════════════════════════════════

class TestRunRecord:
    def test_create_success_record(self):
        snapshot = {"name": "TestCrew"}
        record = RunRecord(
            crew_name="TestCrew",
            crew_snapshot=snapshot,
            duration_ms=5000,
            token_usage=TokenUsage(input_tokens=50, output_tokens=100, total_tokens=150),
            cost=0.004,
            status="success",
        )
        assert record.crew_name == "TestCrew"
        assert record.duration_ms == 5000
        assert record.status == "success"
        assert record.cost == 0.004

    def test_create_failed_record(self):
        record = RunRecord(
            crew_name="CrashCrew",
            crew_snapshot={},
            status="failed",
            error="Division by zero",
        )
        assert record.status == "failed"
        assert record.error == "Division by zero"

    def test_default_status_is_running(self):
        record = RunRecord(crew_name="C", crew_snapshot={})
        assert record.status == "running"

    def test_serialize_run_record(self):
        snapshot = {"name": "MyCrew", "process": "sequential"}
        record = RunRecord(
            crew_name="MyCrew",
            crew_snapshot=snapshot,
            token_usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            status="success",
        )
        data = record.model_dump()
        assert data["crew_name"] == "MyCrew"
        assert data["crew_snapshot"] == snapshot
        assert data["token_usage"]["total_tokens"] == 30
        assert "timestamp" in data

    def test_extra_fields_allowed(self):
        record = RunRecord(
            crew_name="C",
            crew_snapshot={},
            custom_meta="extra-info",
        )
        data = record.model_dump()
        assert data["custom_meta"] == "extra-info"


# ═══════════════════════════════════════════════
#  Edge Cases
# ═══════════════════════════════════════════════

class TestEdgeCases:
    def test_crew_with_no_tasks_is_valid(self):
        crew = CrewModel(name="Empty")
        assert crew.tasks == []

    def test_crew_with_no_agents_is_valid(self):
        crew = CrewModel(name="NoAgents")
        assert crew.agents == []

    def test_context_referencing_missing_task_name_allowed(self):
        """Unknown context names are allowed (could be external refs)."""
        crew = CrewModel(
            name="C",
            tasks=[
                TaskModel(
                    name="A",
                    description="A",
                    expected_output="A",
                    context=["nonexistent"],
                ),
            ],
        )
        assert crew.tasks[0].context == ["nonexistent"]

    def test_duplicate_task_names_accepted(self):
        """We don't enforce unique task names at the model level."""
        crew = CrewModel(
            name="C",
            tasks=[
                TaskModel(name="A", description="A", expected_output="A"),
                TaskModel(name="A", description="B", expected_output="B"),
            ],
        )
        assert len(crew.tasks) == 2

    def test_multiple_agents_with_tools(self):
        crew = CrewModel(
            name="MultiAgent",
            agents=[
                AgentModel(
                    role="R",
                    goal="G",
                    tools=[
                        ToolRef(kind="builtin", name="T1"),
                        ToolRef(kind="builtin", name="T2"),
                    ],
                ),
                AgentModel(
                    role="W",
                    goal="G2",
                    tools=[ToolRef(kind="custom", name="MyTool")],
                ),
            ],
        )
        assert len(crew.agents[0].tools) == 2
        assert len(crew.agents[1].tools) == 1


# ═══════════════════════════════════════════════
#  Deferred Issues (Now Fixed)
# ═══════════════════════════════════════════════


class TestDeferredIssuesFixed:
    """Tests for issues that were deferred in initial review and now fixed."""

    def test_output_json_schema_serialization(self):
        """output_json_schema is serialized as full JSON Schema dict."""
        task = TaskModel(
            name="Analyze",
            description="Analyze data",
            expected_output="Structured result",
            output_json_schema={
                "type": "object",
                "properties": {
                    "sentiment": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["sentiment"],
            },
        )
        crew = CrewModel(name="C", tasks=[task])
        json_str = crew.to_crewai_json()
        data = json.loads(json_str)

        # Should be serialized as full schema dict
        assert "tasks" in data
        assert len(data["tasks"]) == 1
        assert "output_json_schema" in data["tasks"][0]
        assert data["tasks"][0]["output_json_schema"]["type"] == "object"
        assert "sentiment" in data["tasks"][0]["output_json_schema"]["properties"]

    def test_output_json_schema_round_trip(self):
        """output_json_schema survives JSON round-trip."""
        schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        }
        task = TaskModel(
            name="T",
            description="D",
            expected_output="E",
            output_json_schema=schema,
        )
        crew = CrewModel(name="C", tasks=[task])
        restored = CrewModel.from_crewai_json(crew.to_crewai_json())

        assert restored.tasks[0].output_json_schema == schema

    def test_tool_ref_exclude_defaults(self):
        """ToolRef serialization uses exclude_defaults=True for consistency."""
        tool = ToolRef(kind="builtin", name="search")
        crew = CrewModel(
            name="C",
            agents=[AgentModel(role="R", goal="G", tools=[tool])],
        )
        json_str = crew.to_crewai_json()
        data = json.loads(json_str)

        # params and args_schema should NOT appear (they are defaults)
        tool_data = data["agent_config"]["R"]["tools"][0]
        assert "params" not in tool_data
        assert "args_schema" not in tool_data
        assert tool_data["kind"] == "builtin"
        assert tool_data["name"] == "search"

    def test_tool_ref_with_params_serialized(self):
        """ToolRef with non-default params includes them."""
        tool = ToolRef(kind="builtin", name="search", params={"query": "test"})
        crew = CrewModel(
            name="C",
            agents=[AgentModel(role="R", goal="G", tools=[tool])],
        )
        json_str = crew.to_crewai_json()
        data = json.loads(json_str)

        tool_data = data["agent_config"]["R"]["tools"][0]
        assert tool_data["params"] == {"query": "test"}

    def test_runrecord_success_cannot_have_error(self):
        """RunRecord with status='success' cannot have error message."""
        with pytest.raises(ValueError, match="Success status cannot have error"):
            RunRecord(
                crew_name="Test",
                crew_snapshot={},
                status="success",
                error="Something went wrong",
            )

    def test_runrecord_failed_must_have_error(self):
        """RunRecord with status='failed' must have error message."""
        with pytest.raises(ValueError, match="Failed status must have error"):
            RunRecord(
                crew_name="Test",
                crew_snapshot={},
                status="failed",
                error=None,
            )

    def test_runrecord_success_without_error_valid(self):
        """RunRecord with status='success' and no error is valid."""
        record = RunRecord(
            crew_name="Test",
            crew_snapshot={},
            status="success",
            error=None,
        )
        assert record.status == "success"
        assert record.error is None

    def test_runrecord_failed_with_error_valid(self):
        """RunRecord with status='failed' and error is valid."""
        record = RunRecord(
            crew_name="Test",
            crew_snapshot={},
            status="failed",
            error="Connection timeout",
        )
        assert record.status == "failed"
        assert record.error == "Connection timeout"

    def test_runrecord_cancelled_without_error_valid(self):
        """RunRecord with status='cancelled' and no error is valid."""
        record = RunRecord(
            crew_name="Test",
            crew_snapshot={},
            status="cancelled",
            error=None,
        )
        assert record.status == "cancelled"

    def test_runrecord_running_without_error_valid(self):
        """RunRecord with status='running' and no error is valid."""
        record = RunRecord(
            crew_name="Test",
            crew_snapshot={},
            status="running",
            error=None,
        )
        assert record.status == "running"
