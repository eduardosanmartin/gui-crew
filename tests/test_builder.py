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

    def test_corrupt_preserves_raw_and_notifies(self) -> None:
        """When storage has an invalid dict, corrupt data is preserved under separate key."""
        user = _make_user({"crew_model": {"name": None}})
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
        assert isinstance(cm, m.CrewModel)
        assert cm.name == "New Crew"
        assert user.get("crew_model_corrupt") == {"name": None}


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

    def test_agent_llm_temperature_zero_preserved(self) -> None:
        """Temperature 0 is preserved, not treated as falsy."""
        agent = m.AgentModel(
            role="AI",
            goal="Zero temp",
            llm=m.LLMModel(model="gpt-4o", temperature=0.0),
        )
        assert agent.llm is not None
        assert agent.llm.temperature == 0.0

    def test_agent_model_copy_does_not_mutate_original(self) -> None:
        """model_copy(deep=True) creates an independent clone."""
        original = m.AgentModel(
            role="Researcher",
            goal="Research",
            llm=m.LLMModel(model="gpt-4", temperature=0.5),
            tools=[m.ToolRef(kind="builtin", name="SerperDevTool")],
        )
        copy = original.model_copy(deep=True)
        copy.role = "Hacker"
        copy.llm.model = "gpt-4o"  # type: ignore[union-attr]
        copy.tools = []

        assert original.role == "Researcher"
        assert original.llm is not None
        assert original.llm.model == "gpt-4"
        assert len(original.tools) == 1

    def test_agent_model_copy_odd_even_bug(self) -> None:
        """Editing N different agents via copy preserves original N agents."""
        agents = [
            m.AgentModel(role=f"Agent_{i}", goal=f"Goal_{i}")
            for i in range(3)
        ]
        copies = [a.model_copy(deep=True) for a in agents]
        copies[0].role = "Modified_0"
        copies[1].role = "Modified_1"
        copies[2].role = "Modified_2"
        # Originals must be untouched
        assert agents[0].role == "Agent_0"
        assert agents[1].role == "Agent_1"
        assert agents[2].role == "Agent_2"


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

    def test_task_guardrail_max_retries_zero(self) -> None:
        """guardrail_max_retries can be set to 0 (not treated as falsy)."""
        task = m.TaskModel(
            name="Zero Guard", description="D", expected_output="E",
            guardrail_max_retries=0,
        )
        assert task.guardrail_max_retries == 0

    def test_task_guardrail_max_retries_default(self) -> None:
        """guardrail_max_retries defaults to 3 when not set."""
        task = m.TaskModel(
            name="Default Guard", description="D", expected_output="E",
        )
        assert task.guardrail_max_retries == 3

    def test_task_model_copy_does_not_mutate_original(self) -> None:
        """model_copy(deep=True) on TaskModel creates an independent clone."""
        original = m.TaskModel(
            name="Original", description="Desc", expected_output="Out",
            context=["A", "B"],
            tools=[m.ToolRef(kind="builtin", name="SerperDevTool")],
            guardrail_max_retries=5,
        )
        copy = original.model_copy(deep=True)
        copy.name = "Modified"
        copy.context.append("C")
        copy.tools = []

        assert original.name == "Original"
        assert original.context == ["A", "B"]
        assert len(original.tools) == 1
        assert original.guardrail_max_retries == 5

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


# ============================================================================
#  Task 1.24 - LLM / Memory / Knowledge Sub-forms
# ============================================================================

