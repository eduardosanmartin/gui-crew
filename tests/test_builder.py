"""Tests for gui-crew builder.py - state management, validation, and form logic.

Tests are organised into:
- State helpers (``_crew_model``, ``_persist``) - unit-tested with mocked storage.
- Crew form validation (required fields, conditional fields).
- Agent form validation (role, goal, tools, LLM, delegation).
- Task form validation (name, description, output_file, DAG, guardrails).
- Tool catalogue (search, filter, uniqueness).
- Integration smoke tests - round-trip persistence.

Storage-dependent tests mock ``app.storage`` via ``PropertyMock`` on
``Storage.user`` (same pattern as ``test_app.py``) to avoid requiring
a full NiceGUI server context.
"""

from __future__ import annotations

import warnings
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from nicegui import app
from nicegui.storage import Storage

import builder
from builder import BUILTIN_TOOLS

import models as m


# ============================================================================
#  Shared helpers
# ============================================================================

class _FakeUser(dict):
    """A dict subclass that behaves like ``app.storage.user``."""


def _make_user(initial: dict[str, Any] | None = None) -> _FakeUser:
    return _FakeUser(initial) if initial else _FakeUser()


# ============================================================================
#  State Helpers
# ============================================================================

class TestCrewModelState:
    """``_crew_model`` creates / loads correctly from storage."""

    def test_returns_default_when_none(self) -> None:
        """When storage has no crew_model, a default is created."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
        assert isinstance(cm, m.CrewModel)
        assert cm.name == "New Crew"

    def test_returns_default_when_corrupt(self) -> None:
        """When storage has an invalid dict, a default is created."""
        user = _make_user({"crew_model": {"name": None}})
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
        assert isinstance(cm, m.CrewModel)
        assert cm.name == "New Crew"

    def test_loads_from_valid_dict(self) -> None:
        """When storage has a valid dict, it reconstructs CrewModel."""
        valid = {"name": "Research Crew", "process": "sequential"}
        user = _make_user({"crew_model": valid})
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
        assert cm.name == "Research Crew"
        assert cm.process == "sequential"

    def test_loads_from_instance(self) -> None:
        """When storage has an already-constructed CrewModel, it is returned as-is."""
        orig = m.CrewModel(name="Test Crew")
        user = _make_user({"crew_model": orig})
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
        assert cm is orig


class TestPersist:
    """``_persist`` validates and writes to storage."""

    def test_persists_valid_model(self) -> None:
        """A valid model is written to storage and returns 0."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(name="My Crew")
            result = builder._persist(cm)
        assert result == 0
        assert user["crew_model"]["name"] == "My Crew"

    def test_persist_with_agents_and_tasks(self) -> None:
        """A crew with agents and tasks persists correctly."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Full Crew",
                agents=[m.AgentModel(role="R", goal="Research")],
                tasks=[m.TaskModel(name="T", description="D", expected_output="E")],
            )
            result = builder._persist(cm)
        assert result == 0
        saved = user["crew_model"]
        assert saved["name"] == "Full Crew"
        assert len(saved["agents"]) == 1
        assert len(saved["tasks"]) == 1

    def test_persist_twice_idempotent(self) -> None:
        """Persist is idempotent — second save overwrites cleanly."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm1 = m.CrewModel(name="First")
            builder._persist(cm1)
            cm2 = m.CrewModel(name="Second")
            builder._persist(cm2)
        assert user["crew_model"]["name"] == "Second"


# ============================================================================
#  Crew Form Validation Scenarios
# ============================================================================

class TestCrewFormValidation:
    """Validation rules enforced at the form/model level."""

    def test_name_required(self) -> None:
        with pytest.raises(ValueError, match="Crew name must not be empty"):
            m.CrewModel(name="  ")

    def test_sequential_valid(self) -> None:
        cm = m.CrewModel(
            name="Seq Crew",
            process="sequential",
            agents=[m.AgentModel(role="R", goal="Research")],
            tasks=[m.TaskModel(name="T", description="D", expected_output="E")],
        )
        assert cm.process == "sequential"

    def test_hierarchical_with_manager_role(self) -> None:
        cm = m.CrewModel(
            name="Hier Crew",
            process="hierarchical",
            manager_agent_role="Lead",
        )
        assert cm.manager_agent_role == "Lead"

    def test_hierarchical_with_manager_llm(self) -> None:
        cm = m.CrewModel(
            name="Hier Crew",
            process="hierarchical",
            manager_llm=m.LLMModel(model="gpt-4o"),
        )
        assert cm.manager_llm is not None

    def test_hierarchical_without_manager_fails(self) -> None:
        with pytest.raises(ValueError, match="Hierarchical"):
            m.CrewModel(name="H Crew", process="hierarchical")


# ============================================================================
#  Agent Form Validation
# ============================================================================

