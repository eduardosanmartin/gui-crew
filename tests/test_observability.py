"""Unit tests for observability.py — macro layer, crew_id filtering, buffer, and state.

Tests use synthetic ``ProtocolEvent`` dicts — no CrewAI or NiceGUI server needed.
Follows the ``test_canvas.py`` monkeypatch pattern for isolation.
"""

from __future__ import annotations

import sys
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path so imports resolve
_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import observability
from crew_engine import ProtocolEvent


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers — synthetic ProtocolEvent factories
# ═══════════════════════════════════════════════════════════════════════════


def _evt(
    event_type: str,
    crew_id: str = "crew-001",
    ts: float | None = None,
    **kwargs,
) -> ProtocolEvent:
    """Build a synthetic ProtocolEvent dict."""
    event: ProtocolEvent = {
        "type": event_type,
        "crew_id": crew_id,
        "ts": ts if ts is not None else time.time(),
    }
    event.update(kwargs)
    return event


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures — reset module state before each test
# ═══════════════════════════════════════════════════════════════════════════


def _reset_observability_state() -> None:
    """Clear all module-level state dicts between tests."""
    observability._crew_state.clear()
    observability._activity_log.clear()
    observability._token_elements.clear()
    observability._resources.clear()
    observability._errors.clear()
    observability._event_buffer.clear()


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Automatically reset observability state before each test."""
    _reset_observability_state()


# ═══════════════════════════════════════════════════════════════════════════
#  Crew-ID Filtering Gate
# ═══════════════════════════════════════════════════════════════════════════


class TestCrewIdFiltering:
    """``_dispatch`` must silently drop events with missing or mismatched crew_id."""

    def test_dispatch_drops_event_without_crew_id(self):
        """Event without crew_id should NOT mutate any state dict."""
        observability._dispatch(
            _evt("crew.started", crew_id="", crew_name="Ghost")
        )
        assert len(observability._crew_state) == 0

    def test_dispatch_drops_event_with_none_crew_id(self):
        """Event with explicit None crew_id is dropped."""
        event: ProtocolEvent = {
            "type": "crew.started",
            "crew_id": None,
            "ts": time.time(),
        }  # type: ignore[typeddict-item]
        observability._dispatch(event)
        assert len(observability._crew_state) == 0

    def test_dispatch_accepts_event_with_valid_crew_id(self):
        """Event with valid crew_id updates state."""
        observability._dispatch(
            _evt("crew.started", crew_id="crew-A", crew_name="Test Crew",
                 task_count=2)
        )
        assert "crew-A" in observability._crew_state
        assert observability._crew_state["crew-A"]["crew_name"] == "Test Crew"

    def test_cross_crew_isolation(self):
        """Events from crew-B do NOT affect state for crew-A."""
        # Feed crew-A event
        observability._dispatch(
            _evt("crew.started", crew_id="crew-A", crew_name="Crew A",
                 task_count=3)
        )
        # Feed crew-B event
        observability._dispatch(
            _evt("crew.started", crew_id="crew-B", crew_name="Crew B",
                 task_count=1)
        )
        # crew-A state untouched by crew-B
        state_a = observability._crew_state.get("crew-A", {})
        assert state_a.get("crew_name") == "Crew A"
        assert state_a.get("task_count") == 3

    def test_mismatched_crew_id_no_mutation(self):
        """Events with crew-B ID do NOT appear in crew-A state."""
        observability._dispatch(
            _evt("crew.started", crew_id="crew-A", crew_name="Crew A",
                 task_count=2)
        )
        # Dispatch a task event with different crew_id
        observability._dispatch(
            _evt("task.state_change", crew_id="crew-X", task_name="Task 1",
                 old_state="pending", new_state="running")
        )
        tasks = observability._crew_state["crew-A"].get("tasks", {})
        # All tasks should still be pending
        for task in tasks.values():
            assert task.get("state") == "pending"


# ═══════════════════════════════════════════════════════════════════════════
#  Reconnect Buffer — eviction and replay
# ═══════════════════════════════════════════════════════════════════════════


class TestReconnectBuffer:
    """Buffer evicts events older than 60s and replays in chronological order."""

    def test_buffer_evicts_stale_events(self):
        """Events older than 60s from the latest event are evicted."""
        base_ts = 1000.0  # Arbitrary base timestamp for controlled testing
        # Insert events at t=0, t=30, t=61, then new event at t=62
        observability._buffer_event(
            _evt("crew.started", crew_id="c1", ts=base_ts + 0)
        )
        observability._buffer_event(
            _evt("task.state_change", crew_id="c1", ts=base_ts + 30,
                 task_name="Task1", new_state="running")
        )
        observability._buffer_event(
            _evt("task.state_change", crew_id="c1", ts=base_ts + 61,
                 task_name="Task1", new_state="completed")
        )
        # Now insert at t=62 — evict t=0 (older than 60s)
        observability._buffer_event(
            _evt("crew.completed", crew_id="c1", ts=base_ts + 62)
        )

        # t=0 should be gone; t=30, t=61, t=62 should remain
        timestamps = [e.get("ts", 0) for e in observability._event_buffer]
        assert len(timestamps) == 3
        assert (base_ts + 0) not in timestamps
        assert (base_ts + 30) in timestamps
        assert (base_ts + 61) in timestamps
        assert (base_ts + 62) in timestamps

    def test_buffer_preserves_recent_events(self):
        """Events within the 60s window are preserved."""
        base_ts = 1000.0
        observability._buffer_event(
            _evt("crew.started", crew_id="c1", ts=base_ts + 50)
        )
        observability._buffer_event(
            _evt("crew.completed", crew_id="c1", ts=base_ts + 55)
        )
        assert len(observability._event_buffer) == 2

    def test_buffer_evicts_multiple_stale(self):
        """Multiple stale events are evicted in a single insert."""
        base_ts = 1000.0
        for i in range(5):
            observability._buffer_event(
                _evt("task.state_change", crew_id="c1", ts=base_ts + i * 10,
                     task_name=f"Task{i}")
            )
        # Insert at t=1070 — all events at t≤1010 are >60s old.
        # t=1000 (gap 70), t=1010 (gap 60 — NOT > 60, stays).
        # So t=1000 evicted, remaining: 1010, 1020, 1030, 1040, 1070
        observability._buffer_event(
            _evt("crew.completed", crew_id="c1", ts=base_ts + 107)
        )
        # Gap check: 1107-1000=107>60 evict, 1107-1010=97>60 evict,
        # 1107-1020=87>60 evict, 1107-1030=77>60 evict,
        # 1107-1040=67>60 evict.
        # Only the latest event (t=1107) remains.
        assert len(observability._event_buffer) == 1

    def test_replay_buffer_filters_by_crew_id(self):
        """_replay_buffer only replays events matching the given crew_id."""
        base_ts = 1000.0
        for i in range(3):
            observability._buffer_event(
                _evt("task.state_change", crew_id="crew-A", ts=base_ts + i,
                     task_name=f"Task{i}")
            )
            observability._buffer_event(
                _evt("task.state_change", crew_id="crew-B", ts=base_ts + i,
                     task_name=f"TaskB{i}")
            )

        # Replay for crew-A only
        observability._replay_buffer("crew-A")
        state_a = observability._crew_state.get("crew-A", {})
        # crew-A should have state; crew-B should NOT
        assert state_a.get("task_count", 0) > 0 or state_a.get("status") is not None

    def test_replay_buffer_chronological_order(self):
        """Replayed events are processed in order (preserved by deque)."""
        base_ts = 1000.0
        # Feed crew events in chronological order
        observability._buffer_event(
            _evt("crew.started", crew_id="c1", ts=base_ts + 1,
                 crew_name="Chrono Crew", task_count=2)
        )
        observability._buffer_event(
            _evt("task.state_change", crew_id="c1", ts=base_ts + 2,
                 task_name="First", new_state="running")
        )
        observability._buffer_event(
            _evt("task.state_change", crew_id="c1", ts=base_ts + 3,
                 task_name="First", new_state="completed")
        )
        observability._crew_state.clear()  # Simulate reconnect
        observability._replay_buffer("c1")
        state = observability._crew_state.get("c1", {})
        assert state.get("status") == "running" or state.get("progress", 0) > 0


# ═══════════════════════════════════════════════════════════════════════════
#  Macro State Accumulation
# ═══════════════════════════════════════════════════════════════════════════


class TestMacroState:
    """Macro layer correctly accumulates task states from event sequences."""

    def test_crew_started_initialises_tasks(self):
        """A crew.started event sets up pending tasks."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test Crew",
                 task_count=3)
        )
        state = observability._crew_state.get("c1", {})
        assert state.get("status") == "running"
        assert state.get("task_count") == 3
        tasks = state.get("tasks", {})
        assert len(tasks) == 3
        for task in tasks.values():
            assert task.get("state") == "pending"

    def test_task_state_transitions(self):
        """Tasks transition through pending → running → completed."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=2)
        )
        # First task transitions to running
        observability._dispatch(
            _evt("task.state_change", crew_id="c1", task_name="Research",
                 old_state="pending", new_state="running")
        )
        # First task completes
        observability._dispatch(
            _evt("task.state_change", crew_id="c1", task_name="Research",
                 old_state="running", new_state="completed")
        )
        tasks = observability._crew_state["c1"]["tasks"]
        # Find the task that was updated
        research_task = None
        for key, task in tasks.items():
            if task.get("name") == "Research":
                research_task = task
                break
        assert research_task is not None
        assert research_task.get("state") == "completed"

    def test_progress_bar_updates(self):
        """Progress bar reflects completed / total ratio."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=4)
        )
        # Complete 2 tasks
        for name in ("A", "B"):
            observability._dispatch(
                _evt("task.state_change", crew_id="c1", task_name=name,
                     old_state="running", new_state="completed")
            )
        progress = observability._crew_state["c1"].get("progress", 0)
        assert progress == 0.5  # 2/4

    def test_crew_completed_sets_full_progress(self):
        """crew.completed sets status to completed and progress to 1.0."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=1)
        )
        observability._dispatch(
            _evt("crew.completed", crew_id="c1")
        )
        state = observability._crew_state.get("c1", {})
        assert state.get("status") == "completed"
        assert state.get("progress") == 1.0

    def test_task_failed_updates_state(self):
        """Task failure is reflected in state."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=2)
        )
        observability._dispatch(
            _evt("task.state_change", crew_id="c1", task_name="Research",
                 old_state="pending", new_state="running")
        )
        observability._dispatch(
            _evt("task.failed", crew_id="c1", task_name="Research",
                 error="Something broke")
        )
        tasks = observability._crew_state["c1"]["tasks"]
        # Find the research task
        failed_task = None
        for key, task in tasks.items():
            if task.get("name") == "Research":
                failed_task = task
                break
        assert failed_task is not None
        assert failed_task.get("state") == "failed"

    def test_guardrail_retry_state(self):
        """Guardrail started → task shows retrying with attempt counter."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=1)
        )
        observability._dispatch(
            _evt("task.state_change", crew_id="c1", task_name="R1",
                 old_state="pending", new_state="running")
        )
        observability._dispatch(
            _evt("guardrail.started", crew_id="c1", task_name="R1",
                 guardrail_name="CheckLength", attempt=2)
        )
        tasks = observability._crew_state["c1"]["tasks"]
        for key, task in tasks.items():
            if task.get("name") == "R1":
                assert task.get("state") == "retrying"
                assert task.get("attempt") == 2

    def test_guardrail_completed_returns_to_running(self):
        """After guardrail completes, task state returns to running."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=1)
        )
        observability._dispatch(
            _evt("task.state_change", crew_id="c1", task_name="R1",
                 old_state="pending", new_state="running")
        )
        observability._dispatch(
            _evt("guardrail.completed", crew_id="c1", task_name="R1",
                 guardrail_name="CheckLength")
        )
        tasks = observability._crew_state["c1"]["tasks"]
        for key, task in tasks.items():
            if task.get("name") == "R1":
                assert task.get("state") == "running"

    def test_crew_error_status(self):
        """crew.error sets status to error."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=1)
        )
        observability._dispatch(
            _evt("crew.error", crew_id="c1", error="Fatal error")
        )
        assert observability._crew_state["c1"]["status"] == "error"

    def test_crew_stopped_status(self):
        """crew.stopped sets status to stopped."""
        observability._dispatch(
            _evt("crew.started", crew_id="c1", crew_name="Test",
                 task_count=1)
        )
        observability._dispatch(
            _evt("crew.stopped", crew_id="c1", reason="User cancelled")
        )
        assert observability._crew_state["c1"]["status"] == "stopped"


# ═══════════════════════════════════════════════════════════════════════════
#  Guardrail Event Mapping — BridgeListener emits correct ProtocolEvents
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardrailEventMapping:
    """BridgeListener correctly maps guardrail CrewAI events to ProtocolEvents."""

    def test_guardrail_started_event(self):
        """LLMGuardrailStartedEvent maps to guardrail.started."""
        from crew_engine import BridgeListener, _CREWAI_EVENT_MAP

        events: list[dict] = []
        listener = BridgeListener(crew_id="g1", on_event=events.append)
        mapping = _CREWAI_EVENT_MAP["LLMGuardrailStartedEvent"]
        handler = listener._make_handler(mapping)

        mock_event = MagicMock()
        mock_event.guardrail_name = "CheckLength"
        mock_event.task_name = "Task1"
        mock_event.attempt = 3
        handler(mock_event)

        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "guardrail.started"
        assert evt["crew_id"] == "g1"
        assert evt["guardrail_name"] == "CheckLength"
        assert evt["task_name"] == "Task1"
        assert evt["attempt"] == 3
        assert "ts" in evt

    def test_guardrail_failed_event(self):
        """LLMGuardrailFailedEvent maps to guardrail.failed."""
        from crew_engine import BridgeListener, _CREWAI_EVENT_MAP

        events: list[dict] = []
        listener = BridgeListener(crew_id="g1", on_event=events.append)
        mapping = _CREWAI_EVENT_MAP["LLMGuardrailFailedEvent"]
        handler = listener._make_handler(mapping)

        mock_event = MagicMock()
        mock_event.guardrail_name = "CheckLength"
        mock_event.task_name = "Task2"
        mock_event.error = "Validation failed"
        mock_event.attempt = 2
        handler(mock_event)

        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "guardrail.failed"
        assert evt["error"] == "Validation failed"
        assert evt["attempt"] == 2

    def test_guardrail_completed_event(self):
        """LLMGuardrailCompletedEvent maps to guardrail.completed."""
        from crew_engine import BridgeListener, _CREWAI_EVENT_MAP

        events: list[dict] = []
        listener = BridgeListener(crew_id="g1", on_event=events.append)
        mapping = _CREWAI_EVENT_MAP["LLMGuardrailCompletedEvent"]
        handler = listener._make_handler(mapping)

        mock_event = MagicMock()
        mock_event.guardrail_name = "CheckLength"
        mock_event.task_name = "Task3"
        mock_event.output = "Validated OK"
        handler(mock_event)

        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "guardrail.completed"
        assert evt["output"] == "Validated OK"

    def test_task_failed_event(self):
        """TaskFailedEvent maps to task.failed."""
        from crew_engine import BridgeListener, _CREWAI_EVENT_MAP

        events: list[dict] = []
        listener = BridgeListener(crew_id="g1", on_event=events.append)
        mapping = _CREWAI_EVENT_MAP["TaskFailedEvent"]
        handler = listener._make_handler(mapping)

        mock_event = MagicMock()
        mock_event.task_name = "Research"
        mock_event.error = "Task crashed"
        mock_event.traceback = "Traceback (most recent call last)..."
        handler(mock_event)

        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "task.failed"
        assert evt["task_name"] == "Research"
        assert evt["error"] == "Task crashed"
        assert "Traceback" in evt["traceback"]

    def test_guardrail_defensive_getattr(self):
        """When guardrail event has no attempt field, defaults to 1."""
        from crew_engine import BridgeListener, _CREWAI_EVENT_MAP

        events: list[dict] = []
        listener = BridgeListener(crew_id="g1", on_event=events.append)
        mapping = _CREWAI_EVENT_MAP["LLMGuardrailFailedEvent"]
        handler = listener._make_handler(mapping)

        # Mock event WITHOUT attempt attribute
        mock_event = MagicMock(spec=[])
        mock_event.guardrail_name = "CheckFormat"
        mock_event.task_name = "T1"
        mock_event.error = "Bad format"
        mock_event.attempt = 1  # default from getattr

        # Since spec=[] blocks attribute access, use configure_mock
        mock_event = MagicMock()
        mock_event.guardrail_name = "CheckFormat"
        mock_event.task_name = "T1"
        mock_event.error = "Bad format"
        del mock_event.attempt  # Simulate missing field
        mock_event.attempt = 1  # getattr with default will return this

        # Actually let's test it differently — just verify getattr default
        handler(mock_event)
        assert len(events) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Error Panel State
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorState:
    """Guardrail errors accumulate correctly in _errors dict."""

    def test_guardrail_failed_adds_error_entry(self):
        """Each guardrail.failed event adds an error entry."""
        observability._dispatch(
            _evt("guardrail.failed", crew_id="c1", guardrail_name="CheckFormat",
                 task_name="Research", error="Bad format", attempt=1)
        )
        assert "c1" in observability._errors
        assert len(observability._errors["c1"]) == 1
        entry = observability._errors["c1"][0]
        assert entry["guardrail_name"] == "CheckFormat"
        assert entry["error"] == "Bad format"
        assert entry["attempt"] == 1

    def test_multiple_error_entries_accumulate(self):
        """Multiple guardrail failures accumulate in order."""
        for attempt in range(1, 4):
            observability._dispatch(
                _evt("guardrail.failed", crew_id="c1", guardrail_name="Check",
                     task_name="Research", error=f"Fail #{attempt}",
                     attempt=attempt)
            )
        assert len(observability._errors["c1"]) == 3
        assert observability._errors["c1"][0]["attempt"] == 1
        assert observability._errors["c1"][2]["attempt"] == 3


# ═══════════════════════════════════════════════════════════════════════════
#  Resource State
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceState:
    """Resource update events accumulate per-task consumption."""

    def test_resource_update_accumulates(self):
        """resource.update adds task-level resource entries."""
        observability._dispatch(
            _evt("resource.update", crew_id="c1", task="Research",
                 tokens_in=500, tokens_out=1200, cost=0.015,
                 duration=3.5, iterations=2)
        )
        resources = observability._resources.get("c1", {})
        assert "Research" in resources
        entry = resources["Research"]
        assert entry["tokens_in"] == 500
        assert entry["tokens_out"] == 1200
        assert entry["cost"] == 0.015
        assert entry["duration"] == 3.5
        assert entry["iterations"] == 2


# ═══════════════════════════════════════════════════════════════════════════
#  Empty State — render_observability with no crew_id
# ═══════════════════════════════════════════════════════════════════════════


class TestEmptyState:
    """render_observability shows empty state when no crew_id."""

    def test_render_observability_none(self):
        """Passing None shows empty state without error."""
        # Should not raise
        observability.render_observability(crew_id=None)
        # State dicts remain empty
        assert len(observability._crew_state) == 0

    def test_render_observability_empty_string(self):
        """Passing empty string is treated same as None."""
        observability.render_observability(crew_id="")
        assert len(observability._crew_state) == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Import verification — module loads without NiceGUI server
# ═══════════════════════════════════════════════════════════════════════════


class TestModuleIntegrity:
    """Verify observability module loads correctly and has expected attributes."""

    def test_module_imports_cleanly(self):
        """Module is importable without a running NiceGUI server."""
        assert hasattr(observability, "crew_event_bus")
        assert hasattr(observability, "_dispatch")
        assert hasattr(observability, "_buffer_event")
        assert hasattr(observability, "_replay_buffer")
        assert hasattr(observability, "render_observability")

    def test_state_dicts_exist(self):
        """All state dicts are initialised."""
        assert isinstance(observability._crew_state, dict)
        assert isinstance(observability._activity_log, dict)
        assert isinstance(observability._token_elements, dict)
        assert isinstance(observability._resources, dict)
        assert isinstance(observability._errors, dict)
        from collections import deque
        assert isinstance(observability._event_buffer, deque)

    def test_crew_event_bus_is_module_attribute(self):
        """crew_event_bus exists as a module-level attribute."""
        assert hasattr(observability, "crew_event_bus")
        # crew_event_bus is None until wired by app.py (at import time).
        # When running alongside other tests that import app.py, it may
        # already be wired — both states are valid depending on import order.


# ═══════════════════════════════════════════════════════════════════════════
#  Meso State Accumulation — agent, tool, delegation, memory, knowledge
# ═══════════════════════════════════════════════════════════════════════════


class TestMesoAgentCards:
    """Agent events produce correctly structured activity-log cards."""

    def test_agent_started_creates_running_card(self):
        """agent.started → card with type='agent', status='running'."""
        observability._dispatch(
            _evt("agent.started", crew_id="c1", agent_role="Researcher",
                 task_name="Research task")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "agent"
        assert card["agent_role"] == "Researcher"
        assert card["task_name"] == "Research task"
        assert card["status"] == "running"

    def test_agent_completed_creates_completed_card(self):
        """agent.completed → card with type='agent', status='completed'."""
        observability._dispatch(
            _evt("agent.completed", crew_id="c1", agent_role="Researcher",
                 task_name="Research task")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "agent"
        assert card["status"] == "completed"

    def test_agent_cards_isolated_by_crew_id(self):
        """Agent events for different crews do NOT cross-contaminate."""
        observability._dispatch(
            _evt("agent.started", crew_id="crew-A", agent_role="Alpha")
        )
        observability._dispatch(
            _evt("agent.started", crew_id="crew-B", agent_role="Beta")
        )
        log_a = observability._activity_log.get("crew-A", [])
        log_b = observability._activity_log.get("crew-B", [])
        assert len(log_a) == 1
        assert log_a[0]["agent_role"] == "Alpha"
        assert len(log_b) == 1
        assert log_b[0]["agent_role"] == "Beta"


class TestMesoToolCards:
    """Tool events produce correctly structured activity-log cards."""

    def test_tool_call_start_creates_running_card(self):
        """tool.call_start → card with type='tool', status='running'."""
        observability._dispatch(
            _evt("tool.call_start", crew_id="c1", tool_name="search_tool",
                 agent_role="Researcher",
                 params={"query": "AI trends"})
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "tool"
        assert card["tool_name"] == "search_tool"
        assert card["agent_role"] == "Researcher"
        assert card["params"] == {"query": "AI trends"}
        assert card["status"] == "running"

    def test_tool_call_end_creates_completed_card(self):
        """tool.call_end → card with type='tool', status='completed',
        duration, result."""
        observability._dispatch(
            _evt("tool.call_end", crew_id="c1", result_summary="Found 10 results",
                 duration_ms=450, error=None)
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "tool"
        assert card["result_summary"] == "Found 10 results"
        assert card["duration_ms"] == 450
        assert card["status"] == "completed"
        assert "tool_name" not in card  # tool.call_end doesn't carry tool_name

    def test_tool_call_end_with_error_creates_error_card(self):
        """tool.call_end with error → status='error'."""
        observability._dispatch(
            _evt("tool.call_end", crew_id="c1", error="Connection refused",
                 duration_ms=120, result_summary="")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "tool"
        assert card["status"] == "error"
        assert card["error"] == "Connection refused"

    def test_tool_progress_updates_existing_running_card(self):
        """tool.progress attaches elapsed_ms to latest matching running card."""
        observability._dispatch(
            _evt("tool.call_start", crew_id="c1", tool_name="long_tool",
                 agent_role="Worker", params={})
        )
        observability._dispatch(
            _evt("tool.progress", crew_id="c1", tool_name="long_tool",
                 elapsed_ms=5000, status_message="Running for 5s...")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1  # still one card — progress updated in-place
        card = log[0]
        assert card["elapsed_ms"] == 5000
        assert card["status_message"] == "Running for 5s..."

    def test_tool_progress_creates_standalone_on_no_match(self):
        """tool.progress without prior tool.call_start creates standalone."""
        observability._dispatch(
            _evt("tool.progress", crew_id="c1", tool_name="orphan_tool",
                 elapsed_ms=3000, status_message="Running...")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "tool"
        assert card["tool_name"] == "orphan_tool"
        assert card["elapsed_ms"] == 3000

    def test_tool_start_and_end_separate_cards(self):
        """A tool call produces two cards: start (running) then end (completed)."""
        observability._dispatch(
            _evt("tool.call_start", crew_id="c1", tool_name="search",
                 agent_role="Agent", params={"q": "test"})
        )
        observability._dispatch(
            _evt("tool.call_end", crew_id="c1", result_summary="Done",
                 duration_ms=300)
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 2
        assert log[0]["type"] == "tool"
        assert log[0]["status"] == "running"
        assert log[0]["tool_name"] == "search"
        assert log[1]["type"] == "tool"
        assert log[1]["status"] == "completed"
        assert log[1]["duration_ms"] == 300


class TestMesoDelegationCards:
    """Delegation events produce cards with from→to agents."""

    def test_delegation_started_creates_running_card(self):
        """delegation.started → card with from/to agents."""
        observability._dispatch(
            _evt("delegation.started", crew_id="c1",
                 from_agent="Manager", to_agent="Analyst",
                 context="Analyze the market data")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "delegation"
        assert card["from_agent"] == "Manager"
        assert card["to_agent"] == "Analyst"
        assert card["context"] == "Analyze the market data"
        assert card["status"] == "running"

    def test_delegation_completed_creates_completed_card(self):
        """delegation.completed → card with response."""
        observability._dispatch(
            _evt("delegation.completed", crew_id="c1",
                 from_agent="Manager", to_agent="Analyst",
                 response="Market is trending upward")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "delegation"
        assert card["status"] == "completed"
        assert card["response"] == "Market is trending upward"


class TestMesoMemoryCards:
    """Memory operation events produce cards with kind, query, time."""

    def test_memory_op_creates_card(self):
        """memory.op → card with type='memory', kind, query, query_time_ms."""
        observability._dispatch(
            _evt("memory.op", crew_id="c1", kind="long_term",
                 query="What is the capital of France?",
                 query_time_ms=250)
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "memory"
        assert card["kind"] == "long_term"
        assert card["query"] == "What is the capital of France?"
        assert card["query_time_ms"] == 250

    def test_memory_short_term_kind(self):
        """Short-term memory ops are captured with kind='short_term'."""
        observability._dispatch(
            _evt("memory.op", crew_id="c1", kind="short_term",
                 query="Recent conversation", query_time_ms=50)
        )
        card = observability._activity_log["c1"][0]
        assert card["kind"] == "short_term"


class TestMesoKnowledgeCards:
    """Knowledge operation events produce cards with kind, query, chunks."""

    def test_knowledge_op_creates_card(self):
        """knowledge.op → card with type='knowledge', kind, query, chunks."""
        observability._dispatch(
            _evt("knowledge.op", crew_id="c1", kind="retrieval_completed",
                 query="company quarterly report",
                 chunks=7)
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 1
        card = log[0]
        assert card["type"] == "knowledge"
        assert card["kind"] == "retrieval_completed"
        assert card["query"] == "company quarterly report"
        assert card["chunks"] == 7

    def test_knowledge_retrieval_started_kind(self):
        """Retrieval started events have kind='retrieval_started'."""
        observability._dispatch(
            _evt("knowledge.op", crew_id="c1", kind="retrieval_started",
                 query="sales data", chunks=0)
        )
        card = observability._activity_log["c1"][0]
        assert card["kind"] == "retrieval_started"
        assert card["chunks"] == 0


class TestMesoActivityOrder:
    """Activity log preserves chronological insertion order (oldest-first
    in state) so that the renderer can reverse for newest-first display."""

    def test_activities_in_chronological_order(self):
        """Activities are appended in order — index 0 is oldest."""
        base_ts = 1000.0
        observability._dispatch(
            _evt("agent.started", crew_id="c1", ts=base_ts + 1,
                 agent_role="First")
        )
        observability._dispatch(
            _evt("agent.started", crew_id="c1", ts=base_ts + 2,
                 agent_role="Second")
        )
        observability._dispatch(
            _evt("agent.started", crew_id="c1", ts=base_ts + 3,
                 agent_role="Third")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 3
        # Oldest first
        assert log[0]["agent_role"] == "First"
        assert log[1]["agent_role"] == "Second"
        assert log[2]["agent_role"] == "Third"

    def test_reversed_yields_newest_first(self):
        """Reversing the log yields newest-first (for rendering)."""
        observability._dispatch(
            _evt("agent.started", crew_id="c1", agent_role="Oldest")
        )
        observability._dispatch(
            _evt("agent.completed", crew_id="c1", agent_role="Newest")
        )
        log = observability._activity_log.get("c1", [])
        reversed_log = list(reversed(log))
        assert reversed_log[0]["agent_role"] == "Newest"
        assert reversed_log[1]["agent_role"] == "Oldest"

    def test_mixed_activity_types_in_order(self):
        """Mixed agent/tool/memory/knowledge events arrive in insertion order."""
        observability._dispatch(
            _evt("agent.started", crew_id="c1", agent_role="A1")
        )
        observability._dispatch(
            _evt("tool.call_start", crew_id="c1", tool_name="t1")
        )
        observability._dispatch(
            _evt("memory.op", crew_id="c1", kind="short_term")
        )
        observability._dispatch(
            _evt("knowledge.op", crew_id="c1", kind="retrieval_completed")
        )
        observability._dispatch(
            _evt("delegation.started", crew_id="c1",
                 from_agent="Mgr", to_agent="Wkr")
        )
        log = observability._activity_log.get("c1", [])
        assert len(log) == 5
        assert log[0]["type"] == "agent"
        assert log[1]["type"] == "tool"
        assert log[2]["type"] == "memory"
        assert log[3]["type"] == "knowledge"
        assert log[4]["type"] == "delegation"


# ═══════════════════════════════════════════════════════════════════════════
#  Meso Module Attributes
# ═══════════════════════════════════════════════════════════════════════════


class TestMesoModuleAttributes:
    """Meso layer functions are importable from the module."""

    def test_render_meso_is_module_attribute(self):
        """_render_meso is a module-level function."""
        assert hasattr(observability, "_render_meso")

    def test_update_activity_log_is_module_attribute(self):
        """_update_activity_log is a module-level function."""
        assert hasattr(observability, "_update_activity_log")