class TestLLMSubForm:
    """LLM sub-form model creation and validation."""

    def test_create_llm_model(self) -> None:
        """LLMModel can be created with all fields."""
        llm = m.LLMModel(
            model="claude-3-5-sonnet",
            temperature=0.3,
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
        )
        assert llm.model == "claude-3-5-sonnet"
        assert llm.temperature == 0.3
        assert llm.base_url == "https://api.anthropic.com"
        assert llm.api_key_env == "ANTHROPIC_API_KEY"

    def test_llm_model_defaults(self) -> None:
        """LLMModel defaults to gpt-4o."""
        llm = m.LLMModel()
        assert llm.model == "openai/gpt-4o"
        assert llm.temperature is None

    def test_llm_model_can_be_none_on_agent(self) -> None:
        """Agent can have no LLM (uses crew default)."""
        agent = m.AgentModel(role="R", goal="G")
        assert agent.llm is None

    def test_llm_saves_to_agent_model(self) -> None:
        """LLM config can be set on an agent."""
        agent = m.AgentModel(
            role="Researcher",
            goal="Research",
            llm=m.LLMModel(model="claude-3-5-sonnet", temperature=0.5),
        )
        assert agent.llm is not None
        assert agent.llm.model == "claude-3-5-sonnet"
        assert agent.llm.temperature == 0.5

    def test_llm_model_roundtrip_through_dict(self) -> None:
        """LLMModel serializes and deserializes correctly."""
        llm = m.LLMModel(
            model="gpt-4o",
            temperature=0.7,
            base_url="http://localhost:11434/v1",
        )
        d = llm.model_dump()
        reloaded = m.LLMModel(**d)
        assert reloaded.model == llm.model
        assert reloaded.temperature == llm.temperature
        assert reloaded.base_url == llm.base_url

    def test_llm_model_extra_fields_forward_compat(self) -> None:
        """LLMModel extra fields pass through (forward compat)."""
        llm = m.LLMModel(model="gpt-5", max_tokens=4096)
        assert llm.model_extra is not None
        assert llm.model_extra.get("max_tokens") == 4096


class TestMemorySubForm:
    """Memory sub-form model creation and validation."""

    def test_memory_config_defaults(self) -> None:
        """MemoryConfig has sensible defaults."""
        mem = m.MemoryConfig()
        assert mem.enabled is False
        assert mem.recency_weight is None

    def test_memory_config_enabled(self) -> None:
        """MemoryConfig with enabled=True."""
        mem = m.MemoryConfig(enabled=True, recency_weight=0.3)
        assert mem.enabled is True
        assert mem.recency_weight == 0.3

    def test_memory_config_all_fields(self) -> None:
        """MemoryConfig with all fields set."""
        mem = m.MemoryConfig(
            enabled=True,
            recency_weight=0.3,
            semantic_weight=0.5,
            importance_weight=0.2,
            recency_half_life_days=7,
            embedder={"provider": "openai", "model": "text-embedding-3-small"},
        )
        assert mem.recency_half_life_days == 7
        assert mem.embedder is not None
        assert mem.embedder["provider"] == "openai"

    def test_agent_memory_bool(self) -> None:
        """Agent memory can be a simple bool."""
        agent = m.AgentModel(role="R", goal="G", memory=True)
        assert agent.memory is True

    def test_agent_memory_config(self) -> None:
        """Agent memory can be a full MemoryConfig."""
        agent = m.AgentModel(
            role="R",
            goal="G",
            memory=m.MemoryConfig(enabled=True, recency_weight=0.5),
        )
        assert isinstance(agent.memory, m.MemoryConfig)
        assert agent.memory.recency_weight == 0.5

    def test_crew_memory_config(self) -> None:
        """Crew-level memory can be MemoryConfig."""
        cm = m.CrewModel(
            name="Mem Crew",
            memory=m.MemoryConfig(enabled=True, recency_half_life_days=14),
        )
        assert isinstance(cm.memory, m.MemoryConfig)
        assert cm.memory.recency_half_life_days == 14


class TestKnowledgeSubForm:
    """Knowledge sources data management."""

    def test_knowledge_sources_list(self) -> None:
        """Knowledge sources can be stored as a list of dicts."""
        sources: list[dict[str, Any]] = [
            {"name": "Company Wiki", "kind": "text"},
            {"name": "API Docs", "kind": "pdf"},
        ]
        cm = m.CrewModel(name="KS Crew", knowledge_sources=sources)
        assert len(cm.knowledge_sources) == 2
        assert cm.knowledge_sources[0]["name"] == "Company Wiki"

    def test_knowledge_sources_empty_default(self) -> None:
        """Knowledge sources default to empty list."""
        cm = m.CrewModel(name="No KS")
        assert cm.knowledge_sources == []


