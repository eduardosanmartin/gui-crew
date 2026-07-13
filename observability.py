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

import html as _html
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
"""crew_id → list of error entries with tracebacks (guardrail retries + task failures)."""

_token_stats: dict[str, dict[str, Any]] = {}
"""crew_id → {tokens_per_sec, token_count, token_timestamps} for rate calculation."""

_token_displays: dict[str, Any] = {}
"""crew_id → ui.html element for direct DOM mutation of the token stream."""

_tps_labels: dict[str, Any] = {}
"""crew_id → ui.label element showing the live tokens/sec metric."""

_token_count_labels: dict[str, Any] = {}
"""crew_id → ui.label element showing the cumulative token count."""

_head_html_injected: bool = False
"""Guard so ``ui.add_head_html`` for token styles runs only once."""


def _escape_html(text: str) -> str:
    """Escape *text* for safe injection into an HTML element."""
    return _html.escape(text, quote=False)


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
        if event_type in ("task.failed", "crew.error"):
            _update_errors(crew_id, event)

    # -- Guardrail events — affect both crew state and error log -------------
    elif event_type.startswith("guardrail."):
        _update_crew_state(crew_id, event)
        if event_type == "guardrail.failed":
            _update_errors(crew_id, event)

    # -- Meso layer: agent / tool / delegation / memory / knowledge -----------
    elif event_type.startswith(("agent.", "tool.", "memory.",
                                 "knowledge.", "delegation.")):
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
    """Append a structured meso card entry from an agent / tool / delegation /
    memory / knowledge event.

    Normalises the raw ``ProtocolEvent`` into a card dict with a short
    ``type`` (``agent`` | ``tool`` | ``delegation`` | ``memory`` |
    ``knowledge``) and a flat set of display-ready fields so the renderer
    can switch on card type cleanly.
    """
    log = _activity_log.setdefault(crew_id, [])
    event_type: str = event.get("type", "")
    ts = event.get("ts", time.time())

    # -- Agent cards ----------------------------------------------------------
    if event_type.startswith("agent."):
        log.append({
            "ts": ts,
            "type": "agent",
            "agent_role": event.get("agent_role", ""),
            "task_name": event.get("task_name", ""),
            "status": "running" if event_type == "agent.started" else "completed",
        })

    # -- Tool cards -----------------------------------------------------------
    elif event_type.startswith("tool."):
        if event_type == "tool.call_start":
            log.append({
                "ts": ts,
                "type": "tool",
                "tool_name": event.get("tool_name", ""),
                "agent_role": event.get("agent_role", ""),
                "params": event.get("params", {}),
                "status": "running",
            })
        elif event_type == "tool.call_end":
            error = event.get("error")
            log.append({
                "ts": ts,
                "type": "tool",
                "result_summary": event.get("result_summary", ""),
                "duration_ms": event.get("duration_ms", 0),
                "error": error,
                "status": "error" if error else "completed",
            })
        elif event_type == "tool.progress":
            tool_name = event.get("tool_name", "")
            # Find the most recent running tool card for this tool and attach
            # progress info (or create a standalone card).
            for card in reversed(log):
                if card.get("type") == "tool" and card.get("tool_name") == tool_name and card.get("status") == "running":
                    card["elapsed_ms"] = event.get("elapsed_ms", 0)
                    card["status_message"] = event.get("status_message", "")
                    break
            else:
                log.append({
                    "ts": ts,
                    "type": "tool",
                    "tool_name": tool_name,
                    "status": "running",
                    "elapsed_ms": event.get("elapsed_ms", 0),
                    "status_message": event.get("status_message", ""),
                })

    # -- Delegation cards -----------------------------------------------------
    elif event_type.startswith("delegation."):
        log.append({
            "ts": ts,
            "type": "delegation",
            "from_agent": event.get("from_agent", ""),
            "to_agent": event.get("to_agent", ""),
            "context": event.get("context", ""),
            "response": event.get("response"),
            "status": "running" if event_type == "delegation.started" else "completed",
        })

    # -- Memory cards ---------------------------------------------------------
    elif event_type.startswith("memory."):
        log.append({
            "ts": ts,
            "type": "memory",
            "kind": event.get("kind", ""),
            "query": event.get("query", ""),
            "query_time_ms": event.get("query_time_ms", 0),
        })

    # -- Knowledge cards ------------------------------------------------------
    elif event_type.startswith("knowledge."):
        log.append({
            "ts": ts,
            "type": "knowledge",
            "kind": event.get("kind", ""),
            "query": event.get("query", ""),
            "chunks": event.get("chunks", 0),
        })


