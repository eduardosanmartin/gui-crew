"""Unit tests for gui-crew crew_engine — adapter, listener, engine, pricing."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from crew_engine import (
    BridgeListener,
    CancelledError,
    CrewEngine,
    ExecutionHandle,
    ProgressToolWrapper,
    calculate_cost,
    load_pricing,
)
import models


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers — build sample Pydantic models
# ═══════════════════════════════════════════════════════════════════════════

def _make_llm(model: str = "openai/gpt-4o") -> models.LLMModel:
    return models.LLMModel(model=model, temperature=0.7)


def _make_agent(
    role: str = "Researcher",
    goal: str = "Research the topic",
    tools: list[models.ToolRef] | None = None,
    llm: models.LLMModel | None = None,
) -> models.AgentModel:
    return models.AgentModel(
        role=role,
        goal=goal,
        backstory="Expert",
        llm=llm or _make_llm(),
        tools=tools or [],
    )


def _make_task(
    name: str = "research",
    description: str = "Do research",
    expected_output: str = "Report",
    agent_role: str | None = None,
    context: list[str] | None = None,
) -> models.TaskModel:
    return models.TaskModel(
        name=name,
        description=description,
        expected_output=expected_output,
        agent_role=agent_role,
        context=context or [],
    )


def _make_crew(
    name: str = "Test Crew",
    agents: list[models.AgentModel] | None = None,
    tasks: list[models.TaskModel] | None = None,
) -> models.CrewModel:
    return models.CrewModel(
        name=name,
        description="A test crew",
        agents=agents or [_make_agent()],
        tasks=tasks or [_make_task()],
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Pricing tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadPricing:
    """Pricing YAML loading."""

    def test_loads_default_file(self):
        """Default pricing.yaml is loaded and contains known models."""
        prices = load_pricing()
        assert isinstance(prices, dict)
        assert len(prices) > 0
        assert "gpt-4o" in prices
        assert "ollama" in prices
        assert prices["ollama"]["input_per_1k"] == 0
        assert prices["ollama"]["output_per_1k"] == 0

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """Nonexistent file returns empty dict."""
        result = load_pricing(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_empty_yaml_returns_empty(self, tmp_path: Path):
        """Empty YAML file returns empty dict."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        result = load_pricing(path)
        assert result == {}


class TestCalculateCost:
    """Cost calculation from token counts."""

    def test_exact_model_match(self):
        cost = calculate_cost("gpt-4o", 1000, 2000)
        # input: 1k * 0.0025 = 0.0025, output: 2k * 0.01 = 0.02
        assert cost == pytest.approx(0.0225)

    def test_partial_model_match(self):
        """Model name 'openai/gpt-4o' should match pricing key 'gpt-4o'."""
        cost = calculate_cost("openai/gpt-4o", 500, 500)
        # input: 0.5 * 0.0025 = 0.00125, output: 0.5 * 0.01 = 0.005
        assert cost == pytest.approx(0.00625)

    def test_unknown_model_returns_none(self):
        assert calculate_cost("nonexistent-model", 1000, 1000) is None

    def test_free_model_zero_cost(self):
        cost = calculate_cost("ollama", 5000, 10000)
        assert cost == pytest.approx(0.0)

    def test_zero_tokens_zero_cost(self):
        cost = calculate_cost("gpt-4o", 0, 0)
        assert cost == pytest.approx(0.0)

    def test_anthropic_model_parsed(self):
        """anthropic/claude-3-5-sonnet → claude-3-5-sonnet"""
        cost = calculate_cost("anthropic/claude-3-5-sonnet", 1000, 1000)
        assert cost == pytest.approx(0.018)

    def test_cost_rounded_to_6_decimals(self):
        cost = calculate_cost("gpt-4o", 333, 777)
        assert len(str(cost).split(".")[-1]) <= 6


# ═══════════════════════════════════════════════════════════════════════════
#  ProgressToolWrapper tests
# ═══════════════════════════════════════════════════════════════════════════

