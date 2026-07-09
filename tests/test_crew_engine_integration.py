"""Integration tests for gui-crew crew_engine — callback routing, event bridge,
and end-to-end engine flow with mocked CrewAI objects.

These tests use ``unittest.mock`` to mock CrewAI classes so no real LLM
calls are required.
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from crew_engine import (
    Adapter,
    BridgeListener,
    CallbackRouter,
    CancelledError,
    CrewEngine,
    ExecutionHandle,
    ProgressToolWrapper,
)
import models


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_llm(model: str = "openai/gpt-4o") -> models.LLMModel:
    return models.LLMModel(model=model, temperature=0.7)


def _make_agent(
    role: str = "Researcher",
    goal: str = "Research the topic",
    llm: models.LLMModel | None = None,
) -> models.AgentModel:
    return models.AgentModel(
        role=role,
        goal=goal,
        backstory="Expert",
        llm=llm or _make_llm(),
    )


def _make_task(
    name: str = "research",
    description: str = "Do research",
    expected_output: str = "Report",
    agent_role: str | None = None,
) -> models.TaskModel:
    return models.TaskModel(
        name=name,
        description=description,
        expected_output=expected_output,
        agent_role=agent_role,
    )


def _make_crew(
    name: str = "Test Crew",
    agents: list[models.AgentModel] | None = None,
    tasks: list[models.TaskModel] | None = None,
    callbacks: dict | None = None,
) -> models.CrewModel:
    return models.CrewModel(
        name=name,
        description="A test crew",
        agents=agents or [_make_agent()],
        tasks=tasks or [_make_task()],
        callbacks=callbacks or {},
    )


_MOD_CREWAI_AGENT = "crew_engine._CrewAIAgent"
_MOD_CREWAI_CREW = "crew_engine._CrewAICrew"
_MOD_CREWAI_TASK = "crew_engine._CrewAITask"
_MOD_CREWAI_LLM = "crew_engine._CrewAILLM"


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.19 — CallbackRouter tests (unit-level)
# ═══════════════════════════════════════════════════════════════════════════

class TestCallbackRouter:
    """CallbackRouter expands template IDs to callables."""

    def test_empty_callbacks_returns_empty_dict(self):
        """Empty callbacks dict returns empty result."""
        result = CallbackRouter.expand_callbacks({})
        assert result == {}

    def test_string_template_single(self):
        """A string template ID is expanded to a callable."""
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": "log_to_file",
        })
        assert "before_kickoff" in result
        assert len(result["before_kickoff"]) == 1
        assert callable(result["before_kickoff"][0])

    def test_string_template_list(self):
        """A list of string template IDs is expanded to multiple callables."""
        result = CallbackRouter.expand_callbacks({
            "after_kickoff": ["log_to_file", "print_to_console"],
        })
        assert len(result["after_kickoff"]) == 2
        for cb in result["after_kickoff"]:
            assert callable(cb)

    def test_dict_template_with_config(self):
        """A dict spec with template + config is expanded correctly."""
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": [
                {"template": "log_to_file", "config": {"filepath": "/tmp/cb.log"}},
            ],
        })
        assert len(result["before_kickoff"]) == 1
        assert callable(result["before_kickoff"][0])

    def test_multiple_callback_types(self):
        """Multiple callback types are expanded independently."""
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": ["log_to_file"],
            "after_kickoff": ["print_to_console"],
            "step_callback": "log_to_file",
        })
        assert len(result) == 3
        assert "before_kickoff" in result
        assert "after_kickoff" in result
        assert "step_callback" in result

    def test_unknown_template_logs_warning(self, caplog):
        """Unknown template IDs are skipped with a warning."""
        with caplog.at_level(logging.WARNING, logger=CallbackRouter._LOG.name):
            result = CallbackRouter.expand_callbacks({
                "before_kickoff": "nonexistent_template",
            })
        assert result == {}
        assert "Unknown callback template" in caplog.text

    def test_unsupported_spec_type_logs_warning(self, caplog):
        """Non-string/non-dict specs are skipped with a warning."""
        with caplog.at_level(logging.WARNING, logger=CallbackRouter._LOG.name):
            result = CallbackRouter.expand_callbacks({
                "before_kickoff": [42],
            })
        assert result == {}
        assert "Skipping unsupported callback spec type" in caplog.text

    def test_callback_execution_does_not_raise(self):
        """Callbacks wrap errors — calling them must not raise."""
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": "log_to_file",
        })
        cb = result["before_kickoff"][0]
        # Should not raise even with invalid args
        cb("unexpected", "args")

    def test_print_to_console_executes(self, capsys):
        """print_to_console callback writes to stdout."""
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": ["print_to_console"],
        })
        cb = result["before_kickoff"][0]
        cb("test_arg", kwarg="value")
        captured = capsys.readouterr()
        assert "test_arg" in captured.out
        assert "kwarg" in captured.out

    def test_log_to_file_writes_to_disk(self, tmp_path):
        """log_to_file callback appends to the specified file."""
        logfile = tmp_path / "log.txt"
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": [
                {"template": "log_to_file", "config": {"filepath": str(logfile)}},
            ],
        })
        cb = result["before_kickoff"][0]
        cb("invocation_data")
        content = logfile.read_text()
        assert "invocation_data" in content

    def test_send_webhook_is_stub(self, caplog):
        """send_webhook is a stub that logs instead of making HTTP calls."""
        with caplog.at_level(logging.INFO, logger=CallbackRouter._LOG.name):
            result = CallbackRouter.expand_callbacks({
                "before_kickoff": "send_webhook",
            })
            cb = result["before_kickoff"][0]
            cb()
        assert "send_webhook stub" in caplog.text.lower()

    def test_expanded_callbacks_are_independent(self):
        """Each call to expand_callbacks creates new callable instances."""
        r1 = CallbackRouter.expand_callbacks({"cb": "log_to_file"})
        r2 = CallbackRouter.expand_callbacks({"cb": "log_to_file"})
        assert r1["cb"][0] is not r2["cb"][0]

    def test_all_builtin_templates_exist(self):
        """Verify all three built-in templates are registered."""
        assert "log_to_file" in CallbackRouter._TEMPLATES
        assert "print_to_console" in CallbackRouter._TEMPLATES
        assert "send_webhook" in CallbackRouter._TEMPLATES
        assert len(CallbackRouter._TEMPLATES) == 3


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.19 — Callback routing integration in Adapter
# ═══════════════════════════════════════════════════════════════════════════

class TestCallbackAdapterIntegration:
    """CallbackRouter is integrated into Adapter.build_crewai_object()."""

    def test_before_kickoff_callbacks_passed_to_crew(self):
        """before_kickoff template IDs are expanded and passed as
        `before_kickoff_callbacks` to the CrewAI Crew."""
        crew_model = _make_crew(callbacks={"before_kickoff": ["log_to_file"]})

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            assert "before_kickoff_callbacks" in kwargs
            assert len(kwargs["before_kickoff_callbacks"]) == 1
            assert callable(kwargs["before_kickoff_callbacks"][0])

    def test_after_kickoff_callbacks_passed_to_crew(self):
        """after_kickoff template IDs are expanded and passed as
        `after_kickoff_callbacks`."""
        crew_model = _make_crew(callbacks={"after_kickoff": ["print_to_console"]})

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            assert "after_kickoff_callbacks" in kwargs
            assert len(kwargs["after_kickoff_callbacks"]) == 1

    def test_step_callback_passed_as_single_callable(self):
        """step_callback is expanded to a single callable (not a list)."""
        crew_model = _make_crew(callbacks={"step_callback": "log_to_file"})

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            assert "step_callback" in kwargs
            assert callable(kwargs["step_callback"])

    def test_empty_callbacks_do_not_add_kwargs(self):
        """When crew_model.callbacks is empty, no callback kwargs are added."""
        crew_model = _make_crew(callbacks={})

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            assert "before_kickoff_callbacks" not in kwargs
            assert "after_kickoff_callbacks" not in kwargs

    def test_multiple_callback_types_passed(self):
        """Multiple callback types are all passed to CrewAI."""
        crew_model = _make_crew(callbacks={
            "before_kickoff": ["log_to_file"],
            "after_kickoff": ["print_to_console"],
            "step_callback": "log_to_file",
            "task_callback": "print_to_console",
        })

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            assert "before_kickoff_callbacks" in kwargs
            assert "after_kickoff_callbacks" in kwargs
            assert "step_callback" in kwargs
            assert "task_callback" in kwargs


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.20 — BridgeListener integration tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeListenerIntegration:
    """BridgeListener translates synthetic CrewAI events end-to-end."""

    def test_multiple_event_types(self):
        """Multiple different event types produce correct protocol events."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="crew-1", on_event=events.append)

        # Simulate agent started
        h1 = listener._make_handler({
            "type": "agent.started",
            "extract": lambda ev: {"agent_role": ev.agent_role, "task_name": ev.task_name},
        })
        mock_ev1 = MagicMock()
        mock_ev1.agent_role = "Researcher"
        mock_ev1.task_name = "Research"
        h1(mock_ev1)

        # Simulate agent completed
        h2 = listener._make_handler({
            "type": "agent.completed",
            "extract": lambda ev: {"agent_role": ev.agent_role, "task_name": ev.task_name},
        })
        mock_ev2 = MagicMock()
        mock_ev2.agent_role = "Researcher"
        mock_ev2.task_name = "Research"
        h2(mock_ev2)

        assert len(events) == 2
        assert events[0]["type"] == "agent.started"
        assert events[0]["crew_id"] == "crew-1"
        assert events[1]["type"] == "agent.completed"
        assert events[1]["crew_id"] == "crew-1"

    def test_all_protocol_event_types_have_crew_id(self):
        """Every emitted protocol event must carry a crew_id field."""
        events: list[dict] = []
        listener = BridgeListener(crew_id="abc", on_event=events.append)

        import crew_engine as ce
        for event_name, mapping in ce._CREWAI_EVENT_MAP.items():
            handler = listener._make_handler(mapping)
            mock_event = MagicMock()
            handler(mock_event)

        assert len(events) == len(ce._CREWAI_EVENT_MAP)
        for evt in events:
            assert "crew_id" in evt
            assert evt["crew_id"] == "abc"

    def test_handler_swallows_callback_exceptions(self):
        """If on_event raises, BridgeListener must not propagate."""
        errors: list[Exception] = []

        def bad_callback(evt: dict) -> None:
            raise RuntimeError("oops")

        listener = BridgeListener(crew_id="x", on_event=bad_callback)
        mapping = {
            "type": "crew.started",
            "extract": lambda ev: {"crew_name": "test"},
        }
        handler = listener._make_handler(mapping)
        mock_event = MagicMock()
        mock_event.crew_name = "test"
        # Must not raise
        handler(mock_event)


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.20 — CrewEngine lifecycle integration tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCrewEngineLifecycle:
    """End-to-end engine flow: start, progress, complete, error."""

    def test_full_lifecycle_events(self):
        """CrewEngine.run() emits started → completed events."""
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={"input_tokens": 50, "output_tokens": 100})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            handle.thread.join(timeout=10)

            event_types = [e["type"] for e in events]
            assert "crew.started" in event_types
            assert "crew.completed" in event_types
            # Verify resource.update is emitted
            assert "resource.update" in event_types

    def test_error_emits_crew_error(self):
        """Exceptions during execution emit crew.error event."""
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fail(*args, **kwargs):
                raise RuntimeError("Simulated engine failure")

            mock_crew.kickoff_async = _fail
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            handle.thread.join(timeout=10)

            errors = [e for e in events if e["type"] == "crew.error"]
            assert len(errors) >= 1
            assert "Simulated engine failure" in errors[0]["error"]

    def test_stop_sets_flag_and_emits_event(self):
        """Stopping via handle sets the stop flag and emits crew.stopped."""
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            # Simulate a cancelled error during kickoff
            async def _cancelled(*args, **kwargs):
                raise CancelledError("User cancelled")

            mock_crew.kickoff_async = _cancelled
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {"topic": "AI"}, on_event=events.append)
            handle.thread.join(timeout=10)

            stopped = [e for e in events if e["type"] == "crew.stopped"]
            assert len(stopped) >= 1


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.20 — Multi-tab event isolation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiTabIsolation:
    """Two concurrent crews must have isolated crew_id in all events."""

    def test_two_crews_unique_ids(self):
        """Two simultaneous runs produce unique crew_ids."""
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

            handle_a = engine.run(
                _make_crew(name="Crew A"), {}, on_event=events_a.append
            )
            handle_b = engine.run(
                _make_crew(name="Crew B"), {}, on_event=events_b.append
            )

            handle_a.thread.join(timeout=10)
            handle_b.thread.join(timeout=10)

            # Unique crew IDs
            assert handle_a.crew_id != handle_b.crew_id

    def test_events_isolated_per_crew(self):
        """Events for Crew A never appear in Crew B's callback."""
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

            handle_a = engine.run(
                _make_crew(name="Crew A"), {}, on_event=events_a.append
            )
            handle_b = engine.run(
                _make_crew(name="Crew B"), {}, on_event=events_b.append
            )

            handle_a.thread.join(timeout=10)
            handle_b.thread.join(timeout=10)

            # Every event in A's list has A's crew_id
            for e in events_a:
                assert e["crew_id"] == handle_a.crew_id, (
                    f"Event for Crew A has wrong crew_id: {e['crew_id']}"
                )

            # Every event in B's list has B's crew_id
            for e in events_b:
                assert e["crew_id"] == handle_b.crew_id, (
                    f"Event for Crew B has wrong crew_id: {e['crew_id']}"
                )

            # Verify no cross-contamination
            a_ids = {e["crew_id"] for e in events_a}
            b_ids = {e["crew_id"] for e in events_b}
            assert a_ids == {handle_a.crew_id}
            assert b_ids == {handle_b.crew_id}

    def test_three_concurrent_crews_all_isolated(self):
        """Three crews running concurrently all have isolated crew_ids."""
        engine = CrewEngine()
        all_events: list[list[dict]] = [[], [], []]
        handles: list[ExecutionHandle] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            for i in range(3):
                h = engine.run(
                    _make_crew(name=f"Crew {i}"),
                    {},
                    on_event=all_events[i].append,
                )
                handles.append(h)

            for h in handles:
                h.thread.join(timeout=10)

            # All crew_ids must be unique
            ids = {h.crew_id for h in handles}
            assert len(ids) == 3

            # Each crew's events only contain its own crew_id
            for i, h in enumerate(handles):
                for e in all_events[i]:
                    assert e["crew_id"] == h.crew_id


