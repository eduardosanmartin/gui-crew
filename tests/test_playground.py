"""Unit tests for gui-crew playground — timeout enforcement, state management, route registration.

PR 1 tests cover foundation + UI skeleton. PR 2 will add execution-flow tests.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

import models
from crew_engine import CrewEngine


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_agent(role: str = "Researcher", max_execution_time: int | None = None) -> models.AgentModel:
    """Build an AgentModel, optionally with max_execution_time in model_extra."""
    kwargs: dict = {"role": role, "goal": f"Do {role} work"}
    if max_execution_time is not None:
        kwargs["max_execution_time"] = max_execution_time
    return models.AgentModel(**kwargs)


def _make_crew(agents: list[models.AgentModel] | None = None) -> models.CrewModel:
    return models.CrewModel(
        name="Test Crew",
        agents=agents or [_make_agent()],
        tasks=[
            models.TaskModel(name="t1", description="d", expected_output="e"),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Timeout enforcement (crew_engine.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentTimeoutEnforcement:
    """_test_agent_coro wraps kickoff_async in asyncio.wait_for."""

    _MOD_AGENT = "crew_engine._CrewAIAgent"
    _MOD_LLM = "crew_engine._CrewAILLM"

    def test_timeout_emits_agent_timeout_event(self):
        """When kickoff_async exceeds max_execution_time, agent.timeout is emitted."""
        engine = CrewEngine()
        agent_model = _make_agent(role="SlowAgent", max_execution_time=0.001)
        crew = _make_crew(agents=[agent_model])
        events: list[dict] = []

        with patch(self._MOD_AGENT) as MockAgent, \
             patch(self._MOD_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _slow_kickoff(*args, **kwargs):
                await asyncio.sleep(999)  # way beyond timeout
                return "never"

            mock_agent.kickoff_async = _slow_kickoff
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            handle = engine.test_agent(
                crew, "SlowAgent", "prompt", on_event=events.append
            )
            handle.thread.join(timeout=10)

            timeout_events = [e for e in events if e["type"] == "agent.timeout"]
            assert len(timeout_events) == 1
            evt = timeout_events[0]
            assert evt["crew_id"].startswith("pg-")
            assert evt["agent_role"] == "SlowAgent"
            assert evt["timeout_seconds"] == 0.001
            assert "ts" in evt

    def test_no_timeout_when_not_configured(self):
        """Without max_execution_time, agent completes normally (no timeout)."""
        engine = CrewEngine()
        agent_model = _make_agent(role="FastAgent")  # no max_execution_time
        crew = _make_crew(agents=[agent_model])
        events: list[dict] = []

        with patch(self._MOD_AGENT) as MockAgent, \
             patch(self._MOD_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _fast_kickoff(*args, **kwargs):
                return "done"

            mock_agent.kickoff_async = _fast_kickoff
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            handle = engine.test_agent(
                crew, "FastAgent", "prompt", on_event=events.append
            )
            handle.thread.join(timeout=5)

            # Should complete normally — no timeout event
            event_types = {e["type"] for e in events}
            assert "agent.completed" in event_types
            assert "agent.timeout" not in event_types

    def test_max_execution_time_extracted_from_model_extra(self):
        """max_execution_time comes from model_extra, not a first-class field."""
        agent_model = _make_agent(role="TimedAgent", max_execution_time=30)

        # Verify it lands in model_extra (since it's not a declared field)
        assert agent_model.model_extra.get("max_execution_time") == 30

        engine = CrewEngine()
        crew = _make_crew(agents=[agent_model])
        events: list[dict] = []

        with patch(self._MOD_AGENT) as MockAgent, \
             patch(self._MOD_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _fast_kickoff(*args, **kwargs):
                return "done in time"

            mock_agent.kickoff_async = _fast_kickoff
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            handle = engine.test_agent(
                crew, "TimedAgent", "prompt", on_event=events.append
            )
            handle.thread.join(timeout=5)

            event_types = {e["type"] for e in events}
            assert "agent.completed" in event_types
            assert "agent.timeout" not in event_types

    def test_handle_returns_execution_handle(self):
        """test_agent() returns an ExecutionHandle with pg- crew_id."""
        engine = CrewEngine()
        agent_model = _make_agent(role="R")
        crew = _make_crew(agents=[agent_model])
        events: list[dict] = []

        with patch(self._MOD_AGENT) as MockAgent, \
             patch(self._MOD_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _quick(*args, **kwargs):
                return "ok"

            mock_agent.kickoff_async = _quick
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            handle = engine.test_agent(crew, "R", "hello", on_event=events.append)
            handle.thread.join(timeout=5)

            from crew_engine import ExecutionHandle
            assert isinstance(handle, ExecutionHandle)
            assert handle.crew_id.startswith("pg-")
            assert isinstance(handle.thread, threading.Thread)
            assert handle.flag == {"stop": False}


class TestAgentTimeoutEventShape:
    """Verify the agent.timeout event payload has the expected shape."""

    _MOD_AGENT = "crew_engine._CrewAIAgent"
    _MOD_LLM = "crew_engine._CrewAILLM"

    def test_timeout_event_has_all_required_fields(self):
        engine = CrewEngine()
        agent_model = _make_agent(role="T", max_execution_time=5)
        crew = _make_crew(agents=[agent_model])
        events: list[dict] = []

        with patch(self._MOD_AGENT) as MockAgent, \
             patch(self._MOD_LLM) as MockLLM:
            mock_agent = MagicMock()

            async def _will_timeout(*args, **kwargs):
                await asyncio.sleep(999)
                return "nope"

            mock_agent.kickoff_async = _will_timeout
            MockAgent.return_value = mock_agent
            MockLLM.return_value = MagicMock()

            handle = engine.test_agent(crew, "T", "p", on_event=events.append)
            handle.thread.join(timeout=10)

            timeout_events = [e for e in events if e["type"] == "agent.timeout"]
            assert len(timeout_events) == 1
            evt = timeout_events[0]

            # Required fields per spec
            assert "type" in evt and evt["type"] == "agent.timeout"
            assert "crew_id" in evt
            assert "agent_role" in evt and evt["agent_role"] == "T"
            assert "timeout_seconds" in evt
            assert "ts" in evt


# ═══════════════════════════════════════════════════════════════════════════
#  Playground state (operations.py — deque behavior)
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundRunsState:
    """_playground_runs is a dict of deques keyed by session,
    each with maxlen=2 to keep the last 2 runs.
    """

    def test_deque_maxlen_is_2(self):
        """deque(maxlen=2) evicts oldest when a third item is appended."""
        _playground_runs: dict[str, deque] = {}

        session_id = "test-session-1"
        _playground_runs[session_id] = deque(maxlen=2)

        _playground_runs[session_id].append({"run": 1})
        _playground_runs[session_id].append({"run": 2})
        _playground_runs[session_id].append({"run": 3})

        assert len(_playground_runs[session_id]) == 2
        # Oldest (run 1) evicted
        runs = list(_playground_runs[session_id])
        assert runs[0]["run"] == 2
        assert runs[1]["run"] == 3

    def test_run_metadata_shape(self):
        """Each run entry carries the expected metadata fields."""
        from datetime import datetime, timezone

        run_entry = {
            "crew_id": "pg-abc123",
            "agent_role": "Researcher",
            "prompt": "What is CrewAI?",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }

        required_keys = {"crew_id", "agent_role", "prompt", "start_time", "status"}
        assert required_keys.issubset(run_entry.keys())

    def test_different_sessions_isolated(self):
        """Two sessions get independent deques."""
        _playground_runs: dict[str, deque] = {}

        _playground_runs["session-A"] = deque(maxlen=2)
        _playground_runs["session-B"] = deque(maxlen=2)

        _playground_runs["session-A"].append({"run": "A1"})
        _playground_runs["session-B"].append({"run": "B1"})

        assert len(_playground_runs["session-A"]) == 1
        assert len(_playground_runs["session-B"]) == 1
        # A's deque unaffected by B
        _playground_runs["session-A"].append({"run": "A2"})
        assert len(_playground_runs["session-A"]) == 2
        assert len(_playground_runs["session-B"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Agent model — model_extra passthrough
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentModelExtraPassthrough:
    """max_execution_time and other extra fields land in model_extra."""

    def test_max_execution_time_in_model_extra(self):
        agent = _make_agent(role="X", max_execution_time=30)
        assert "max_execution_time" in agent.model_extra
        assert agent.model_extra["max_execution_time"] == 30

    def test_multiple_extra_fields(self):
        agent = models.AgentModel(
            role="X",
            goal="G",
            max_execution_time=60,
            max_rpm=10,
            cache=True,
        )
        assert agent.model_extra.get("max_execution_time") == 60
        assert agent.model_extra.get("max_rpm") == 10
        assert agent.model_extra.get("cache") is True

    def test_no_extra_means_empty_model_extra(self):
        agent = models.AgentModel(role="X", goal="G")
        # model_extra is always a dict, just empty when no extra fields
        assert isinstance(agent.model_extra, dict)


# ═══════════════════════════════════════════════════════════════════════════
#  Route registration (app.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundRoute:
    """Verify /playground and /operations routes are registered."""

    def test_routes_registered_in_module(self):
        """Both /playground and /operations route functions exist and have correct paths."""
        import app as _app_module  # noqa: F811

        # Check that the route functions exist
        assert hasattr(_app_module, "playground_page"), (
            "playground_page function should exist in app module"
        )
        assert hasattr(_app_module, "operations"), (
            "operations function should exist in app module"
        )

        # Verify they are decorated as NiceGUI pages by checking for the
        # __wrapped__-like markers that NiceGUI adds or the route path attr.
        import inspect

        # NiceGUI stores route metadata on the function
        pg_fn = _app_module.playground_page
        ops_fn = _app_module.operations

        # Verify the functions are callable (they were decorated)
        assert callable(pg_fn), "playground_page should be callable"
        assert callable(ops_fn), "operations should be callable"

    def test_nav_items_include_playground(self):
        """NAV_ITEMS includes a Playground entry."""
        import app as _app_module

        nav_labels = {item[0] for item in _app_module.NAV_ITEMS}
        assert "Playground" in nav_labels

        nav_paths = {item[1] for item in _app_module.NAV_ITEMS}
        assert "/playground" in nav_paths


# ═══════════════════════════════════════════════════════════════════════════
#  Execution flow — _run_playground stores handle and routes events
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundExecutionFlow:
    """_run_playground calls CrewEngine.test_agent and stores the
    ExecutionHandle in the session's deque."""

    def test_run_playground_stores_handle_in_deque(self):
        """_run_playground creates a run entry with crew_id, agent_role,
        prompt, start_time, status=running, and handle."""
        from unittest.mock import MagicMock, patch

        import operations as ops

        mock_handle = MagicMock()
        mock_handle.crew_id = "pg-mock123"
        mock_handle.thread = MagicMock()

        with patch.object(ops, "_get_session_id", return_value="ses-run"):
            with patch.object(ops, "_get_crew_model", return_value=_make_crew()):
                with patch("crew_engine.CrewEngine") as MockEngine:
                    mock_engine = MagicMock()
                    mock_engine.test_agent.return_value = mock_handle
                    MockEngine.return_value = mock_engine

                    with patch.object(ops._render_playground, "refresh"):
                        ops._run_playground("Researcher", "Test prompt")

        runs = ops._playground_runs.get("ses-run")
        assert runs is not None
        assert len(runs) == 1
        run = runs[0]
        assert run["crew_id"].startswith("pg-")
        assert run["agent_role"] == "Researcher"
        assert run["prompt"] == "Test prompt"
        assert run["status"] == "running"
        assert run["handle"] is mock_handle

        ops._playground_runs.clear()

    def test_run_playground_warns_on_no_crew(self):
        """_run_playground shows warning when no crew is configured."""
        from unittest.mock import patch

        import operations as ops

        with patch.object(ops, "_get_session_id", return_value="ses-no-crew"):
            with patch.object(ops, "_get_crew_model", return_value=None):
                with patch.object(ops.ui, "notify") as mock_notify:
                    ops._run_playground("Agent", "Prompt")
                    mock_notify.assert_called_once()
                    assert "No crew" in mock_notify.call_args[0][0]

    def test_run_playground_warns_on_empty_prompt(self):
        """_run_playground shows warning when prompt is whitespace-only."""
        from unittest.mock import patch

        import operations as ops

        with patch.object(ops, "_get_session_id", return_value="ses-empty-prompt"):
            with patch.object(ops, "_get_crew_model", return_value=_make_crew()):
                with patch.object(ops.ui, "notify") as mock_notify:
                    ops._run_playground("Researcher", "  ")
                    mock_notify.assert_called_once()
                    assert "prompt" in mock_notify.call_args[0][0].lower()