def _handle_token(crew_id: str, event: ProtocolEvent) -> None:
    """Buffer a streaming token and update the micro panel via direct DOM.

    On each ``token.stream`` event this handler:

    1. Stores the token in ``_token_elements[crew_id]`` for replay.
    2. Updates the sliding-window tokens/sec metric.
    3. Appends a styled ``<span>`` directly to the streaming ``ui.html``
       element if one has been created by ``_render_micro``.
    """
    global _head_html_injected

    token_text = event.get("text", "")
    is_thinking = event.get("is_thinking", False)
    ts = event.get("ts", time.time())

    # -- State: store token --------------------------------------------------
    _token_elements.setdefault(crew_id, []).append({
        "ts": ts,
        "agent_role": event.get("agent_role", ""),
        "is_thinking": is_thinking,
        "text": token_text,
    })

    # -- Stats: tokens/sec + cumulative count --------------------------------
    stats = _token_stats.setdefault(crew_id, {
        "tokens_per_sec": 0.0,
        "token_count": 0,
        "token_timestamps": [],
        "last_ts": ts,
    })
    stats["token_count"] += 1
    stats["token_timestamps"].append(ts)
    # Keep a sliding window of the last 100 tokens for rate calculation.
    if len(stats["token_timestamps"]) > 100:
        stats["token_timestamps"] = stats["token_timestamps"][-100:]
    if len(stats["token_timestamps"]) >= 2:
        window = stats["token_timestamps"][-1] - stats["token_timestamps"][0]
        if window > 0:
            stats["tokens_per_sec"] = (len(stats["token_timestamps"]) - 1) / window

    # -- Direct DOM mutation -------------------------------------------------
    display = _token_displays.get(crew_id)
    if display is not None:
        try:
            style_cls = "token-thinking" if is_thinking else "token-answer"
            escaped = _escape_html(token_text)
            span = f'<span class="{style_cls}">{escaped}</span>'
            current = getattr(display, "content", "") or ""
            display.set_content(current + span)
        except Exception:
            pass  # Graceful degradation when UI element is not connected.

    # -- Update metric labels ------------------------------------------------
    tps_label = _tps_labels.get(crew_id)
    if tps_label is not None:
        try:
            tps_label.set_text(f"{stats['tokens_per_sec']:.1f} tokens/sec")
        except Exception:
            pass

    count_label = _token_count_labels.get(crew_id)
    if count_label is not None:
        try:
            count_label.set_text(f"{stats['token_count']} tokens")
        except Exception:
            pass


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
        "agent": "",
    })

    if event.get("type") == "resource.update":
        entry["tokens_in"] = event.get("tokens_in", 0)
        entry["tokens_out"] = event.get("tokens_out", 0)
        entry["cost"] = event.get("cost")
        entry["duration"] = event.get("duration", 0.0)
        entry["iterations"] = event.get("iterations", 0)
        entry["model"] = event.get("model", "")
        if event.get("agent_role"):
            entry["agent"] = event["agent_role"]