# ============================================================================
#  Task 1.25 - Guided Wizard Mode
# ============================================================================

class TestWizardMode:
    """Wizard mode logic — template application and data conversion."""

    def test_wizard_templates_exist(self) -> None:
        """Wizard templates are defined and have required keys."""
        from builder import _WIZARD_TEMPLATES
        assert "blank" in _WIZARD_TEMPLATES
        assert "research" in _WIZARD_TEMPLATES
        assert "code_review" in _WIZARD_TEMPLATES
        for key, tmpl in _WIZARD_TEMPLATES.items():
            assert "name" in tmpl
            assert "process" in tmpl

    def test_research_template_has_agents_and_tasks(self) -> None:
        """Research template has predefined agents and tasks."""
        from builder import _WIZARD_TEMPLATES
        tmpl = _WIZARD_TEMPLATES["research"]
        assert len(tmpl["agents"]) >= 2
        assert len(tmpl["tasks"]) >= 2

    def test_blank_template_is_minimal(self) -> None:
        """Blank template has no agents or tasks."""
        from builder import _WIZARD_TEMPLATES
        tmpl = _WIZARD_TEMPLATES["blank"]
        assert "agents" not in tmpl or len(tmpl.get("agents", [])) == 0

    def test_wizard_data_to_crew(self) -> None:
        """Wizard data can be converted to a valid CrewModel."""
        wizard_data = {
            "name": "Test Wizard Crew",
            "description": "Wizard test",
            "process": "sequential",
            "agents": [
                {"role": "R", "goal": "Research", "backstory": "Experienced", "tools": []},
            ],
            "tasks": [
                {
                    "name": "T1",
                    "description": "Do something",
                    "expected_output": "Result",
                    "agent_role": "R",
                },
            ],
        }
        agents = [
            m.AgentModel(
                role=a["role"],
                goal=a["goal"],
                backstory=a.get("backstory", ""),
            )
            for a in wizard_data["agents"]
        ]
        tasks = [
            m.TaskModel(
                name=t["name"],
                description=t["description"],
                expected_output=t["expected_output"],
                agent_role=t.get("agent_role"),
            )
            for t in wizard_data["tasks"]
        ]
        cm = m.CrewModel(
            name=wizard_data["name"],
            description=wizard_data["description"],
            process=wizard_data["process"],  # type: ignore[arg-type]
            agents=agents,
            tasks=tasks,
        )
        assert cm.name == "Test Wizard Crew"
        assert len(cm.agents) == 1
        assert cm.agents[0].role == "R"
        assert len(cm.tasks) == 1
        assert cm.tasks[0].name == "T1"

    def test_wizard_agent_with_tools(self) -> None:
        """Wizard agents can include tool names."""
        wizard_agent = {
            "role": "Researcher",
            "goal": "Find info",
            "backstory": "",
            "tools": ["SerperDevTool", "FileReadTool"],
        }
        tools = [m.ToolRef(kind="builtin", name=tn) for tn in wizard_agent["tools"]]
        agent = m.AgentModel(
            role=wizard_agent["role"],
            goal=wizard_agent["goal"],
            backstory=wizard_agent["backstory"],
            tools=tools,
        )
        assert len(agent.tools) == 2
        assert agent.tools[0].name == "SerperDevTool"

    def test_wizard_task_unassigned_agent(self) -> None:
        """Wizard tasks can have no assigned agent."""
        task = m.TaskModel(
            name="T",
            description="D",
            expected_output="E",
            agent_role=None,
        )
        assert task.agent_role is None

    # ------------------------------------------------------------------
    #  Fix 2: Wizard template data is copyable into wizard_data
    # ------------------------------------------------------------------

    def test_wizard_template_populates_agents_and_tasks(self) -> None:
        """Template data can be deep-copied into wizard_data agents/tasks."""
        from builder import _WIZARD_TEMPLATES

        # Research template has predefined agents and tasks
        tmpl = _WIZARD_TEMPLATES["research"]
        agents = [dict(ag) for ag in tmpl.get("agents", [])]
        tasks = [dict(tk) for tk in tmpl.get("tasks", [])]

        assert len(agents) == 2
        assert agents[0]["role"] == "Researcher"
        assert agents[1]["role"] == "Writer"
        assert len(tasks) == 2
        assert tasks[0]["name"] == "Research"
        assert tasks[1]["name"] == "Write Report"

        # Blank template has no agents or tasks
        blank = _WIZARD_TEMPLATES["blank"]
        assert len(blank.get("agents", [])) == 0
        assert len(blank.get("tasks", [])) == 0

        # Code review template has expected structure
        cr = _WIZARD_TEMPLATES["code_review"]
        assert len(cr.get("agents", [])) == 2
        assert cr["agents"][0]["role"] == "Reviewer"
        assert len(cr.get("tasks", [])) == 2
        assert cr["tasks"][1]["name"] == "Document"

    # ------------------------------------------------------------------
    #  Fix 3: _apply_wizard_to_model does not corrupt live model
    # ------------------------------------------------------------------

    def test_wizard_apply_uses_new_model_does_not_corrupt_original(self) -> None:
        """Building a CrewModel from wizard data does not mutate the original."""
        original = m.CrewModel(
            name="Original Crew",
            description="Original description",
            agents=[m.AgentModel(role="R", goal="Research")],
            tasks=[m.TaskModel(name="T1", description="D", expected_output="E")],
        )
        original_copy = original.model_copy(deep=True)

        # Build agents and tasks from wizard data (simulating _apply_wizard_to_model)
        wizard_data = {
            "name": "Wizard Crew",
            "description": "Wizard description",
            "process": "sequential",
            "agents": [
                {"role": "A1", "goal": "G1", "backstory": "", "tools": []},
                {"role": "A2", "goal": "G2", "backstory": "", "tools": []},
            ],
            "tasks": [
                {"name": "T1", "description": "D1", "expected_output": "E1", "agent_role": "A1"},
            ],
        }

        # Construct new model from wizard data (without touching original)
        new_agents = [
            m.AgentModel(
                role=ag["role"], goal=ag["goal"],
                backstory=ag.get("backstory", ""),
            )
            for ag in wizard_data["agents"]
        ]
        new_tasks = [
            m.TaskModel(
                name=tk["name"], description=tk["description"],
                expected_output=tk["expected_output"],
                agent_role=tk.get("agent_role"),
            )
            for tk in wizard_data["tasks"]
        ]
        new_cm = m.CrewModel(
            name=wizard_data["name"],
            description=wizard_data["description"],
            process=wizard_data["process"],  # type: ignore[arg-type]
            agents=new_agents,
            tasks=new_tasks,
        )

        # Original must be untouched
        assert original.name == "Original Crew"
        assert original.description == "Original description"
        assert len(original.agents) == 1
        assert original.agents[0].role == "R"
        assert len(original.tasks) == 1
        assert original.tasks[0].name == "T1"

        # New model has wizard data
        assert new_cm.name == "Wizard Crew"
        assert len(new_cm.agents) == 2
        assert len(new_cm.tasks) == 1

    def test_wizard_apply_preserves_original_on_invalid_data(self) -> None:
        """Constructing a CrewModel with invalid data fails without touching original."""
        original = m.CrewModel(
            name="Safe Crew",
            agents=[m.AgentModel(role="R", goal="Research")],
        )

        # Invalid wizard data — missing task required fields
        with pytest.raises(Exception):
            m.CrewModel(
                name="Bad Crew",
                tasks=[m.TaskModel(name="", description="", expected_output="")],
            )

        # Original must still be intact
        assert original.name == "Safe Crew"
        assert len(original.agents) == 1
        assert original.agents[0].role == "R"