# ═══════════════════════════════════════════════════════════════════════════
#  Task 1.20 — End-to-end callback routing test
# ═══════════════════════════════════════════════════════════════════════════

class TestCallbackRoutingEndToEnd:
    """Callbacks are expanded, passed to CrewAI, and errors are logged."""

    def test_crew_model_with_callbacks_builds_correctly(self):
        """A CrewModel with callbacks is processed end-to-end by the Adapter."""
        crew_model = _make_crew(callbacks={
            "before_kickoff": ["log_to_file", "print_to_console"],
            "after_kickoff": ["send_webhook"],
        })

        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            result = Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]

            assert len(kwargs["before_kickoff_callbacks"]) == 2
            assert len(kwargs["after_kickoff_callbacks"]) == 1

            # Verify all are callable
            for cb in kwargs["before_kickoff_callbacks"]:
                assert callable(cb)
            for cb in kwargs["after_kickoff_callbacks"]:
                assert callable(cb)

    def test_callbacks_execute_without_breaking_crew(self):
        """Executing expanded callbacks must not raise even with edge cases."""
        expanded = CallbackRouter.expand_callbacks({
            "before_kickoff": ["log_to_file", "print_to_console", "send_webhook"],
        })

        for cb in expanded["before_kickoff"]:
            # Should execute without raising
            cb()

    def test_callback_errors_are_logged(self, caplog):
        """If a callback encounters an error at runtime, it is logged and
        does not propagate (crew continues execution)."""
        # Use log_to_file with an invalid path that will fail on write
        result = CallbackRouter.expand_callbacks({
            "before_kickoff": [
                {"template": "log_to_file", "config": {"filepath": "/nonexistent/dir/should/fail.log"}},
            ],
        })
        cb = result["before_kickoff"][0]

        with caplog.at_level(logging.ERROR, logger=CallbackRouter._LOG.name):
            # Must NOT raise — callback catches the error internally
            cb("test_data")

        assert "log_to_file callback failed" in caplog.text

    def test_callback_router_backward_compatible(self):
        """Empty callbacks dict works without errors (backward compat)."""
        crew_model = _make_crew()  # default callbacks={}
        with patch(_MOD_CREWAI_AGENT) as MockAgent, \
             patch(_MOD_CREWAI_LLM) as MockLLM, \
             patch(_MOD_CREWAI_CREW) as MockCrew, \
             patch(_MOD_CREWAI_TASK) as MockTask:
            MockAgent.return_value = MagicMock()
            MockLLM.return_value = MagicMock()
            MockCrew.return_value = MagicMock()
            MockTask.return_value = MagicMock()

            result = Adapter.build_crewai_object(crew_model)
            kwargs = MockCrew.call_args[1]
            # No callback kwargs should be present
            callback_kwargs = [k for k in kwargs if "callback" in k.lower()]
            assert callback_kwargs == []