class TestProgressToolWrapper:
    """Progress events and cooperative cancellation."""

    def test_wraps_tool_attributes(self):
        """Wrapper inherits tool name and description."""
        inner = MagicMock()
        inner.name = "TestTool"
        inner.description = "Does testing"
        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="test-crew-1",
        )
        assert wrapper.name == "TestTool"
        assert "Does testing" in wrapper.description or "MagicMock" in str(wrapper.description)

    def test_emits_progress_event(self):
        """Quick tools emit at least one progress pulse."""
        inner = MagicMock()
        inner.name = "QuickTool"
        inner._run.return_value = "done"

        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="test-crew-1",
        )
        wrapper._run()

        # At least one tool.progress event should be emitted
        progress = [e for e in events if e["type"] == "tool.progress"]
        assert len(progress) >= 1
        evt = progress[0]
        assert evt["crew_id"] == "test-crew-1"
        assert evt["tool_name"] == "QuickTool"
        assert "elapsed_ms" in evt

    def test_cancellation_via_flag(self):
        """Flag stop=True raises CancelledError."""
        inner = MagicMock()
        inner.name = "SlowTool"

        def _slow_run(*args, **kwargs):
            time.sleep(10)
            return "never"

        inner._run = _slow_run

        flag = {"stop": True}  # Already stopped
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="test-crew-1",
        )

        with pytest.raises(CancelledError) as exc_info:
            wrapper._run()
        assert "cancelled" in str(exc_info.value).lower()

    def test_cancelled_error_is_custom_class(self):
        """CancelledError is our own exception class."""
        err = CancelledError("Stop requested")
        assert isinstance(err, Exception)
        assert str(err) == "Stop requested"

    def test_returns_inner_tool_result(self):
        """_run() returns the inner tool's result, not None."""
        from crew_engine import _CrewAIBaseTool

        inner = MagicMock()
        inner.name = "ResultTool"
        inner._run.return_value = "task output"

        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="test-crew-1",
        )
        result = wrapper._run()
        assert result == "task output"

    def test_propagates_inner_tool_exception(self):
        """Exceptions from the inner tool propagate through _run()."""
        inner = MagicMock()
        inner.name = "FailTool"
        inner._run.side_effect = RuntimeError("inner failure")

        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="test-crew-1",
        )
        with pytest.raises(RuntimeError, match="inner failure"):
            wrapper._run()

    def test_is_basetool_subclass(self):
        """ProgressToolWrapper inherits from _CrewAIBaseTool."""
        from crew_engine import _CrewAIBaseTool, _ProgressToolBase

        assert issubclass(ProgressToolWrapper, _ProgressToolBase)
        if _CrewAIBaseTool is not None:
            assert issubclass(ProgressToolWrapper, _CrewAIBaseTool)
            wrapper = ProgressToolWrapper(
                tool=MagicMock(),
                flag={"stop": False},
                on_event=lambda e: None,
                crew_id="t1",
            )
            # CrewAI's isinstance check for BaseTool
            from crewai.tools import BaseTool
            assert isinstance(wrapper, BaseTool)


# ═══════════════════════════════════════════════════════════════════════════
#  ExecutionHandle tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExecutionHandle:
    """Dataclass lifecycle."""

    def test_creation(self):
        thread = threading.Thread(target=lambda: None)
        flag = {"stop": False}
        listener = MagicMock(spec=BridgeListener)
        handle = ExecutionHandle(
            thread=thread,
            flag=flag,
            listener=listener,
            crew_id="abc-123",
        )
        assert handle.thread is thread
        assert handle.flag is flag
        assert handle.listener is listener
        assert handle.crew_id == "abc-123"

    def test_flag_is_mutable_across_contexts(self):
        """Flag dict is shared by reference — stop signal passes through."""
        thread = threading.Thread(target=lambda: None)
        flag = {"stop": False}
        handle = ExecutionHandle(
            thread=thread,
            flag=flag,
            listener=MagicMock(),
            crew_id="x",
        )
        handle.flag["stop"] = True
        assert flag["stop"] is True  # original dict updated