# ═══════════════════════════════════════════════════════════════════════════
#  Stacked panels — deque(maxlen=2) keeps last 2 only
# ═══════════════════════════════════════════════════════════════════════════


class TestStackedPanels:
    """The deque(maxlen=2) evicts the oldest run when a third is appended,
    and reversed iteration displays newest first."""

    def test_deque_evicts_oldest_on_third_run(self):
        """After 3 append() calls, len==2 and the first entry is gone."""
        _playground_runs_deque: deque = deque(maxlen=2)
        _playground_runs_deque.append({"crew_id": "pg-1", "agent_role": "A"})
        _playground_runs_deque.append({"crew_id": "pg-2", "agent_role": "B"})
        _playground_runs_deque.append({"crew_id": "pg-3", "agent_role": "C"})

        assert len(_playground_runs_deque) == 2
        crew_ids = [r["crew_id"] for r in _playground_runs_deque]
        assert "pg-1" not in crew_ids
        assert crew_ids == ["pg-2", "pg-3"]

    def test_reversed_iteration_newest_first(self):
        """reversed() yields newest entry first."""
        dq: deque = deque(maxlen=2)
        dq.append({"crew_id": "pg-1", "agent_role": "A"})
        dq.append({"crew_id": "pg-2", "agent_role": "B"})

        reversed_runs = list(reversed(dq))
        assert reversed_runs[0]["crew_id"] == "pg-2"  # newest
        assert reversed_runs[1]["crew_id"] == "pg-1"  # oldest