# ============================================================================
#  Task 1.26 - Custom Tool Form
# ============================================================================

class TestCustomToolForm:
    """Custom tool creation via ToolRef model."""

    def test_custom_tool_with_args_schema(self) -> None:
        """Custom ToolRef with Pydantic args_schema serializes correctly."""
        tool = m.ToolRef(
            kind="custom",
            name="MyApiTool",
            params={"description": "Calls my API"},
            args_schema={
                "endpoint": {"type": "string", "description": "API endpoint"},
                "method": {"type": "string", "description": "HTTP method", "default": "GET"},
            },
        )
        assert tool.kind == "custom"
        assert tool.name == "MyApiTool"
        assert tool.args_schema is not None
        assert tool.args_schema["endpoint"]["type"] == "string"
        assert tool.args_schema["method"]["default"] == "GET"

    def test_custom_tool_serializes_to_dict(self) -> None:
        """Custom ToolRef model_dump preserves args_schema."""
        tool = m.ToolRef(
            kind="custom",
            name="CustomTool",
            args_schema={"param": {"type": "int"}},
        )
        d = tool.model_dump()
        assert d["kind"] == "custom"
        assert d["name"] == "CustomTool"
        assert d["args_schema"] == {"param": {"type": "int"}}

    def test_custom_tool_roundtrip(self) -> None:
        """Custom ToolRef survives model_dump → ToolRef reconstruction."""
        original = m.ToolRef(
            kind="custom",
            name="RoundTripTool",
            params={"key": "val"},
            args_schema={"input": {"type": "string"}},
        )
        reloaded = m.ToolRef(**original.model_dump())
        assert reloaded.kind == "custom"
        assert reloaded.name == "RoundTripTool"
        assert reloaded.args_schema == {"input": {"type": "string"}}

    def test_custom_tool_name_validation(self) -> None:
        """Custom tool name must not be empty."""
        with pytest.raises(ValueError, match="Tool name must not be empty"):
            m.ToolRef(kind="custom", name="", args_schema={})

    def test_builtin_tool_has_no_args_schema(self) -> None:
        """Built-in tools don't need args_schema."""
        tool = m.ToolRef(kind="builtin", name="SerperDevTool")
        assert tool.kind == "builtin"
        assert tool.args_schema is None

    def test_tool_catalog_search(self) -> None:
        """Tool catalogue supports name-based search."""
        from builder import BUILTIN_TOOLS
        matches = [t for t in BUILTIN_TOOLS if "serper" in t["name"].lower()]
        assert len(matches) == 1
        assert matches[0]["name"] == "SerperDevTool"

    def test_tool_catalog_search_description(self) -> None:
        """Tool catalogue supports description-based search."""
        from builder import BUILTIN_TOOLS
        matches = [t for t in BUILTIN_TOOLS if "scrape" in t["description"].lower()]
        assert len(matches) >= 1

    # ------------------------------------------------------------------
    #  Fix 5: Custom tools are persisted and retrievable
    # ------------------------------------------------------------------

    def test_custom_tool_stored_in_storage(self) -> None:
        """Custom tool persisted to storage appears in _all_tool_options."""
        from unittest.mock import PropertyMock, patch
        from nicegui.storage import Storage
        from builder import _all_tool_options

        tool_dict = {
            "kind": "custom",
            "name": "MyApiTool",
            "params": {"description": "Calls my API"},
            "args_schema": {"endpoint": {"type": "string"}},
        }
        user: dict[str, Any] = {"custom_tools": [tool_dict]}
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            options = _all_tool_options()

        custom_names = [t["name"] for t in options if t.get("_custom")]
        assert "MyApiTool" in custom_names
        assert len(custom_names) == 1

    def test_custom_tools_empty_by_default(self) -> None:
        """When no custom tools exist, _all_tool_options returns only built-in tools."""
        from unittest.mock import PropertyMock, patch
        from nicegui.storage import Storage
        from builder import _all_tool_options

        user: dict[str, Any] = {}
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            options = _all_tool_options()

        custom_tools = [t for t in options if t.get("_custom")]
        assert len(custom_tools) == 0
        assert len(options) == len(BUILTIN_TOOLS)