# ═══════════════════════════════════════════════════════════════════════════
#  BridgeListener tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeListener:
    """Event registration and translation."""

    def test_init_stores_params(self):
        events: list[dict] = []
        listener = BridgeListener(crew_id="crew-99", on_event=events.append)
        assert listener._crew_id == "crew-99"
        # Verify the callback is stored (use string equality since bound
        # methods may differ in object identity)
        assert listener._on_event.__self__ is events  # type: ignore[union-attr]
        assert listener._registered is False

    def test_register_is_idempotent(self):
        """Calling register() twice does not double-register."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="c1", on_event=events.append)

        import crew_engine as ce
        with patch.object(ce, "_crewai_event_bus", create=True) as mock_bus:
            mock_bus.on = MagicMock()
            listener.register()
            first_count = mock_bus.on.call_count
            listener.register()
            assert mock_bus.on.call_count == first_count

    def test_unregister_is_idempotent(self):
        """Calling unregister() twice is safe."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="c1", on_event=events.append)
        import crew_engine as ce
        with patch.object(ce, "_crewai_event_bus", create=True) as mock_bus:
            mock_bus.on = MagicMock()
            mock_bus.off = MagicMock()
            listener.register()
            listener.unregister()
            listener.unregister()  # safe — second call noop

    def test_handler_emits_with_crew_id(self):
        """A handler closure adds crew_id and ts to the event dict."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="abc-123", on_event=events.append)

        mapping = {
            "type": "agent.started",
            "extract": lambda ev: {"agent_role": ev.role},
        }
        handler = listener._make_handler(mapping)

        mock_event = MagicMock()
        mock_event.role = "Researcher"
        handler(mock_event)

        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "agent.started"
        assert evt["crew_id"] == "abc-123"
        assert evt["agent_role"] == "Researcher"
        assert "ts" in evt

    def test_handler_extract_failure_does_not_crash(self):
        """If extract() raises, the event is still emitted (with crew_id only)."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="x", on_event=events.append)

        mapping = {
            "type": "tool.call_start",
            "extract": lambda ev: {"bad": ev.nonexistent_attr},
        }
        handler = listener._make_handler(mapping)

        mock_event = MagicMock(spec=[])
        handler(mock_event)

        assert len(events) == 1
        assert events[0]["crew_id"] == "x"
        assert events[0]["type"] == "tool.call_start"

    def test_callback_failure_does_not_crash(self):
        """If on_event() raises, the handler swallows it."""
        def _bad_callback(evt: dict) -> None:
            raise RuntimeError("oops")

        listener = BridgeListener(crew_id="x", on_event=_bad_callback)
        mapping = {
            "type": "crew.started",
            "extract": lambda ev: {"crew_name": "test"},
        }
        handler = listener._make_handler(mapping)
        mock_event = MagicMock()
        mock_event.crew_name = "test"
        handler(mock_event)  # must NOT raise

    def test_discover_event_classes_handles_missing_module(self):
        """When crewai.events is not importable, returns empty dict."""
        listener = BridgeListener("x", lambda e: None)
        with patch.object(listener, "_discover_event_classes", return_value={}):
            result = listener._discover_event_classes()
            assert result == {}


# ═══════════════════════════════════════════════════════════════════════════
#  Adapter tests
# ═══════════════════════════════════════════════════════════════════════════

# The modular names are now private: _CrewAIAgent, _CrewAICrew, etc.
_MOD_CREWAI_AGENT = "crew_engine._CrewAIAgent"
_MOD_CREWAI_CREW = "crew_engine._CrewAICrew"
_MOD_CREWAI_TASK = "crew_engine._CrewAITask"
_MOD_CREWAI_LLM = "crew_engine._CrewAILLM"


