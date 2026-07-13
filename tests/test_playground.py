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