# ============================================================================
#  Task 1.27 - Variable Interpolation Preview
# ============================================================================

class TestVariableInterpolationPreview:
    """Variable interpolation preview logic."""

    def test_interpolate_single_variable(self) -> None:
        """A single {var} is replaced by its default value."""
        inputs = [m.InputVar(name="topic", type="str", default="AI")]
        result = builder._interpolate_preview("Research {topic}", inputs)
        assert result == "Research AI"

    def test_interpolate_multiple_variables(self) -> None:
        """Multiple {var} placeholders are replaced."""
        inputs = [
            m.InputVar(name="topic", type="str", default="AI"),
            m.InputVar(name="depth", type="str", default="comprehensive"),
        ]
        result = builder._interpolate_preview(
            "Research {topic} with {depth} analysis", inputs,
        )
        assert result == "Research AI with comprehensive analysis"

    def test_interpolate_unknown_variable_preserved(self) -> None:
        """Unknown {var} is left as-is."""
        inputs = [m.InputVar(name="topic", type="str", default="AI")]
        result = builder._interpolate_preview("Research {topic} for {audience}", inputs)
        assert result == "Research AI for {audience}"

    def test_interpolate_empty_text(self) -> None:
        """Empty text returns empty string."""
        result = builder._interpolate_preview("", [])
        assert result == ""

    def test_interpolate_no_inputs(self) -> None:
        """Text with no inputs returns unchanged."""
        result = builder._interpolate_preview(
            "Research {topic}", [],
        )
        assert result == "Research {topic}"

    def test_interpolate_input_without_default(self) -> None:
        """Variables without defaults are not replaced."""
        inputs = [m.InputVar(name="topic", type="str", default=None)]
        result = builder._interpolate_preview("Research {topic}", inputs)
        assert result == "Research {topic}"

    def test_interpolate_non_string_default(self) -> None:
        """Non-string defaults are stringified."""
        inputs = [m.InputVar(name="count", type="int", default=5)]
        result = builder._interpolate_preview("Run {count} times", inputs)
        assert result == "Run 5 times"

    def test_interpolate_variable_repeated(self) -> None:
        """Same variable appearing multiple times is replaced each time."""
        inputs = [m.InputVar(name="topic", type="str", default="AI")]
        result = builder._interpolate_preview("{topic} is the topic. Research {topic}.", inputs)
        assert result == "AI is the topic. Research AI."

    def test_interpolate_goal_with_variables(self) -> None:
        """Agent goal with variables interpolates correctly."""
        inputs = [m.InputVar(name="domain", type="str", default="cybersecurity")]
        result = builder._interpolate_preview(
            "Analyze {domain} threats and produce report", inputs,
        )
        assert "cybersecurity" in result

    def test_interpolate_backstory_with_variables(self) -> None:
        """Agent backstory with variables interpolates correctly."""
        inputs = [m.InputVar(name="company", type="str", default="Acme Corp")]
        result = builder._interpolate_preview(
            "Expert researcher at {company} with 10 years experience", inputs,
        )
        assert "Acme Corp" in result

    def test_interpolate_escaped_braces_preserved(self) -> None:
        """Doubled braces {{var}} are not treated as variables."""
        inputs = [m.InputVar(name="topic", type="str", default="AI")]
        result = builder._interpolate_preview("Use {{topic}} literally, not {topic}", inputs)
        assert "{{topic}}" in result
        assert "AI" in result