class TestAgentFormValidation:
    """Agent model validation rules."""

    def test_role_required(self) -> None:
        with pytest.raises(ValueError, match="Agent role must not be empty"):
            m.AgentModel(role="  ", goal="Something")

    def test_goal_required(self) -> None:
        with pytest.raises(ValueError, match="Agent goal must not be empty"):
            m.AgentModel(role="Researcher", goal="  ")

    def test_agent_with_tools(self) -> None:
        agent = m.AgentModel(
            role="Researcher",
            goal="Find info",
            tools=[
                m.ToolRef(kind="builtin", name="SerperDevTool"),
                m.ToolRef(kind="builtin", name="FileReadTool"),
            ],
        )
        assert len(agent.tools) == 2
        assert agent.tools[0].name == "SerperDevTool"

    def test_agent_allow_delegation(self) -> None:
        agent = m.AgentModel(role="Lead", goal="Oversee", allow_delegation=True)
        assert agent.allow_delegation is True

    def test_agent_max_iter(self) -> None:
        agent = m.AgentModel(role="Worker", goal="Execute", max_iter=5)
        assert agent.max_iter == 5

    def test_agent_max_iter_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            m.AgentModel(role="W", goal="G", max_iter=-1)

    def test_agent_with_llm(self) -> None:
        agent = m.AgentModel(
            role="AI",
            goal="Think",
            llm=m.LLMModel(model="claude-3-5-sonnet", temperature=0.3),
        )
        assert agent.llm is not None
        assert agent.llm.model == "claude-3-5-sonnet"
        assert agent.llm.temperature == 0.3


# ============================================================================
#  Task Form Validation
# ============================================================================

class TestTaskFormValidation:
    """Task model validation rules."""

    def test_name_required(self) -> None:
        with pytest.raises(ValueError, match="Task name must not be empty"):
            m.TaskModel(name="", description="D", expected_output="E")

    def test_description_required(self) -> None:
        with pytest.raises(ValueError, match="Task description must not be empty"):
            m.TaskModel(name="N", description="", expected_output="E")

    def test_expected_output_required(self) -> None:
        with pytest.raises(ValueError, match="Task expected_output must not be empty"):
            m.TaskModel(name="N", description="D", expected_output="")

    def test_self_context_rejected(self) -> None:
        """A task cannot depend on itself - validated at crew construction."""
        with pytest.raises(ValueError, match="cannot depend on itself"):
            m.CrewModel(
                name="Self Context",
                tasks=[
                    m.TaskModel(
                        name="A", description="Desc A",
                        expected_output="Out A", context=["A"],
                    ),
                ],
            )

    def test_cycle_rejected(self) -> None:
        """Cycle A->B->C->A is rejected at crew construction."""
        with pytest.raises(ValueError, match="Circular dependency"):
            m.CrewModel(
                name="Cycle Crew",
                tasks=[
                    m.TaskModel(
                        name="A", description="A",
                        expected_output="OA", context=["C"],
                    ),
                    m.TaskModel(
                        name="B", description="B",
                        expected_output="OB", context=["A"],
                    ),
                    m.TaskModel(
                        name="C", description="C",
                        expected_output="OC", context=["B"],
                    ),
                ],
            )

    def test_valid_dag(self) -> None:
        """A -> B -> C is a valid DAG."""
        cm = m.CrewModel(
            name="Valid DAG",
            tasks=[
                m.TaskModel(name="A", description="A", expected_output="OA"),
                m.TaskModel(
                    name="B", description="B",
                    expected_output="OB", context=["A"],
                ),
                m.TaskModel(
                    name="C", description="C",
                    expected_output="OC", context=["B"],
                ),
            ],
        )
        assert cm.tasks[2].context == ["B"]

    def test_output_file_parent_dir_rejected(self) -> None:
        """output_file containing '..' is rejected."""
        with pytest.raises(ValueError, match="parent-dir"):
            m.TaskModel(
                name="Bad Path", description="D",
                expected_output="E", output_file="../escape.txt",
            )

    def test_output_file_absolute_rejected(self) -> None:
        """Absolute output_file paths are rejected."""
        with pytest.raises(ValueError, match="absolute"):
            m.TaskModel(
                name="Bad Path", description="D",
                expected_output="E", output_file="/etc/passwd",
            )

    def test_output_file_relative_ok(self) -> None:
        task = m.TaskModel(
            name="Good Path", description="D",
            expected_output="E", output_file="output/report.txt",
        )
        assert task.output_file == "output/report.txt"

    def test_task_with_guardrails(self) -> None:
        task = m.TaskModel(
            name="Guarded", description="D", expected_output="E",
            guardrails=["max_length(100)", "no_pii"],
            guardrail_max_retries=2,
        )
        assert len(task.guardrails) == 2
        assert task.guardrail_max_retries == 2

    def test_agent_role_references_existing_agent(self) -> None:
        with pytest.raises(ValueError, match="references agent_role"):
            m.CrewModel(
                name="Missing Agent",
                tasks=[
                    m.TaskModel(
                        name="T", description="D",
                        expected_output="E", agent_role="GhostAgent",
                    ),
                ],
            )

    def test_agent_role_references_valid_agent(self) -> None:
        cm = m.CrewModel(
            name="Valid Agent Ref",
            agents=[m.AgentModel(role="Researcher", goal="Research")],
            tasks=[
                m.TaskModel(
                    name="T", description="D",
                    expected_output="E", agent_role="Researcher",
                ),
            ],
        )
        assert cm.tasks[0].agent_role == "Researcher"