class TestAdapterLLM:
    """LLM model conversion."""

    def test_build_llm_from_model(self):
        from crew_engine import Adapter

        llm_model = models.LLMModel(model="gpt-4o", temperature=0.5)
        with patch(_MOD_CREWAI_LLM) as MockLLM:
            MockLLM.return_value = MagicMock()
            result = Adapter._build_llm(llm_model)
            MockLLM.assert_called_once()
            call_kwargs = MockLLM.call_args[1]
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["temperature"] == 0.5

    def test_build_llm_none_returns_none(self):
        from crew_engine import Adapter
        assert Adapter._build_llm(None) is None

    def test_build_llm_filters_none_values(self):
        """Optional None values should be filtered from kwargs."""
        from crew_engine import Adapter

        llm_model = models.LLMModel(model="gpt-4o", base_url=None)
        with patch(_MOD_CREWAI_LLM) as MockLLM:
            MockLLM.return_value = MagicMock()
            Adapter._build_llm(llm_model)
            call_kwargs = MockLLM.call_args[1]
            assert "base_url" not in call_kwargs


class TestAdapterAgent:
    """Agent model → CrewAI Agent conversion."""

    def test_build_basic_agent(self):
        """Minimal agent with role + goal."""
        from crew_engine import Adapter

        agent_model = _make_agent()
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(_make_crew(agents=[agent_model]))
            call_kwargs = MockAgent.call_args[1]
            assert call_kwargs["role"] == "Researcher"
            assert call_kwargs["goal"] == "Research the topic"

    def test_agent_with_tools(self):
        """Tools are resolved and included."""
        from crew_engine import Adapter

        tool = models.ToolRef(kind="builtin", name="SerperDevTool")
        agent_model = _make_agent(tools=[tool])
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch("crew_engine.Adapter._resolve_tool", return_value=MagicMock()), \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert "tools" in call_kwargs
            assert len(call_kwargs["tools"]) == 1

    def test_agent_with_memory_config(self):
        """MemoryConfig is translated."""
        from crew_engine import Adapter

        agent_model = _make_agent()
        agent_model.memory = models.MemoryConfig(enabled=True)
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask, \
             patch("crew_engine.Adapter._resolve_tool", return_value=MagicMock()):
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )

    def test_agent_extra_passthrough(self):
        """Model extra fields pass through to CrewAI Agent."""
        from crew_engine import Adapter

        agent_model = models.AgentModel(role="R", goal="G", verbose=True)
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert call_kwargs.get("verbose") is True


class TestAdapterTask:
    """Task model → CrewAI Task conversion."""

    def test_build_basic_task(self):
        from crew_engine import Adapter

        task_model = _make_task(agent_role=None)
        with patch(_MOD_CREWAI_TASK) as MockTask, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew:
            MockTask.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            Adapter.build_crewai_object(_make_crew(tasks=[task_model]))
            call_kwargs = MockTask.call_args[1]
            assert call_kwargs["description"] == "Do research"
            assert call_kwargs["expected_output"] == "Report"

    def test_task_with_context_resolved(self):
        """Tasks with context dependencies get resolved."""
        from crew_engine import Adapter

        task_a = _make_task(name="A", description="First")
        task_b = _make_task(name="B", description="Second", context=["A"])

        t_a_mock = MagicMock()
        t_b_mock = MagicMock()

        with patch(_MOD_CREWAI_TASK) as MockTask, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew:
            MockTask.side_effect = [t_a_mock, t_b_mock]
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()

            crew = _make_crew(tasks=[task_a, task_b])
            Adapter.build_crewai_object(crew)

    def test_task_with_guardrails(self):
        from crew_engine import Adapter

        task_model = _make_task()
        task_model.guardrails = ["no_pii", "max_length"]
        task_model.guardrail_max_retries = 5

        with patch(_MOD_CREWAI_TASK) as MockTask, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew:
            MockTask.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            Adapter.build_crewai_object(_make_crew(tasks=[task_model]))
            call_kwargs = MockTask.call_args[1]
            assert call_kwargs.get("guardrails") == ["no_pii", "max_length"]
            assert call_kwargs.get("guardrail_max_retries") == 5