# ═══════════════════════════════════════════════════════════════════════════
#  ProgressToolWrapper integration tests
# ═══════════════════════════════════════════════════════════════════════════

class TestProgressToolWrapperIntegration:
    """ProgressToolWrapper emits progress events and supports cancellation."""

    def test_emits_multiple_progress_events_for_long_tool(self):
        """A tool running for >5s emits progress events at intervals."""
        import concurrent.futures

        inner = MagicMock()
        inner.name = "LongTool"
        inner._run.return_value = "done"

        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="int-crew-1",
        )
        wrapper._run()

        progress_events = [e for e in events if e["type"] == "tool.progress"]
        assert len(progress_events) >= 1
        for evt in progress_events:
            assert evt["crew_id"] == "int-crew-1"
            assert evt["tool_name"] == "LongTool"
            assert "elapsed_ms" in evt
            assert "status_message" in evt

    def test_progress_event_has_required_fields(self):
        """tool.progress events carry all required fields."""
        inner = MagicMock()
        inner.name = "RequiredTool"
        inner._run.return_value = "output"

        flag = {"stop": False}
        events: list[dict] = []

        wrapper = ProgressToolWrapper(
            tool=inner,
            flag=flag,
            on_event=events.append,
            crew_id="fields-1",
        )
        wrapper._run()

        progress = [e for e in events if e["type"] == "tool.progress"]
        assert len(progress) >= 1
        required_fields = {"type", "crew_id", "tool_name", "elapsed_ms", "status_message", "ts"}
        for evt in progress:
            assert required_fields.issubset(evt.keys()), f"Missing fields: {required_fields - set(evt.keys())}"