# ============================================================================
#  Tool Catalogue
# ============================================================================

class TestToolCatalogue:
    """Built-in tool catalogue structure and search."""

    def test_catalogue_has_expected_tools(self) -> None:
        assert len(BUILTIN_TOOLS) > 0

    def test_serper_dev_tool_present(self) -> None:
        names = [t["name"] for t in BUILTIN_TOOLS]
        assert "SerperDevTool" in names

    def test_all_tools_have_name_and_description(self) -> None:
        for tool in BUILTIN_TOOLS:
            assert tool["name"], f"Tool has empty name: {tool}"
            assert tool["description"], f"Tool {tool['name']} has empty description"

    def test_no_duplicate_tool_names(self) -> None:
        names = [t["name"] for t in BUILTIN_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


# ============================================================================
#  Integration Smoke Tests
# ============================================================================

class TestBuilderIntegration:
    """Smoke tests that verify the builder module integrates with the app."""

    def test_render_builder_is_callable(self) -> None:
        assert callable(builder.render_builder)

    def test_state_sync_roundtrip(self) -> None:
        """Crew model round-trips through storage correctly."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Test Crew",
                description="Integration test",
                process="sequential",
                agents=[m.AgentModel(role="R", goal="Find answers")],
                tasks=[
                    m.TaskModel(
                        name="T1", description="Do thing",
                        expected_output="Result",
                    ),
                ],
            )
            builder._persist(cm)
            loaded = builder._crew_model()

        assert loaded.name == "Test Crew"
        assert loaded.description == "Integration test"
        assert len(loaded.agents) == 1
        assert loaded.agents[0].role == "R"
        assert len(loaded.tasks) == 1
        assert loaded.tasks[0].name == "T1"

    def test_toolref_creation(self) -> None:
        builtin = m.ToolRef(kind="builtin", name="SerperDevTool")
        assert builtin.kind == "builtin"
        assert builtin.name == "SerperDevTool"

        custom = m.ToolRef(
            kind="custom",
            name="MyTool",
            args_schema={"param": {"type": "string"}},
        )
        assert custom.kind == "custom"
        assert custom.args_schema is not None

    def test_complex_crew_persists(self) -> None:
        """A complex crew with agents, tasks, and hierarchical config persists."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Complex Crew",
                process="hierarchical",
                manager_agent_role="Lead",
                agents=[
                    m.AgentModel(
                        role="Lead",
                        goal="Oversee the project",
                        allow_delegation=True,
                        tools=[m.ToolRef(kind="builtin", name="SerperDevTool")],
                    ),
                    m.AgentModel(
                        role="Writer",
                        goal="Write content",
                        tools=[m.ToolRef(kind="builtin", name="FileWriterTool")],
                    ),
                ],
                tasks=[
                    m.TaskModel(
                        name="Research",
                        description="Find info on topic",
                        expected_output="Research notes",
                        agent_role="Lead",
                    ),
                    m.TaskModel(
                        name="Write",
                        description="Write the report",
                        expected_output="Final report",
                        agent_role="Writer",
                        context=["Research"],
                        output_file="reports/output.md",
                        markdown=True,
                    ),
                ],
                planning=True,
            )
            builder._persist(cm)
            loaded = builder._crew_model()

        assert loaded.name == "Complex Crew"
        assert loaded.process == "hierarchical"
        assert len(loaded.agents) == 2
        assert len(loaded.tasks) == 2
        assert loaded.tasks[1].context == ["Research"]
        assert loaded.tasks[1].output_file == "reports/output.md"


# ============================================================================
#  Variable validation (model-level, tested here for builder relevance)
# ============================================================================

class TestVariableReferences:
    """Variable interpolation warnings (builder-relevant)."""

    def test_missing_variable_warns(self) -> None:
        """Agent goal referencing undefined {var} emits a warning."""
        with pytest.warns(RuntimeWarning, match="does not match any crew input"):
            m.CrewModel(
                name="Missing Var",
                agents=[m.AgentModel(role="R", goal="Research {topic}")],
            )

    def test_defined_variable_no_warning(self) -> None:
        """Agent goal referencing defined input does not warn."""
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            m.CrewModel(
                name="Has Var",
                inputs=[m.InputVar(name="topic", type="str", default="AI")],
                agents=[m.AgentModel(role="R", goal="Research {topic}")],
            )
        runtime_warnings = [
            w for w in record if issubclass(w.category, RuntimeWarning)
        ]
        assert len(runtime_warnings) == 0