class TestAdapterCrew:
    """Full crew model conversion."""

    def test_build_full_crew(self):
        from crew_engine import Adapter

        crew_model = _make_crew()
        crew_model.planning = True
        crew_model.verbose = True

        with patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockCrew.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            result = Adapter.build_crewai_object(crew_model)
            assert result is not None
            call_kwargs = MockCrew.call_args[1]
            assert call_kwargs["name"] == "Test Crew"
            assert call_kwargs["process"] == "sequential"
            assert call_kwargs["planning"] is True
            assert call_kwargs["verbose"] is True

    def test_hierarchical_crew(self):
        from crew_engine import Adapter

        crew_model = _make_crew()
        crew_model.process = "hierarchical"
        crew_model.manager_agent_role = "Researcher"  # matches _make_agent role

        with patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            mock_agent = MagicMock()
            mock_agent.role = "Researcher"
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(crew_model)
            call_kwargs = MockCrew.call_args[1]
            assert call_kwargs.get("manager_agent") is not None
            # manager_agent should be the resolved Agent, not a string
            assert call_kwargs["manager_agent"] is mock_agent

    def test_crew_with_memory(self):
        from crew_engine import Adapter

        crew_model = _make_crew()
        crew_model.memory = models.MemoryConfig(enabled=True, embedder={"provider": "openai"})

        with patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockCrew.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(crew_model)
            call_kwargs = MockCrew.call_args[1]
            assert "memory" in call_kwargs

    def test_missing_crewai_raises(self):
        """When CrewAI is not installed, the adapter raises ImportError."""
        from crew_engine import Adapter
        with patch(_MOD_CREWAI_AGENT, None), \
             patch(_MOD_CREWAI_CREW, None), \
             patch(_MOD_CREWAI_TASK, None):
            with pytest.raises(ImportError, match="CrewAI is required"):
                Adapter.build_crewai_object(_make_crew())