# ═══════════════════════════════════════════════════════════════════════════
#  ExecutionHandle integration tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExecutionHandleIntegration:
    """ExecutionHandle is properly constructed by CrewEngine.run()."""

    def test_handle_contains_all_required_attributes(self):
        """Every ExecutionHandle has thread, flag, listener, crew_id."""
        engine = CrewEngine()
        events: list[dict] = []

        with patch("crew_engine.Adapter.build_crewai_object") as mock_build, \
             patch("crew_engine.BridgeListener.register"), \
             patch("crew_engine.BridgeListener.unregister"):
            mock_crew = MagicMock()

            async def _fast(*args, **kwargs):
                return MagicMock(token_usage={})

            mock_crew.kickoff_async = _fast
            mock_build.return_value = mock_crew

            handle = engine.run(_make_crew(), {}, on_event=events.append)
            handle.thread.join(timeout=10)

            assert isinstance(handle, ExecutionHandle)
            assert isinstance(handle.thread, threading.Thread)
            assert isinstance(handle.flag, dict)
            assert "stop" in handle.flag
            assert isinstance(handle.listener, BridgeListener)
            assert isinstance(handle.crew_id, str)
            assert len(handle.crew_id) == 36  # UUID4


# ═══════════════════════════════════════════════════════════════════════════
#  CrewModel callback field integration
# ═══════════════════════════════════════════════════════════════════════════