def _update_errors(crew_id: str, event: ProtocolEvent) -> None:
    """Append an error entry — guardrail retry or task/crew failure.

    Each entry carries a ``type`` field (``guardrail_retry`` | ``error``)
    so the renderer can differentiate retry counters from final failures.
    """
    errors = _errors.setdefault(crew_id, [])
    event_type = event.get("type", "")

    if event_type == "guardrail.failed":
        errors.append({
            "ts": event.get("ts", time.time()),
            "type": "guardrail_retry",
            "guardrail_name": event.get("guardrail_name", ""),
            "task_name": event.get("task_name", ""),
            "agent_role": event.get("agent_role", ""),
            "message": event.get("error", "Guardrail validation failed"),
            "attempt": event.get("attempt", 1),
            "max_retries": event.get("max_retries", 3),
            "traceback": event.get("traceback"),
        })
    elif event_type in ("task.failed", "crew.error"):
        errors.append({
            "ts": event.get("ts", time.time()),
            "type": "error",
            "task_name": event.get("task_name", ""),
            "agent_role": event.get("agent_role", ""),
            "message": event.get("error", "Unknown error"),
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
#  Meso Panel — agent / tool / delegation / memory / knowledge cards
# ═══════════════════════════════════════════════════════════════════════════


def _activity_card_border(status: str) -> str:
    """Map a card status to a left-border colour class."""
    return {
        "running": "border-l-4 border-blue",
        "completed": "border-l-4 border-green",
        "error": "border-l-4 border-red",
    }.get(status, "border-l-4 border-grey")


def _render_activity_card(activity: dict[str, Any]) -> None:
    """Render a single meso activity card based on its ``type`` field."""
    card_type: str = activity.get("type", "")
    status = activity.get("status", "")

    # -- Agent card -----------------------------------------------------------
    if card_type == "agent":
        with ui.card().classes(f"mb-2 p-3 {_activity_card_border(status)}"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("person", color="blue").classes("text-h6")
                ui.label(
                    f"Agent: {activity.get('agent_role', 'Unknown')}"
                ).classes("font-bold")
            with ui.row().classes("items-center gap-2 q-mt-xs"):
                ui.label(f"Status: {status}").classes("text-caption")
            if activity.get("task_name"):
                ui.label(
                    f"Task: {activity['task_name']}"
                ).classes("text-caption text-grey")

    # -- Tool card ------------------------------------------------------------
    elif card_type == "tool":
        with ui.card().classes(f"mb-2 p-3 {_activity_card_border(status)}"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("build", color="orange").classes("text-h6")
                tool_name = activity.get("tool_name") or activity.get(
                    "result_summary", "Unknown"
                )[:60]
                ui.label(f"Tool: {tool_name}").classes("font-bold")
            with ui.row().classes("items-center gap-2 q-mt-xs"):
                ui.label(f"Status: {status}").classes("text-caption")
            if activity.get("duration_ms"):
                ui.label(
                    f"Duration: {activity['duration_ms']}ms"
                ).classes("text-caption")
            params = activity.get("params")
            if params:
                ui.label(
                    f"Input: {_truncate_dict_repr(params)}"
                ).classes("text-xs text-grey")
            result = activity.get("result_summary")
            if result:
                ui.label(f"Result: {result}").classes("text-xs")
            error = activity.get("error")
            if error:
                ui.label(f"Error: {error}").classes("text-xs text-red")
            elapsed = activity.get("elapsed_ms")
            if elapsed:
                msg = activity.get("status_message", "")
                ui.label(f"Running: {elapsed}ms {msg}").classes(
                    "text-caption text-grey"
                )

    # -- Delegation card ------------------------------------------------------
    elif card_type == "delegation":
        with ui.card().classes(f"mb-2 p-3 {_activity_card_border(status)}"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("swap_horiz", color="purple").classes("text-h6")
                ui.label("Delegation").classes("font-bold")
            ui.label(
                f"From: {activity.get('from_agent', '?')} "
                f"→ To: {activity.get('to_agent', '?')}"
            ).classes("text-body2 q-mt-xs")
            ui.label(f"Status: {status}").classes("text-caption")
            ctx = activity.get("context")
            if ctx:
                ui.label(f"Context: {ctx}").classes("text-xs text-grey")
            response = activity.get("response")
            if response:
                ui.label(f"Response: {response}").classes("text-xs")

    # -- Memory card ----------------------------------------------------------
    elif card_type == "memory":
        with ui.card().classes("mb-2 p-3 border-l-4 border-teal"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("memory", color="teal").classes("text-h6")
                ui.label(
                    f"Memory: {activity.get('kind', 'op')}"
                ).classes("font-bold")
            query = activity.get("query")
            if query:
                ui.label(f"Query: {query}").classes("text-caption")
            query_time = activity.get("query_time_ms")
            if query_time:
                ui.label(f"Time: {query_time}ms").classes("text-caption")

    # -- Knowledge card -------------------------------------------------------
    elif card_type == "knowledge":
        with ui.card().classes("mb-2 p-3 border-l-4 border-indigo"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("lightbulb", color="indigo").classes("text-h6")
                ui.label(
                    f"Knowledge: {activity.get('kind', 'op')}"
                ).classes("font-bold")
            query = activity.get("query")
            if query:
                ui.label(f"Query: {query}").classes("text-caption")
            chunks = activity.get("chunks")
            if chunks:
                ui.label(f"Chunks: {chunks}").classes("text-caption")


def _truncate_dict_repr(obj: object, max_len: int = 120) -> str:
    """Safe truncated representation of an object for inline card display."""
    try:
        s = repr(obj)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


@ui.refreshable
def _render_meso(crew_id: str) -> None:
    """Render the meso-layer activity log: agent, tool, delegation, memory,
    and knowledge cards in a scrollable panel, newest-first.

    Each card is driven by the events stored in ``_activity_log[crew_id]``.
    """
    activities = _activity_log.get(crew_id, [])

    with ui.card().classes("w-full q-pa-md rounded-borders"):
        ui.label("Activity Log").classes("text-h6 font-bold q-mb-sm")

        if not activities:
            with ui.row().classes("items-center gap-2"):
                ui.icon("list", color="grey-5")
                ui.label("No activity yet").classes("text-caption text-grey")
            return

        with ui.scroll_area().classes("h-96"):
            for activity in reversed(activities):
                _render_activity_card(activity)


# ═══════════════════════════════════════════════════════════════════════════
#  Micro Panel — token-level streaming with direct DOM mutation
# ═══════════════════════════════════════════════════════════════════════════


def _render_micro(crew_id: str) -> None:
    """Render the micro-layer token streaming panel.

    This panel is **not** ``@ui.refreshable`` — it uses direct DOM mutation
    via ``_handle_token`` to append styled ``<span>`` elements to a ``ui.html``
    container.  On initial render any already-buffered tokens are replayed
    into the display so the panel is not blank after a reconnect.

    Styles are injected once via ``ui.add_head_html`` and follow the
    ``styles.Token.THINKING`` / ``styles.Token.ANSWER`` colour palette.
    """
    global _head_html_injected

    if not _head_html_injected:
        ui.add_head_html(
            "<style>"
            ".token-thinking { font-style: italic; opacity: 0.7; color: #757575; }"
            ".token-answer { opacity: 1; color: #212121; }"
            ".token-stream { font-family: monospace; white-space: pre-wrap; "
            "word-break: break-word; }"
            "</style>"
        )
        _head_html_injected = True

    with ui.card().classes("w-full q-pa-md rounded-borders"):
        # -- Header with metrics ----------------------------------------------
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Token Stream").classes("text-h6 font-bold")

            with ui.row().classes("items-center gap-4"):
                tps_label = ui.label("0.0 tokens/sec").classes(
                    "text-caption text-grey"
                )
                _tps_labels[crew_id] = tps_label

                count_label = ui.label("0 tokens").classes(
                    "text-caption text-grey"
                )
                _token_count_labels[crew_id] = count_label

        # -- Streaming container (direct mutation target) --------------------
        with ui.scroll_area().classes("h-64 border rounded p-2 q-mt-sm"):
            token_html = ui.html("").classes("token-stream")
            _token_displays[crew_id] = token_html

        # -- Replay buffered tokens into the initial display ------------------
        tokens = _token_elements.get(crew_id, [])
        if tokens:
            parts: list[str] = []
            for t in tokens:
                cls = "token-thinking" if t.get("is_thinking") else "token-answer"
                parts.append(
                    f'<span class="{cls}">{_escape_html(t.get("text", ""))}</span>'
                )
            try:
                token_html.set_content("".join(parts))
            except Exception:
                pass

        # -- Restore metric values from stats ---------------------------------
        stats = _token_stats.get(crew_id, {})
        if stats.get("tokens_per_sec"):
            try:
                tps_label.set_text(
                    f"{stats['tokens_per_sec']:.1f} tokens/sec"
                )
            except Exception:
                pass
        if stats.get("token_count"):
            try:
                count_label.set_text(f"{stats['token_count']} tokens")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
#  Resource Panel — per-task consumption table
# ═══════════════════════════════════════════════════════════════════════════


@ui.refreshable
def _render_resource(crew_id: str) -> None:
    """Render the per-task resource consumption table.

    Columns: Task, Agent, Duration, Tokens In, Tokens Out, Est. Cost, Iterations.
    Cost shows "—" when no exact ``resource.update`` has arrived.
    A total-cost summary is displayed below the table.
    """
    resources = _resources.get(crew_id, {})

    with ui.card().classes("w-full q-pa-md rounded-borders"):
        ui.label("Resource Consumption").classes("text-h6 font-bold q-mb-sm")

        if not resources:
            with ui.row().classes("items-center gap-2"):
                ui.icon("receipt_long", color="grey-5")
                ui.label("No resource data yet").classes(
                    "text-caption text-grey"
                )
            return

        # -- Build table rows ------------------------------------------------
        columns: list[dict[str, str]] = [
            {"name": "task", "label": "Task", "field": "task", "align": "left"},
            {"name": "agent", "label": "Agent", "field": "agent", "align": "left"},
            {"name": "duration", "label": "Duration", "field": "duration"},
            {"name": "tokens_in", "label": "Tokens In", "field": "tokens_in"},
            {"name": "tokens_out", "label": "Tokens Out", "field": "tokens_out"},
            {"name": "cost", "label": "Est. Cost", "field": "cost"},
            {"name": "iterations", "label": "Iters", "field": "iterations"},
        ]

        total_cost = 0.0
        rows: list[dict[str, Any]] = []
        for task_name, data in resources.items():
            cost = data.get("cost")
            cost_display = f"${cost:.4f}" if cost is not None else "—"
            if cost is not None:
                total_cost += cost

            rows.append({
                "task": task_name,
                "agent": data.get("agent", data.get("agent_role", "")),
                "duration": _fmt_duration(data.get("duration", 0)),
                "tokens_in": data.get("tokens_in", 0),
                "tokens_out": data.get("tokens_out", 0),
                "cost": cost_display,
                "iterations": data.get("iterations", 0),
            })

        ui.table(columns=columns, rows=rows).classes("w-full q-mb-sm")

        # -- Total cost -------------------------------------------------------
        total_display = f"${total_cost:.4f}" if total_cost > 0 else "—"
        with ui.row().classes("w-full justify-end"):
            ui.label(f"Total Cost: {total_display}").classes(
                "font-bold text-green-700"
            )


def _fmt_duration(duration: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    if duration >= 60:
        return f"{duration / 60:.1f}m"
    if duration >= 1:
        return f"{duration:.1f}s"
    return f"{int(duration * 1000)}ms"


# ═══════════════════════════════════════════════════════════════════════════
#  Error Panel — guardrail retries + task failures
# ═══════════════════════════════════════════════════════════════════════════


@ui.refreshable
def _render_error(crew_id: str) -> None:
    """Render error cards with tracebacks and guardrail retry counters.

    Only explicit guardrail retries and task/crew failures are displayed.
    Internal CrewAI tool retries are naturally filtered — they never reach
    ``_update_errors``.
    """
    errors = _errors.get(crew_id, [])

    with ui.card().classes("w-full q-pa-md rounded-borders"):
        ui.label("Errors & Retries").classes("text-h6 font-bold q-mb-sm")

        if not errors:
            with ui.row().classes("items-center gap-2"):
                ui.icon("check_circle", color="grey-5")
                ui.label("No errors").classes("text-caption text-grey")
            return

        # Newest first
        for entry in reversed(errors):
            entry_type = entry.get("type", "error")

            if entry_type == "guardrail_retry":
                _render_guardrail_card(entry)
            else:
                _render_error_card(entry)


def _render_guardrail_card(entry: dict[str, Any]) -> None:
    """Render a guardrail retry card with attempt counter."""
    attempt = entry.get("attempt", 1)
    max_retries = entry.get("max_retries", 3)

    with ui.card().classes("mb-2 p-3 border-l-4 border-orange-500 bg-orange-50"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("refresh", color="orange").classes("text-h6")
            ui.label(
                f"Guardrail Retry ({attempt}/{max_retries})"
            ).classes("font-bold text-orange-700")

        if entry.get("guardrail_name"):
            ui.label(
                f"Guardrail: {entry['guardrail_name']}"
            ).classes("text-caption text-orange-600")

        if entry.get("agent_role") or entry.get("task_name"):
            parts = []
            if entry.get("agent_role"):
                parts.append(f"Agent: {entry['agent_role']}")
            if entry.get("task_name"):
                parts.append(f"Task: {entry['task_name']}")
            ui.label(" · ".join(parts)).classes("text-xs text-grey")

        ui.label(entry.get("message", "")).classes("text-sm q-mt-xs")

        traceback = entry.get("traceback")
        if traceback:
            with ui.expansion("Show traceback").classes("text-xs"):
                ui.label(traceback).classes(
                    "font-mono text-xs whitespace-pre-wrap"
                )


def _render_error_card(entry: dict[str, Any]) -> None:
    """Render a task/crew error card with traceback."""
    with ui.card().classes("mb-2 p-3 border-l-4 border-red-500 bg-red-50"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("error", color="red").classes("text-h6")
            ui.label("Error").classes("font-bold text-red-700")

        parts = []
        if entry.get("agent_role"):
            parts.append(f"Agent: {entry['agent_role']}")
        if entry.get("task_name"):
            parts.append(f"Task: {entry['task_name']}")
        if parts:
            ui.label(" · ".join(parts)).classes("text-xs text-grey")

        ui.label(entry.get("message", "")).classes("text-sm q-mt-xs")

        traceback = entry.get("traceback")
        if traceback:
            with ui.expansion("Show traceback").classes("text-xs"):
                ui.label(traceback).classes(
                    "font-mono text-xs whitespace-pre-wrap"
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

    # Macro layer — crew pipeline and progress
    _render_macro(crew_id)

    ui.separator()

    # Meso layer — agent / tool / delegation / memory / knowledge cards
    _render_meso(crew_id)

    ui.separator()

    # Micro layer — token streaming (direct DOM mutation)
    _render_micro(crew_id)

    ui.separator()

    # Resource layer — per-task consumption table
    _render_resource(crew_id)

    ui.separator()

    # Error layer — guardrail retries + task failures
    _render_error(crew_id)