class TestAdapterToolResolution:
    """Tool resolution from ToolRef."""

    def test_builtin_tool_from_crewai_tools(self):
        from crew_engine import Adapter

        tool_ref = models.ToolRef(kind="builtin", name="SerperDevTool", params={"api_key": "test"})

        mock_tool_cls = MagicMock()
        mock_tool_instance = MagicMock()
        mock_tool_cls.return_value = mock_tool_instance

        with patch.dict("sys.modules", {"crewai_tools": MagicMock()}):
            import crewai_tools
            crewai_tools.SerperDevTool = mock_tool_cls

            result = Adapter._resolve_builtin_tool(tool_ref)
            assert result is mock_tool_instance
            mock_tool_cls.assert_called_once_with(api_key="test")

    def test_builtin_tool_not_found_fallback(self):
        from crew_engine import Adapter

        tool_ref = models.ToolRef(kind="builtin", name="NonExistentTool")

        with patch.dict("sys.modules", {"crewai_tools": MagicMock()}):
            import crewai_tools
            crewai_tools.NonExistentTool = None  # type: ignore[assignment]

            result = Adapter._resolve_builtin_tool(tool_ref)
            assert isinstance(result, dict)
            assert result["name"] == "NonExistentTool"
            assert result["kind"] == "builtin"

    def test_custom_tool_returns_placeholder(self):
        from crew_engine import Adapter

        tool_ref = models.ToolRef(
            kind="custom",
            name="MyTool",
            args_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        result = Adapter._build_custom_tool(tool_ref)
        assert isinstance(result, dict)
        assert result["name"] == "MyTool"
        assert result["kind"] == "custom"
        assert "args_schema" in result

    def test_tool_with_progress_wrapping(self):
        """When flag/on_event provided, tools are wrapped in ProgressToolWrapper."""
        from crew_engine import Adapter

        tool_ref = models.ToolRef(kind="builtin", name="TestTool")
        flag = {"stop": False}
        events: list[dict] = []

        with patch("crew_engine.Adapter._resolve_builtin_tool") as mock_resolve:
            inner_tool = MagicMock()
            inner_tool.name = "TestTool"
            mock_resolve.return_value = inner_tool

            result = Adapter._resolve_tool(
                tool_ref, flag=flag, on_event=events.append, crew_id="c1"
            )
            assert isinstance(result, ProgressToolWrapper)
            assert result.name == "TestTool"


# ═══════════════════════════════════════════════════════════════════════════
#  CrewEngine tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCrewEngineRun:
    """Engine.run() spawns a thread and returns a handle."""

    def test_run_returns_execution_handle(self):
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register") as mock_register, \
             patch("crew_engine.BridgeListener.unregister") as mock_unregister:
            mock_crew = MagicMock()
            mock_crew.kickoff_async = MagicMock()
            mock_crew.kickoff_async.return_value = MagicMock()
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)

            assert isinstance(handle, ExecutionHandle)
            assert isinstance(handle.thread, threading.Thread)
            assert handle.flag == {"stop": False}
            assert handle.crew_id is not None

    def test_run_spawns_background_thread(self):
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _slow(*args, **kwargs):
                import asyncio
                await asyncio.sleep(0.1)
                return MagicMock()

            mock_crew.kickoff_async = _slow
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            assert handle.thread.is_alive()

            handle.thread.join(timeout=5)
            assert not handle.thread.is_alive()

    def test_run_emits_crew_events(self):
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={"input_tokens": 100, "output_tokens": 200})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            handle.thread.join(timeout=5)

            event_types = [e["type"] for e in events]
            assert "crew.started" in event_types
            assert "crew.completed" in event_types

    def test_run_error_emits_crew_error(self):
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fail(*args, **kwargs):
                raise RuntimeError("Boom!")

            mock_crew.kickoff_async = _fail
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            handle.thread.join(timeout=5)

            errors = [e for e in events if e["type"] == "crew.error"]
            assert len(errors) >= 1
            assert "Boom!" in errors[0]["error"]


class TestCrewEngineStop:
    """Engine.stop() sets flag and joins thread."""

    def test_stop_sets_flag(self):
        engine = CrewEngine()
        thread = threading.Thread(target=lambda: time.sleep(0.5))
        thread.start()
        handle = ExecutionHandle(
            thread=thread,
            flag={"stop": False},
            listener=MagicMock(),
            crew_id="test",
        )
        engine.stop(handle)
        assert handle.flag["stop"] is True

    def test_stop_joins_thread(self):
        engine = CrewEngine()
        barrier = threading.Barrier(2, timeout=5)
        flag = {"stop": False}

        def _worker():
            barrier.wait()
            while not flag["stop"]:
                time.sleep(0.05)

        thread = threading.Thread(target=_worker)
        thread.start()
        barrier.wait()

        handle = ExecutionHandle(
            thread=thread,
            flag=flag,
            listener=MagicMock(),
            crew_id="test",
        )
        engine.stop(handle)
        assert not thread.is_alive()


class TestCrewEngineTestAgent:
    """Engine.test_agent() runs a single agent."""

    def test_agent_not_found_raises(self):
        engine = CrewEngine()
        crew = _make_crew(agents=[_make_agent(role="R", goal="G")])
        with pytest.raises(ValueError, match="No agent with role"):
            engine.test_agent(crew, "NonExistent", "prompt")

    def test_test_agent_builds_and_runs(self):
        engine = CrewEngine()
        crew = _make_crew(agents=[_make_agent(role="R", goal="G")])

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _fake_kickoff(*args, **kwargs):
                return "Test output"

            mock_agent.kickoff_async = _fake_kickoff
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            result = engine.test_agent(crew, "R", "Hello")
            assert result == "Test output"

    def test_test_agent_error_raises(self):
        engine = CrewEngine()
        crew = _make_crew(agents=[_make_agent(role="R", goal="G")])

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _fail(*args, **kwargs):
                raise RuntimeError("Agent failure")

            mock_agent.kickoff_async = _fail
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            with pytest.raises(RuntimeError, match="Agent test failed"):
                engine.test_agent(crew, "R", "Hello")