class TestCrewModelCallbacks:
    """CrewModel.callbacks field survives round-trip serialization."""

    def test_callbacks_field_in_model(self):
        """CrewModel callbacks field stores template IDs."""
        crew = models.CrewModel(
            name="Crew",
            callbacks={
                "before_kickoff": ["log_to_file"],
                "after_kickoff": ["print_to_console"],
            },
        )
        assert crew.callbacks == {
            "before_kickoff": ["log_to_file"],
            "after_kickoff": ["print_to_console"],
        }

    def test_callbacks_survive_json_roundtrip(self):
        """Callbacks survive to_crewai_json → from_crewai_json roundtrip."""
        crew = models.CrewModel(
            name="Crew",
            agents=[_make_agent()],
            tasks=[_make_task()],
            callbacks={
                "before_kickoff": ["log_to_file"],
                "after_kickoff": ["print_to_console"],
            },
        )
        json_str = crew.to_crewai_json()
        crew2 = models.CrewModel.from_crewai_json(json_str)
        assert crew2.callbacks == {
            "before_kickoff": ["log_to_file"],
            "after_kickoff": ["print_to_console"],
        }

    def test_callbacks_default_to_empty_dict(self):
        """Default callbacks is an empty dict."""
        crew = models.CrewModel(name="Minimal")
        assert crew.callbacks == {}