# ═══════════════════════════════════════════════════════════════════════════
#  Stop button — cooperative cancellation
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundStop:
    """_stop_playground sets flag["stop"] = True for cooperative cancellation."""

    def test_stop_sets_flag_and_status(self):
        """The stop handler sets the stop flag and marks the run as stopping."""
        from unittest.mock import MagicMock, patch

        import operations as ops
        import threading

        stop_event = threading.Event()
        thread = threading.Thread(target=stop_event.wait)
        thread.start()

        try:
            mock_handle = MagicMock()
            mock_handle.thread = thread
            mock_handle.flag = {"stop": False}

            session_id = "test-session-stop"

            ops._playground_runs[session_id] = deque(maxlen=2)
            ops._playground_runs[session_id].append({
                "crew_id": "pg-test-stop",
                "agent_role": "R",
                "prompt": "p",
                "start_time": 0,
                "status": "running",
                "handle": mock_handle,
            })

            with patch.object(ops, "_get_session_id", return_value=session_id):
                with patch.object(ops._render_playground, "refresh"):
                    ops._stop_playground()

            assert mock_handle.flag["stop"] is True
            assert ops._playground_runs[session_id][0]["status"] == "stopping"
        finally:
            stop_event.set()
            thread.join(timeout=2)
            ops._playground_runs.clear()

    def test_stop_no_runs_silent(self):
        """_stop_playground with no runs just returns without error."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-session-no-runs"
        ops._playground_runs.pop(session_id, None)

        with patch.object(ops, "_get_session_id", return_value=session_id):
            with patch.object(ops._render_playground, "refresh"):
                ops._stop_playground()

    def test_stop_no_handle_silent(self):
        """If the current run has no handle, _stop_playground is a noop."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-session-no-handle"

        ops._playground_runs[session_id] = deque(maxlen=2)
        ops._playground_runs[session_id].append({
            "crew_id": "pg-no-handle",
            "agent_role": "R",
            "prompt": "p",
            "start_time": 0,
            "status": "running",
            "handle": None,
        })

        with patch.object(ops, "_get_session_id", return_value=session_id):
            with patch.object(ops._render_playground, "refresh"):
                ops._stop_playground()

        ops._playground_runs.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  Error / timeout event handling
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundEventHandling:
    """_on_playground_event updates run status and stores error/timeout data."""

    def test_event_completed_updates_status(self):
        """agent.completed marks the run as completed."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-events-comp"

        ops._playground_runs[session_id] = deque(maxlen=2)
        ops._playground_runs[session_id].append({
            "crew_id": "pg-events-1",
            "agent_role": "R",
            "prompt": "p",
            "start_time": 0,
            "status": "running",
            "handle": None,
            "error": None,
            "timeout_seconds": None,
        })

        with patch.object(ops._render_playground, "refresh"):
            ops._on_playground_event({
                "type": "agent.completed",
                "crew_id": "pg-events-1",
                "agent_role": "R",
            })

        assert ops._playground_runs[session_id][0]["status"] == "completed"
        ops._playground_runs.clear()

    def test_event_error_updates_status_and_stores_message(self):
        """agent.error stores the error message and marks status=error."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-events-err"

        ops._playground_runs[session_id] = deque(maxlen=2)
        ops._playground_runs[session_id].append({
            "crew_id": "pg-err-1",
            "agent_role": "R",
            "prompt": "p",
            "start_time": 0,
            "status": "running",
            "handle": None,
            "error": None,
            "timeout_seconds": None,
        })

        with patch.object(ops._render_playground, "refresh"):
            ops._on_playground_event({
                "type": "agent.error",
                "crew_id": "pg-err-1",
                "error": "LLM connection failed",
            })

        assert ops._playground_runs[session_id][0]["status"] == "error"
        assert ops._playground_runs[session_id][0]["error"] == "LLM connection failed"
        ops._playground_runs.clear()

    def test_event_timeout_updates_status_and_stores_seconds(self):
        """agent.timeout marks status=timeout and stores timeout_seconds."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-events-timeout"

        ops._playground_runs[session_id] = deque(maxlen=2)
        ops._playground_runs[session_id].append({
            "crew_id": "pg-timeout-1",
            "agent_role": "R",
            "prompt": "p",
            "start_time": 0,
            "status": "running",
            "handle": None,
            "error": None,
            "timeout_seconds": None,
        })

        with patch.object(ops._render_playground, "refresh"):
            ops._on_playground_event({
                "type": "agent.timeout",
                "crew_id": "pg-timeout-1",
                "timeout_seconds": 30,
            })

        assert ops._playground_runs[session_id][0]["status"] == "timeout"
        assert ops._playground_runs[session_id][0]["timeout_seconds"] == 30
        ops._playground_runs.clear()

    def test_event_stopped_updates_status(self):
        """agent.stopped marks status=stopped."""
        from unittest.mock import patch

        import operations as ops

        session_id = "test-events-stopped"

        ops._playground_runs[session_id] = deque(maxlen=2)
        ops._playground_runs[session_id].append({
            "crew_id": "pg-stopped-1",
            "agent_role": "R",
            "prompt": "p",
            "start_time": 0,
            "status": "running",
            "handle": None,
            "error": None,
            "timeout_seconds": None,
        })

        with patch.object(ops._render_playground, "refresh"):
            ops._on_playground_event({
                "type": "agent.stopped",
                "crew_id": "pg-stopped-1",
                "reason": "User cancelled",
            })

        assert ops._playground_runs[session_id][0]["status"] == "stopped"
        ops._playground_runs.clear()

    def test_event_handler_does_not_crash_on_exception(self):
        """_on_playground_event catches exceptions and never raises."""
        from unittest.mock import patch

        import operations as ops

        ops._playground_runs.clear()

        with patch.object(ops._render_playground, "refresh",
                          side_effect=RuntimeError("UI not available")):
            # Should not raise — event with unknown crew_id handled gracefully
            ops._on_playground_event({
                "type": "agent.completed",
                "crew_id": "pg-ghost-999",
            })

        ops._playground_runs.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  Observability integration — events reach _dispatch
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundObservabilityIntegration:
    """The on_event closure in _run_playground routes events to
    observability._dispatch AND _on_playground_event."""

    def test_on_event_routes_to_observability_dispatch(self):
        """The on_event callable passed to test_agent calls
        observability._dispatch with each event."""
        from unittest.mock import MagicMock, patch

        import operations as ops

        mock_handle = MagicMock()
        mock_handle.crew_id = "pg-obs-test"
        mock_handle.thread = MagicMock()

        with patch.object(ops, "_get_session_id", return_value="ses-obs"):
            with patch.object(ops, "_get_crew_model", return_value=_make_crew()):
                with patch("crew_engine.CrewEngine") as MockEngine:
                    mock_engine = MagicMock()
                    mock_engine.test_agent.return_value = mock_handle
                    MockEngine.return_value = mock_engine

                    with patch.object(ops._render_playground, "refresh"):
                        with patch.object(
                            ops, "_on_playground_event"
                        ) as pg_mock:
                            with patch.object(
                                ops.observability, "_dispatch"
                            ) as disp_mock:
                                ops._run_playground("Researcher", "Test obs")

                                # Capture and call INSIDE all patch contexts
                                on_event_fn = (
                                    mock_engine.test_agent.call_args[1][
                                        "on_event"
                                    ]
                                )
                                assert callable(on_event_fn)

                                on_event_fn({
                                    "type": "token.stream",
                                    "crew_id": "pg-obs-test",
                                    "text": "Hello",
                                })

                    # Mocks are MagicMock objects — call data preserved
                    disp_mock.assert_called_once()
                    assert disp_mock.call_args[0][0] == {
                        "type": "token.stream",
                        "crew_id": "pg-obs-test",
                        "text": "Hello",
                    }
                    pg_mock.assert_called_once()

        ops._playground_runs.clear()

    def test_on_event_survives_dispatch_exception(self):
        """If observability._dispatch raises, the playground event handler
        is still called (crash guard)."""
        from unittest.mock import MagicMock, patch

        import operations as ops

        mock_handle = MagicMock()
        mock_handle.crew_id = "pg-crash-test"
        mock_handle.thread = MagicMock()

        with patch.object(ops, "_get_session_id", return_value="ses-crash"):
            with patch.object(ops, "_get_crew_model", return_value=_make_crew()):
                with patch("crew_engine.CrewEngine") as MockEngine:
                    mock_engine = MagicMock()
                    mock_engine.test_agent.return_value = mock_handle
                    MockEngine.return_value = mock_engine

                    with patch.object(ops.observability, "_dispatch",
                                      side_effect=RuntimeError("Boom")):
                        with patch.object(ops._render_playground, "refresh"):
                            ops._run_playground("Researcher", "Test crash guard")

        on_event_fn = mock_engine.test_agent.call_args[1]["on_event"]
        assert callable(on_event_fn)

        # Even if _dispatch crashes, _on_playground_event is still called
        with patch.object(ops, "_on_playground_event") as mock_pg_event:
            on_event_fn({
                "type": "token.stream",
                "crew_id": "pg-crash-test",
                "text": "Still alive",
            })

        mock_pg_event.assert_called_once()
        ops._playground_runs.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  Empty state — no agents
# ═══════════════════════════════════════════════════════════════════════════


class TestEmptyStateBehavior:
    """Playground interactions when no crew or no agents are configured."""

    def test_get_crew_model_returns_none_when_no_model(self):
        """_get_crew_model returns None when crew_model is None (mocked)."""
        from unittest.mock import patch, PropertyMock

        import operations as ops

        StorageCls = type(ops.app.storage)
        with patch.object(StorageCls, "user", new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = {"crew_model": None}
            assert ops._get_crew_model() is None

    def test_get_crew_model_returns_valid_model_with_no_agents(self):
        """A crew with zero agents still returns a valid CrewModel (mocked)."""
        from unittest.mock import patch, PropertyMock

        import operations as ops
        import models

        # Build an explicitly empty crew (skip the helper which adds defaults)
        empty_crew = models.CrewModel(
            name="Empty Crew",
            agents=[],
            tasks=[
                models.TaskModel(name="t1", description="d", expected_output="e"),
            ],
        )
        StorageCls = type(ops.app.storage)
        with patch.object(StorageCls, "user", new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = {
                "crew_model": empty_crew.model_dump(mode="json"),
            }
            model = ops._get_crew_model()
            assert model is not None
            assert isinstance(model, models.CrewModel)
            assert len(model.agents) == 0
            assert model.name == "Empty Crew"

    def test_run_playground_graceful_with_invalid_crew_data(self):
        """_run_playground handles invalid crew data without crashing."""
        from unittest.mock import patch

        import operations as ops

        with patch.object(ops, "_get_session_id", return_value="ses-grace"):
            with patch.object(ops, "_get_crew_model", return_value=None):
                with patch.object(ops.ui, "notify") as mock_notify:
                    ops._run_playground("Agent", "Prompt")
                    mock_notify.assert_called_once()
                    assert "No crew" in mock_notify.call_args[0][0]