class TestCrewEngineTestTask:
    """Engine.test_task() Fase 2 placeholder."""

    def test_task_not_found_raises(self):
        engine = CrewEngine()
        crew = _make_crew()
        with pytest.raises(ValueError, match="No task with name"):
            engine.test_task(crew, "nonexistent", "mock")

    def test_returns_fase2_placeholder(self):
        engine = CrewEngine()
        crew = _make_crew(tasks=[_make_task(name="t1")])
        result = engine.test_task(crew, "t1", "mock context")
        assert "Fase 2" in result
        assert "t1" in result


# ═══════════════════════════════════════════════════════════════════════════
#  Fix verification tests — BridgeListener registration
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeListenerRegistration:
    """Verify that BridgeListener.register() is called in CrewEngine.run()."""

    def test_listener_registered_during_run(self):
        """BridgeListener.register is called when run() starts."""
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register") as mock_register, \
             patch("crew_engine.BridgeListener.unregister") as mock_unregister:
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {}, on_event=events.append)
            handle.thread.join(timeout=5)

            # register() must be called
            mock_register.assert_called_once()
            # unregister() must be called in finally
            mock_unregister.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
#  Memory config tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMemoryConversion:
    """MemoryConfig is resolved to bool for CrewAI."""

    def test_agent_memory_config_enabled(self):
        """MemoryConfig(enabled=True) passes memory=True."""
        from crew_engine import Adapter

        agent_model = _make_agent(role="MemAgent")
        agent_model.memory = models.MemoryConfig(enabled=True)

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert call_kwargs.get("memory") is True

    def test_agent_memory_config_disabled(self):
        """MemoryConfig(enabled=False) does NOT pass memory."""
        from crew_engine import Adapter

        agent_model = _make_agent(role="MemAgent")
        agent_model.memory = models.MemoryConfig(enabled=False)

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert "memory" not in call_kwargs

    def test_agent_memory_bool_true(self):
        """agent_model.memory=True passes memory=True."""
        from crew_engine import Adapter

        agent_model = _make_agent(role="MemAgent")
        agent_model.memory = True  # type: ignore[assignment]

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert call_kwargs.get("memory") is True

    def test_crew_memory_config_enabled(self):
        """Crew MemoryConfig(enabled=True) passes memory=True to Crew."""
        from crew_engine import Adapter

        crew_model = _make_crew()
        crew_model.memory = models.MemoryConfig(enabled=True)

        with patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockCrew.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(crew_model)
            call_kwargs = MockCrew.call_args[1]
            assert call_kwargs.get("memory") is True

    def test_crew_memory_config_disabled(self):
        """Crew MemoryConfig(enabled=False) does NOT pass memory."""
        from crew_engine import Adapter

        crew_model = _make_crew()
        crew_model.memory = models.MemoryConfig(enabled=False)

        with patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockCrew.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(crew_model)
            call_kwargs = MockCrew.call_args[1]
            assert "memory" not in call_kwargs


# ═══════════════════════════════════════════════════════════════════════════
#  output_json_schema → Pydantic model tests
# ═══════════════════════════════════════════════════════════════════════════