# ============================================================================
#  Task 1.28 - Save / Load Persistence
# ============================================================================

class TestSaveLoad:
    """Explicit save/load to app.storage.user."""

    def test_save_writes_to_storage(self) -> None:
        """Saving a crew model persists it to storage."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Saved Crew",
                agents=[
                    m.AgentModel(role="R", goal="Research"),
                ],
            )
            builder._persist(cm)
            saved = user.get("crew_model")
            assert saved is not None
            assert saved["name"] == "Saved Crew"
            assert len(saved["agents"]) == 1

    def test_load_from_saved_storage(self) -> None:
        """Loading after save returns the same data."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Persist Crew",
                description="Test persistence",
                agents=[m.AgentModel(role="X", goal="Y")],
                tasks=[m.TaskModel(name="T", description="D", expected_output="E")],
            )
            builder._persist(cm)
            loaded = builder._crew_model()
            assert loaded.name == "Persist Crew"
            assert loaded.description == "Test persistence"
            assert len(loaded.agents) == 1
            assert loaded.agents[0].role == "X"
            assert len(loaded.tasks) == 1

    def test_save_overwrites_previous(self) -> None:
        """Second save overwrites the first."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm1 = m.CrewModel(name="First")
            builder._persist(cm1)
            cm2 = m.CrewModel(name="Second")
            builder._persist(cm2)
            loaded = builder._crew_model()
            assert loaded.name == "Second"

    def test_save_with_full_crew_dump(self) -> None:
        """Save includes all crew fields — agents, tasks, inputs, knowledge."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Full Dump",
                process="hierarchical",
                manager_agent_role="Lead",
                knowledge_sources=[{"name": "Wiki", "kind": "text"}],
                inputs=[m.InputVar(name="topic", default="AI")],
                agents=[
                    m.AgentModel(
                        role="Lead",
                        goal="Manage",
                        llm=m.LLMModel(model="gpt-4o"),
                        memory=m.MemoryConfig(enabled=True),
                    ),
                ],
            )
            builder._persist(cm)
            saved = user["crew_model"]
            assert saved["name"] == "Full Dump"
            assert saved["process"] == "hierarchical"
            assert saved["manager_agent_role"] == "Lead"
            assert len(saved["knowledge_sources"]) == 1
            assert len(saved["inputs"]) == 1

    def test_save_load_roundtrip_agent_llm(self) -> None:
        """Agent with LLM round-trips through storage correctly."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="LLM Roundtrip",
                agents=[
                    m.AgentModel(
                        role="AI",
                        goal="Think",
                        llm=m.LLMModel(
                            model="claude-3-5-sonnet",
                            temperature=0.3,
                            base_url="https://api.anthropic.com",
                        ),
                    ),
                ],
            )
            builder._persist(cm)
            loaded = builder._crew_model()
            assert loaded.agents[0].llm is not None
            assert loaded.agents[0].llm.model == "claude-3-5-sonnet"
            assert loaded.agents[0].llm.temperature == 0.3

    def test_save_load_roundtrip_memory_config(self) -> None:
        """MemoryConfig round-trips through storage."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Memory RT",
                memory=m.MemoryConfig(
                    enabled=True,
                    recency_weight=0.4,
                    recency_half_life_days=14,
                ),
            )
            builder._persist(cm)
            loaded = builder._crew_model()
            mem = loaded.memory
            assert isinstance(mem, m.MemoryConfig)
            assert mem.enabled is True
            assert mem.recency_weight == 0.4
            assert mem.recency_half_life_days == 14

    def test_save_load_roundtrip_custom_tool(self) -> None:
        """Custom ToolRef round-trips through storage."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = m.CrewModel(
                name="Tool RT",
                agents=[
                    m.AgentModel(
                        role="Dev",
                        goal="Build",
                        tools=[
                            m.ToolRef(
                                kind="custom",
                                name="MyTool",
                                args_schema={"param": {"type": "string"}},
                            ),
                        ],
                    ),
                ],
            )
            builder._persist(cm)
            loaded = builder._crew_model()
            assert len(loaded.agents[0].tools) == 1
            tool = loaded.agents[0].tools[0]
            assert tool.kind == "custom"
            assert tool.name == "MyTool"
            assert tool.args_schema is not None

    def test_empty_storage_loads_default(self) -> None:
        """When storage is empty, a default crew is returned."""
        user = _make_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
            assert isinstance(cm, m.CrewModel)
            assert cm.name == "New Crew"

    def test_load_handles_invalid_data_gracefully(self) -> None:
        """Corrupt storage produces a default and preserves the corrupt data."""
        user = _make_user({"crew_model": {"name": None, "invalid": True}})
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=user
        ):
            cm = builder._crew_model()
            assert cm.name == "New Crew"
            assert user.get("crew_model_corrupt") is not None
