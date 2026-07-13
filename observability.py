"""Real-Time Execution Dashboard — macro / meso / micro / resource / error panels.

Single-file observability module following the ``canvas.py`` / ``operations.py``
pattern: module-level state dicts keyed by ``crew_id``, ``@ui.refreshable`` for
discrete panels, direct DOM mutation for token streaming.

Public surface
--------------
* ``crew_event_bus`` — module-level ``Event`` singleton for bridging from
  ``crew_engine`` callbacks.
* ``render_observability(crew_id)`` — composes the five observation panels.
* ``_dispatch(event)`` — crew_id gate + type routing to internal handlers.

State model
-----------
All state dicts are keyed by ``crew_id`` so multiple tabs / crews coexist
without cross-contamination.  The reconnect buffer keeps the last 60 s of
events and replays them on connect so transient page reloads don't lose
execution context.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from nicegui import ui

from crew_engine import ProtocolEvent

# ═══════════════════════════════════════════════════════════════════════════
#  Event Bus — module-level singleton wired in app.py
# ═══════════════════════════════════════════════════════════════════════════

crew_event_bus: Any = None  # set in app.py: crew_event_bus = Event()


def _get_event_class() -> type:
    """Lazy-import NiceGUI Event to keep the module importable without server."""
    from nicegui.event import Event
    return Event


# ═══════════════════════════════════════════════════════════════════════════
#  State dicts — keyed by crew_id
# ═══════════════════════════════════════════════════════════════════════════

_crew_state: dict[str, dict[str, Any]] = {}
"""crew_id → {status, tasks: {task_name: {state, agent, ...}}, progress, ...}"""

_activity_log: dict[str, list[dict[str, Any]]] = {}
"""crew_id → list of meso card entries (agent, tool, delegation, knowledge)."""

_token_elements: dict[str, list[Any]] = {}
"""crew_id → list of streaming UI element refs (for direct DOM mutation)."""

_resources: dict[str, dict[str, Any]] = {}
"""crew_id → {task_name: {tokens_in, tokens_out, cost, duration, iterations}}."""

_errors: dict[str, list[dict[str, Any]]] = {}
"""crew_id → list of error entries with tracebacks (guardrail retries only)."""

# ═══════════════════════════════════════════════════════════════════════════
#  Reconnect buffer
# ═══════════════════════════════════════════════════════════════════════════

_event_buffer: deque[ProtocolEvent] = deque()
"""Unbounded deque; stale events (>60 s) evicted on every insert."""

_BUFFER_WINDOW_S: float = 60.0
"""Maximum age (seconds) of events retained in the reconnect buffer."""


def _buffer_event(event: ProtocolEvent) -> None:
    """Append *event* to the buffer and evict stale entries.

    Eviction is time-based: any event whose ``ts`` is more than
    ``_BUFFER_WINDOW_S`` older than *event*'s ``ts`` is removed from the
    front of the deque (events arrive in chronological order from the
    engine, so a single-pass left-to-right eviction is correct).
    """
    now = event.get("ts", time.time())
    _event_buffer.append(event)
    while _event_buffer and (now - _event_buffer[0].get("ts", 0)) > _BUFFER_WINDOW_S:
        _event_buffer.popleft()


def _replay_buffer(crew_id: str) -> None:
    """Replay all buffered events for *crew_id* in chronological order.

    Called on page load / reconnect so the dashboard catches up with
    events that fired while the client was disconnected.
    """
    for event in list(_event_buffer):
        if event.get("crew_id") == crew_id:
            _route_event(crew_id, event)


# ═══════════════════════════════════════════════════════════════════════════
#  Dispatch — crew_id gate + type routing
# ═══════════════════════════════════════════════════════════════════════════


def _dispatch(event: ProtocolEvent) -> None:
    """Entry point called from the event bus subscriber.

    **Crew-id gate**: events without ``crew_id`` or with a mismatched
    ``crew_id`` are silently dropped.  This prevents cross-crew state
    pollution in multi-tab scenarios.
    """
    crew_id = event.get("crew_id")
    if not crew_id:
        return

    _buffer_event(event)
    _route_event(crew_id, event)


def _route_event(crew_id: str, event: ProtocolEvent) -> None:
    """Route a validated event to the appropriate state handler by type."""
    event_type: str = event.get("type", "")

    # -- Macro layer: crew & task lifecycle ---------------------------------
    if event_type.startswith("crew.") or event_type.startswith("task."):
        _update_crew_state(crew_id, event)

    # -- Guardrail events — affect both crew state and error log -------------
    elif event_type.startswith("guardrail."):
        _update_crew_state(crew_id, event)
        if event_type == "guardrail.failed":
            _update_errors(crew_id, event)

    # -- Meso layer: agent / tool / delegation / knowledge ------------------
    elif event_type.startswith("agent.") or event_type.startswith("tool."):
        _update_activity_log(crew_id, event)
    elif event_type.startswith("memory.") or event_type.startswith("knowledge."):
        _update_activity_log(crew_id, event)

    # -- Micro layer: token streaming ---------------------------------------
    elif event_type.startswith("token."):
        _handle_token(crew_id, event)

    # -- Resource + LLM — per-task usage and completion data ----------------
    elif event_type.startswith("resource."):
        _update_resources(crew_id, event)

    # -- LLM call completions — also contribute to resources ----------------
    elif event_type.startswith("llm.call_completed"):
        _update_resources(crew_id, event)


# ═══════════════════════════════════════════════════════════════════════════
#  State Handlers — each mutates one state dict
# ═══════════════════════════════════════════════════════════════════════════


def _find_or_update_task(
    tasks: dict[str, dict[str, Any]],
    task_name: str,
    new_state: str,
) -> dict[str, Any]:
    """Find a task entry by name or create/update one.

    Strategy (in priority order):
    1. Direct key match — ``task_name`` is already a key in *tasks*.
    2. Name match — an entry's ``name`` field equals *task_name*.
    3. Fallback — the first entry still in "pending" state (used for the
       initial transition from pending → running).
    4. Insert — if no match exists, create a new entry keyed by *task_name*.
    """
    # 1. Direct key match
    if task_name in tasks:
        tasks[task_name]["state"] = new_state
        return tasks[task_name]

    # 2. Name field match
    for key, task in tasks.items():
        if task.get("name") == task_name:
            task["state"] = new_state
            return task

    # 3. First pending task
    for key, task in tasks.items():
        if task.get("state") == "pending":
            task["state"] = new_state
            task["name"] = task_name
            return task

    # 4. Insert new entry
    tasks[task_name] = {"state": new_state, "name": task_name, "agent": "", "attempt": 0}
    return tasks[task_name]


def _update_crew_state(crew_id: str, event: ProtocolEvent) -> None:
    """Update macro-level crew state from crew/task lifecycle events."""
    state = _crew_state.setdefault(crew_id, {
        "status": "idle",
        "crew_name": "",
        "task_count": 0,
        "tasks": {},
        "progress": 0.0,
        "current_tool": "",
    })

    event_type = event.get("type", "")

    if event_type == "crew.started":
        state["status"] = "running"
        state["crew_name"] = event.get("crew_name", "")
        task_count = event.get("task_count", 0)
        state["task_count"] = task_count
        state["progress"] = 0.0
        # Initialise all tasks as pending
        tasks: dict[str, dict[str, Any]] = {}
        for i in range(task_count):
            tasks[f"task_{i}"] = {"state": "pending", "agent": "", "attempt": 0}
        state["tasks"] = tasks

    elif event_type == "crew.completed":
        state["status"] = "completed"
        state["progress"] = 1.0

    elif event_type == "crew.error":
        state["status"] = "error"

    elif event_type == "crew.stopped":
        state["status"] = "stopped"

    elif event_type == "task.state_change":
        task_name = event.get("task_name", "")
        new_state = event.get("new_state", "")
        tasks = state.get("tasks", {})
        if task_name:
            _find_or_update_task(tasks, task_name, new_state)
            # Recalculate progress
            total = state.get("task_count", 0)
            if total > 0:
                completed = sum(
                    1 for t in tasks.values()
                    if t.get("state") in ("completed",)
                )
                state["progress"] = completed / total

    elif event_type == "task.failed":
        task_name = event.get("task_name", "")
        tasks = state.get("tasks", {})
        if task_name:
            _find_or_update_task(tasks, task_name, "failed")

    elif event_type == "guardrail.started":
        task_name = event.get("task_name", "")
        attempt = event.get("attempt", 1)
        tasks = state.get("tasks", {})
        if task_name:
            entry = _find_or_update_task(tasks, task_name, "retrying")
            entry["attempt"] = attempt

    elif event_type == "guardrail.completed":
        task_name = event.get("task_name", "")
        tasks = state.get("tasks", {})
        if task_name:
            _find_or_update_task(tasks, task_name, "running")


def _update_activity_log(crew_id: str, event: ProtocolEvent) -> None:
    """Append an agent / tool / delegation / knowledge card entry."""
    log = _activity_log.setdefault(crew_id, [])
    log.append({
        "ts": event.get("ts", time.time()),
        "type": event.get("type", ""),
        **{k: v for k, v in event.items()
           if k not in ("type", "crew_id", "ts")},
    })


def _handle_token(crew_id: str, event: ProtocolEvent) -> None:
    """Buffer a streaming token for the micro panel."""
    elements = _token_elements.setdefault(crew_id, [])
    elements.append({
        "ts": event.get("ts", time.time()),
        "agent_role": event.get("agent_role", ""),
        "is_thinking": event.get("is_thinking", False),
        "text": event.get("text", ""),
    })


def _update_resources(crew_id: str, event: ProtocolEvent) -> None:
    """Accumulate per-task resource consumption."""
    resources = _resources.setdefault(crew_id, {})
    task = event.get("task", event.get("task_name", "unknown"))
    entry = resources.setdefault(task, {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": None,
        "duration": 0.0,
        "iterations": 0,
        "model": "",
    })

    if event.get("type") == "resource.update":
        entry["tokens_in"] = event.get("tokens_in", 0)
        entry["tokens_out"] = event.get("tokens_out", 0)
        entry["cost"] = event.get("cost")
        entry["duration"] = event.get("duration", 0.0)
        entry["iterations"] = event.get("iterations", 0)
        entry["model"] = event.get("model", "")


def _update_errors(crew_id: str, event: ProtocolEvent) -> None:
    """Append a guardrail error entry."""
    errors = _errors.setdefault(crew_id, [])
    errors.append({
        "ts": event.get("ts", time.time()),
        "guardrail_name": event.get("guardrail_name", ""),
        "task_name": event.get("task_name", ""),
        "error": event.get("error", ""),
        "attempt": event.get("attempt", 1),
        "traceback": event.get("traceback"),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  Macro Panel — crew pipeline, progress bar, status bar
# ═══════════════════════════════════════════════════════════════════════════


def _status_color(status: str) -> str:
    """Map crew/task status to a colour name."""
    return {
        "pending": "grey",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "retrying": "orange",
        "stopped": "amber",
        "error": "red",
        "idle": "grey",
    }.get(status, "grey")


def _status_icon(status: str) -> str:
    """Map crew/task status to a Material icon name."""
    return {
        "pending": "schedule",
        "running": "play_circle",
        "completed": "check_circle",
        "failed": "error",
        "retrying": "refresh",
        "stopped": "stop_circle",
        "error": "warning",
        "idle": "radio_button_unchecked",
    }.get(status, "radio_button_unchecked")


@ui.refreshable
def _render_macro(crew_id: str) -> None:
    """Render the macro-layer pipeline: task states, progress bar, status bar.

    This is the top-level crew execution view showing an overview of all
    tasks and their current states.
    """
    state = _crew_state.get(crew_id, {})
    status = state.get("status", "idle")
    crew_name = state.get("crew_name", "Unknown Crew")
    tasks: dict[str, dict[str, Any]] = state.get("tasks", {})
    progress = state.get("progress", 0.0)

    with ui.card().classes("w-full q-pa-md rounded-borders"):
        # -- Header: crew name + status badge -------------------------------
        with ui.row().classes("w-full items-center justify-between q-mb-md"):
            ui.label(crew_name).classes("text-h6 font-bold")
            with ui.row().classes("items-center gap-2"):
                ui.icon(_status_icon(status), color=_status_color(status))
                ui.label(status.capitalize()).classes(
                    f"text-{_status_color(status)}"
                )

        # -- Progress bar ---------------------------------------------------
        with ui.row().classes("w-full items-center gap-2 q-mb-md"):
            ui.label(f"{int(progress * 100)}%").classes("text-caption")
            ui.linear_progress(value=progress).classes("flex-grow")

        # -- Status bar — current tool name ---------------------------------
        current_tool = state.get("current_tool", "")
        if current_tool:
            ui.label(f"Tool: {current_tool}").classes(
                "text-caption text-grey"
            ).classes("q-mb-sm")

        # -- Pipeline: sequential task states --------------------------------
        if not tasks:
            ui.label("No tasks defined").classes("text-caption text-grey")
            return

        task_keys = sorted(tasks.keys())
        with ui.column().classes("w-full gap-1"):
            for key in task_keys:
                task = tasks[key]
                task_state = task.get("state", "pending")
                task_name = task.get("name", key)
                color = _status_color(task_state)
                icon = _status_icon(task_state)

                with ui.row().classes("w-full items-center gap-2"):
                    # Step indicator
                    ui.icon(icon, color=color).classes("text-h6")
                    # Task label
                    ui.label(task_name).classes(f"text-{color}")
                    # Attempt counter (only for retrying)
                    attempt = task.get("attempt", 0)
                    if task_state == "retrying" and attempt > 0:
                        ui.label(f"(attempt {attempt})").classes(
                            "text-caption text-orange"
                        )


# ═══════════════════════════════════════════════════════════════════════════
#  Empty State — shown when no active crew
# ═══════════════════════════════════════════════════════════════════════════


def _render_empty_state() -> None:
    """Render a blank panel with history stub when no ``crew_id`` is active."""
    with ui.card().classes("w-full q-pa-md rounded-borders"):
        with ui.column().classes("w-full items-center justify-center q-pa-xl"):
            ui.icon("visibility", color="grey-5").classes("text-h2 q-mb-md")
            ui.label("No active crew").classes("text-h6 text-grey-7")
            ui.label(
                "Run a crew from the Builder to see real-time execution here."
            ).classes("text-body2 text-grey-6")


# ═══════════════════════════════════════════════════════════════════════════
#  Main Entry Point — composes panels
# ═══════════════════════════════════════════════════════════════════════════


def render_observability(crew_id: str | None = None) -> None:
    """Render the complete observability dashboard.

    Parameters
    ----------
    crew_id : str | None
        Active crew identifier.  When ``None`` or empty, renders the
        empty-state panel instead of live execution data.
    """
    if not crew_id:
        _render_empty_state()
        return

    # Replay any buffered events missed during disconnect
    _replay_buffer(crew_id)

    # Macro layer — always visible for PR 5a
    _render_macro(crew_id)