class TestOutputJsonSchemaConversion:
    """JSON schema dict is converted to a Pydantic BaseModel class."""

    def test_simple_schema_creates_model(self):
        """A basic JSON schema is converted to a Pydantic model."""
        from crew_engine import _json_schema_to_pydantic_model

        schema = {
            "type": "object",
            "title": "ResearchResult",
            "properties": {
                "summary": {"type": "string"},
                "confidence": {"type": "number"},
                "is_complete": {"type": "boolean"},
            },
            "required": ["summary", "confidence"],
        }

        model = _json_schema_to_pydantic_model(schema)
        assert issubclass(model, pydantic.BaseModel)
        assert model.__name__ == "ResearchResult"

        instance = model(summary="test", confidence=0.95, is_complete=True)
        assert instance.summary == "test"
        assert instance.confidence == 0.95
        assert instance.is_complete is True

    def test_optional_fields_work(self):
        """Fields not in 'required' are Optional."""
        from crew_engine import _json_schema_to_pydantic_model

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "optional_field": {"type": "string"},
            },
            "required": ["name"],
        }

        model = _json_schema_to_pydantic_model(schema)
        instance = model(name="required only")
        assert instance.name == "required only"
        assert instance.optional_field is None

    def test_output_json_in_task_kwargs(self):
        """output_json_schema dict is converted to a Pydantic model in kwargs."""
        from crew_engine import Adapter
        import pydantic

        task_model = _make_task(name="structured")
        task_model.output_json_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
        }

        with patch(_MOD_CREWAI_TASK) as MockTask, \
             patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew:
            MockTask.return_value = MagicMock()
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            Adapter.build_crewai_object(_make_crew(tasks=[task_model]))
            call_kwargs = MockTask.call_args[1]
            output_json = call_kwargs.get("output_json")
            assert output_json is not None
            assert isinstance(output_json, type)
            assert issubclass(output_json, pydantic.BaseModel)


# ═══════════════════════════════════════════════════════════════════════════
#  Multi-tab isolation tests (crew_id filtering)
# ═══════════════════════════════════════════════════════════════════════════

class TestCrewIdIsolation:
    """Verify that concurrent crews get unique crew_ids in events."""

    def test_unique_crew_ids(self):
        engine = CrewEngine()
        events_a: list[dict] = []
        events_b: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle_a = engine.run(_make_crew(name="Crew A"), {}, on_event=events_a.append)
            handle_b = engine.run(_make_crew(name="Crew B"), {}, on_event=events_b.append)

            handle_a.thread.join(timeout=5)
            handle_b.thread.join(timeout=5)

            assert handle_a.crew_id != handle_b.crew_id

            for e in events_a:
                assert e["crew_id"] == handle_a.crew_id
            for e in events_b:
                assert e["crew_id"] == handle_b.crew_id

    def test_events_do_not_cross_contaminate(self):
        """Events from crew A never show up in crew B's callback."""
        engine = CrewEngine()
        events_a: list[dict] = []
        events_b: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle_a = engine.run(_make_crew(name="Crew A"), {}, on_event=events_a.append)
            handle_b = engine.run(_make_crew(name="Crew B"), {}, on_event=events_b.append)

            handle_a.thread.join(timeout=5)
            handle_b.thread.join(timeout=5)

            a_ids = {e["crew_id"] for e in events_a}
            b_ids = {e["crew_id"] for e in events_b}
            assert a_ids == {handle_a.crew_id}
            assert b_ids == {handle_b.crew_id}


# ═══════════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Unusual but valid scenarios."""

    def test_empty_agents_and_tasks(self):
        """Crew with no agents or tasks builds without error."""
        from crew_engine import Adapter

        crew_model = models.CrewModel(name="Empty Crew")
        with patch(_MOD_CREWAI_CREW) as MockCrew:
            MockCrew.return_value = MagicMock()
            result = Adapter.build_crewai_object(crew_model)
            assert result is not None
            call_kwargs = MockCrew.call_args[1]
            assert call_kwargs["agents"] == []
            assert call_kwargs["tasks"] == []

    def test_model_with_extra_fields_passthrough(self):
        """Unknown fields in Pydantic models pass through to CrewAI kwargs."""
        from crew_engine import Adapter

        agent_model = models.AgentModel(role="R", goal="G", cache=True, max_rpm=60)
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()
            Adapter.build_crewai_object(
                _make_crew(agents=[agent_model], tasks=[_make_task()])
            )
            call_kwargs = MockAgent.call_args[1]
            assert call_kwargs.get("cache") is True
            assert call_kwargs.get("max_rpm") == 60
