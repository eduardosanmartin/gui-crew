"""Canvas DAG Editor for gui-crew — visual crew topology editing.

Renders crew structure as a visual graph with agent (circle) and task
(rectangle) nodes connected by directed edges representing task
dependencies.  Provides a palette for node creation, edge management,
auto-layout, zoom/pan controls, DAG validation, and an undo/redo stack.

State lives in ``app.storage.user["canvas_state"]`` and the canvas
bidirectionally syncs with the builder via the shared ``crew_model``.

Architecture
------------
- **Visual layer**: ``ui.html()`` renders an SVG overlay for edges plus
  absolutely-positioned HTML cards for nodes — all inside a scrollable
  container.  No external JavaScript dependencies.
- **Interaction layer**: NiceGUI buttons, dialogs and event handlers
  drive all operations server-side.  A lightweight JS bridge captures
  canvas clicks.
- **State layer**: Pure-Python graph representation with nodes, edges,
  positions, zoom, selection, and undo/redo stacks.
"""

from __future__ import annotations

import html
from collections import deque
from typing import Any

from nicegui import app, ui

from styles import THEME

# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

AGENT_NODE_W: int = 150
AGENT_NODE_H: int = 90
TASK_NODE_W: int = 180
TASK_NODE_H: int = 100
CANVAS_W: int = 4000
CANVAS_H: int = 3000
GRID_GAP: int = 40
MAX_UNDO: int = 50

AGENT_COLOR: str = "#1976D2"
TASK_COLOR: str = "#26A69A"
EDGE_COLOR: str = "#757575"
ERROR_COLOR: str = "#E53935"
SELECTED_COLOR: str = "#FFC107"

# Sentinels for edge-creation result
_EDGE_SELF: str = "_edge:self"
_EDGE_DUPE: str = "_edge:dupe"

# ═══════════════════════════════════════════════════════════════════════════
#  State helpers
# ═══════════════════════════════════════════════════════════════════════════


def _get_state() -> dict[str, Any]:
    """Return the current canvas state from user storage, initialising
    it if missing."""
    if "canvas_state" not in app.storage.user:
        app.storage.user["canvas_state"] = {
            "nodes": [],
            "edges": [],
            "zoom": 1.0,
            "selected_id": None,
            "connecting_from": None,
            "undo_stack": [],
            "redo_stack": [],
            "node_counter": 0,
            "edge_counter": 0,
        }
    return app.storage.user["canvas_state"]  # type: ignore[no-any-return]


def _save_state(state: dict[str, Any]) -> None:
    """Persist canvas state back to user storage."""
    app.storage.user["canvas_state"] = state


def _node_w(state: dict[str, Any], node: dict[str, Any]) -> int:
    """Return the width of *node*."""
    return node.get("w", AGENT_NODE_W if node["type"] == "agent" else TASK_NODE_W)


def _node_h(state: dict[str, Any], node: dict[str, Any]) -> int:
    """Return the height of *node*."""
    return node.get("h", AGENT_NODE_H if node["type"] == "agent" else TASK_NODE_H)


def _node_center(
    state: dict[str, Any], node: dict[str, Any]
) -> tuple[int, int]:
    """Return (cx, cy) pixel centre of *node*."""
    return (
        node["x"] + _node_w(state, node) // 2,
        node["y"] + _node_h(state, node) // 2,
    )


def _find_node(
    state: dict[str, Any], node_id: str
) -> dict[str, Any] | None:
    """Return the node dict for *node_id* or ``None``."""
    for n in state["nodes"]:
        if n["id"] == node_id:
            return n
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Undo / Redo
# ═══════════════════════════════════════════════════════════════════════════


def _push_undo(state: dict[str, Any]) -> None:
    """Snapshot the mutable parts of *state* and push onto the undo stack."""
    snapshot: dict[str, Any] = {
        "nodes": [dict(n) for n in state["nodes"]],
        "edges": [dict(e) for e in state["edges"]],
        "selected_id": state["selected_id"],
    }
    stack: list[dict[str, Any]] = state["undo_stack"]
    stack.append(snapshot)
    if len(stack) > MAX_UNDO:
        stack.pop(0)
    state["redo_stack"].clear()


def undo(state: dict[str, Any]) -> bool:
    """Pop from the undo stack and apply the snapshot.  Returns ``True`` on
    success, ``False`` when the stack is empty."""
    undo_stack: list[dict[str, Any]] = state["undo_stack"]
    if not undo_stack:
        return False
    current: dict[str, Any] = {
        "nodes": [dict(n) for n in state["nodes"]],
        "edges": [dict(e) for e in state["edges"]],
        "selected_id": state["selected_id"],
    }
    state["redo_stack"].append(current)
    snapshot = undo_stack.pop()
    state["nodes"] = snapshot["nodes"]
    state["edges"] = snapshot["edges"]
    state["selected_id"] = snapshot["selected_id"]
    _save_state(state)
    return True


def redo(state: dict[str, Any]) -> bool:
    """Pop from the redo stack and apply the snapshot.  Returns ``True`` on
    success, ``False`` when the stack is empty."""
    redo_stack: list[dict[str, Any]] = state["redo_stack"]
    if not redo_stack:
        return False
    current: dict[str, Any] = {
        "nodes": [dict(n) for n in state["nodes"]],
        "edges": [dict(e) for e in state["edges"]],
        "selected_id": state["selected_id"],
    }
    state["undo_stack"].append(current)
    snapshot = redo_stack.pop()
    state["nodes"] = snapshot["nodes"]
    state["edges"] = snapshot["edges"]
    state["selected_id"] = snapshot["selected_id"]
    _save_state(state)
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  DAG Validation  (Kahn's algorithm — O(V+E))
# ═══════════════════════════════════════════════════════════════════════════


def detect_cycle(state: dict[str, Any]) -> list[str] | None:
    """Check the task-DAG for directed cycles.

    Returns a cycle path (list of node ids) if one exists, otherwise
    ``None``.
    """
    task_ids: set[str] = {
        n["id"] for n in state["nodes"] if n["type"] == "task"
    }
    task_edges: list[dict[str, Any]] = [
        e for e in state["edges"]
        if e["from"] in task_ids and e["to"] in task_ids
    ]
    if len(task_edges) <= 1:
        return None

    adj: dict[str, list[str]] = {tid: [] for tid in task_ids}
    in_degree: dict[str, int] = {tid: 0 for tid in task_ids}
    for e in task_edges:
        adj[e["from"]].append(e["to"])
        in_degree[e["to"]] += 1

    q = deque(tid for tid, d in in_degree.items() if d == 0)
    processed = 0
    while q:
        node = q.popleft()
        processed += 1
        for neighbour in adj[node]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                q.append(neighbour)

    if processed == len(task_ids):
        return None

    # Find one cycle via DFS for the error message
    remaining = {tid for tid, d in in_degree.items() if d > 0}
    path: list[str] = []
    visited: set[str] = set()

    def _dfs(n: str) -> list[str] | None:
        if n in path:
            idx = path.index(n)
            return path[idx:] + [n]
        if n in visited:
            return None
        visited.add(n)
        path.append(n)
        for neighbour in adj.get(n, []):
            result = _dfs(neighbour)
            if result:
                return result
        path.pop()
        return None

    for start in remaining:
        cycle = _dfs(start)
        if cycle:
            return cycle

    return None


def _mark_cycle_edges(state: dict[str, Any]) -> None:
    """Update edge ``cycle`` flags based on DAG validation."""
    for e in state["edges"]:
        e["cycle"] = False
    cycle = detect_cycle(state)
    if not cycle:
        return
    cycle_edges: set[tuple[str, str]] = set()
    for i in range(len(cycle) - 1):
        cycle_edges.add((cycle[i], cycle[i + 1]))
    for e in state["edges"]:
        if (e["from"], e["to"]) in cycle_edges:
            e["cycle"] = True


# ═══════════════════════════════════════════════════════════════════════════
#  Auto-Layout  (top-down hierarchical)
# ═══════════════════════════════════════════════════════════════════════════


def auto_layout(state: dict[str, Any]) -> None:
    """Apply a top-down hierarchical layout to all nodes.

    Algorithm: topological sort of task nodes to assign layers (ranks),
    then distribute task nodes within layers.  Agent nodes are placed
    to the left of their assigned tasks.
    """
    _push_undo(state)

    task_nodes = [
        n for n in state["nodes"] if n["type"] == "task"
    ]
    agent_nodes = [
        n for n in state["nodes"] if n["type"] == "agent"
    ]
    task_ids = {n["id"] for n in task_nodes}

    # Build adjacency on task ids
    adj: dict[str, list[str]] = {n["id"]: [] for n in task_nodes}
    in_deg: dict[str, int] = {n["id"]: 0 for n in task_nodes}
    for e in state["edges"]:
        if e["from"] in task_ids and e["to"] in task_ids:
            adj[e["from"]].append(e["to"])
            in_deg[e["to"]] += 1

    # Topological sort → layers
    q = deque(tid for tid, d in in_deg.items() if d == 0)
    layers: list[list[str]] = []
    layer_map: dict[str, int] = {}
    while q:
        layer: list[str] = []
        for _ in range(len(q)):
            nid = q.popleft()
            layer.append(nid)
        layers.append(layer)
        for nid in layer:
            layer_map[nid] = len(layers) - 1
            for nb in adj[nid]:
                in_deg[nb] -= 1
                if in_deg[nb] == 0:
                    q.append(nb)

    remaining = [n for n in task_nodes if n["id"] not in layer_map]
    if remaining:
        layers.append([n["id"] for n in remaining])

    # Position task nodes
    start_x = 300
    start_y = 80
    for layer_idx, layer in enumerate(layers):
        y = start_y + layer_idx * (TASK_NODE_H + GRID_GAP)
        for pos, nid in enumerate(layer):
            node = _find_node(state, nid)
            if node:
                node["x"] = start_x + pos * (TASK_NODE_W + GRID_GAP)
                node["y"] = y

    # Agent nodes: stacked on the left
    for idx, agent in enumerate(agent_nodes):
        agent["x"] = 50
        agent["y"] = start_y + idx * (AGENT_NODE_H + GRID_GAP)

    _mark_cycle_edges(state)
    _save_state(state)


# ═══════════════════════════════════════════════════════════════════════════
#  Node / Edge CRUD
# ═══════════════════════════════════════════════════════════════════════════


def add_node(
    state: dict[str, Any], node_type: str, label: str = ""
) -> str:
    """Add a new node to the canvas.  Returns the new node id."""
    _push_undo(state)
    counter = state.get("node_counter", len(state["nodes"]))
    node_id = f"{node_type}_{counter}"
    state["node_counter"] = counter + 1
    type_count = len(
        [n for n in state["nodes"] if n["type"] == node_type]
    )
    default_label = (
        f"New Agent {type_count + 1}"
        if node_type == "agent"
        else f"New Task {type_count + 1}"
    )
    new_node: dict[str, Any] = {
        "id": node_id,
        "type": node_type,
        "x": 50 + len(state["nodes"]) * 20,
        "y": 50 + len(state["nodes"]) * 20,
        "w": AGENT_NODE_W if node_type == "agent" else TASK_NODE_W,
        "h": AGENT_NODE_H if node_type == "agent" else TASK_NODE_H,
        "label": label or default_label,
        "subtitle": node_type.capitalize(),
    }
    state["nodes"].append(new_node)
    _save_state(state)
    return node_id


def delete_node(state: dict[str, Any], node_id: str) -> bool:
    """Delete *node_id* and all edges connected to it.  Returns ``True`` on
    success."""
    _push_undo(state)
    node = _find_node(state, node_id)
    if node is None:
        if state["undo_stack"]:
            state["undo_stack"].pop()
        return False
    state["nodes"] = [n for n in state["nodes"] if n["id"] != node_id]
    state["edges"] = [
        e for e in state["edges"]
        if e["from"] != node_id and e["to"] != node_id
    ]
    if state["selected_id"] == node_id:
        state["selected_id"] = None
    state["connecting_from"] = None
    _mark_cycle_edges(state)
    _save_state(state)
    return True


def select_node(state: dict[str, Any], node_id: str | None) -> None:
    """Set the selected node."""
    state["selected_id"] = node_id
    state["connecting_from"] = None
    _save_state(state)


def create_edge(
    state: dict[str, Any], from_id: str, to_id: str
) -> str:
    """Create a directed edge ``from_id → to_id``.

    Returns the new edge id on success, or one of the sentinel strings:
    ``_EDGE_SELF`` for self-connections,
    ``_EDGE_DUPE`` for duplicates,
    ``"cycle:<path>"`` when the edge would create a cycle.
    """
    if from_id == to_id:
        return _EDGE_SELF

    for e in state["edges"]:
        if e["from"] == from_id and e["to"] == to_id:
            return _EDGE_DUPE

    _push_undo(state)

    edge_counter = state.get("edge_counter", len(state["edges"]))
    edge_id = f"edge_{edge_counter}"
    state["edge_counter"] = edge_counter + 1
    temp_edge: dict[str, Any] = {
        "id": edge_id, "from": from_id, "to": to_id, "cycle": False,
    }
    state["edges"].append(temp_edge)

    cycle = detect_cycle(state)
    if cycle is not None:
        state["edges"].pop()
        if state["undo_stack"]:
            state["undo_stack"].pop()
        state["redo_stack"].clear()
        _save_state(state)
        return f"cycle:{' → '.join(cycle)}"

    state["connecting_from"] = None
    _mark_cycle_edges(state)
    _save_state(state)
    return edge_id


def delete_edge(state: dict[str, Any], edge_id: str) -> bool:
    """Delete an edge by id."""
    _push_undo(state)
    before = len(state["edges"])
    state["edges"] = [e for e in state["edges"] if e["id"] != edge_id]
    if len(state["edges"]) == before:
        if state["undo_stack"]:
            state["undo_stack"].pop()
        return False
    _mark_cycle_edges(state)
    _save_state(state)
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Sync with Builder (bidirectional via shared ``crew_model``)
# ═══════════════════════════════════════════════════════════════════════════


def sync_from_crew_model(state: dict[str, Any]) -> None:
    """Populate the canvas from the shared ``crew_model`` in storage.

    Agents and tasks become nodes; ``task.context`` entries become edges.
    """
    import models as _m

    raw = app.storage.user.get("crew_model")
    if raw is None:
        return

    if isinstance(raw, dict):
        try:
            crew = _m.CrewModel(**raw)
        except Exception:
            return
    elif isinstance(raw, _m.CrewModel):
        crew = raw
    else:
        return

    state["nodes"].clear()
    state["edges"].clear()

    for idx, agent in enumerate(crew.agents):
        state["nodes"].append({
            "id": f"agent_{idx}",
            "type": "agent",
            "x": 50,
            "y": 80 + idx * (AGENT_NODE_H + GRID_GAP),
            "w": AGENT_NODE_W,
            "h": AGENT_NODE_H,
            "label": agent.role,
            "subtitle": (
                agent.goal[:40] + "..."
                if len(agent.goal) > 40
                else agent.goal
            ),
        })

    for idx, task in enumerate(crew.tasks):
        node_id = f"task_{idx}"
        state["nodes"].append({
            "id": node_id,
            "type": "task",
            "x": 300 + idx * (TASK_NODE_W + GRID_GAP),
            "y": 80 + idx * (TASK_NODE_H + GRID_GAP),
            "w": TASK_NODE_W,
            "h": TASK_NODE_H,
            "label": task.name,
            "subtitle": f"Agent: {task.agent_role or 'unassigned'}",
        })

    task_name_to_id: dict[str, str] = {}
    for idx, task in enumerate(crew.tasks):
        task_name_to_id[task.name] = f"task_{idx}"

    edge_count = 0
    for idx, task in enumerate(crew.tasks):
        for ctx_name in task.context:
            if ctx_name in task_name_to_id:
                state["edges"].append({
                    "id": f"edge_{edge_count}",
                    "from": task_name_to_id[ctx_name],
                    "to": f"task_{idx}",
                    "cycle": False,
                })
                edge_count += 1

    _mark_cycle_edges(state)
    _save_state(state)


def sync_to_crew_model(state: dict[str, Any]) -> None:
    """Apply canvas mutations back to the shared ``crew_model``.

    Merges changes into existing agents/tasks instead of rebuilding
    from scratch, preserving fields that are not editable on the canvas
    (e.g. backstory, LLM, tools, memory, delegation, output_file,
    guardrails, async, human_input, markdown, extra fields).
    """
    import models as _m

    raw = app.storage.user.get("crew_model")
    if raw is None:
        return

    if isinstance(raw, dict):
        try:
            crew = _m.CrewModel(**raw)
        except Exception:
            return
    elif isinstance(raw, _m.CrewModel):
        crew = raw
    else:
        return

    # Build lookup maps from existing crew
    existing_agents: dict[str, _m.AgentModel] = {
        a.role: a for a in crew.agents
    }
    existing_tasks: dict[str, _m.TaskModel] = {
        t.name: t for t in crew.tasks
    }

    # Collect canvas node data
    node_id_to_task_name: dict[str, str] = {}
    # Build context from edges: {task_name: set(context_names)}
    edge_context: dict[str, set[str]] = {}

    for node in state["nodes"]:
        if node["type"] == "task":
            task_label = node["label"]
            node_id_to_task_name[node["id"]] = task_label
            if task_label not in edge_context:
                edge_context[task_label] = set()

    for edge in state["edges"]:
        from_name = node_id_to_task_name.get(edge["from"])
        to_name = node_id_to_task_name.get(edge["to"])
        if from_name and to_name and to_name in edge_context:
            edge_context[to_name].add(from_name)

    # Merge agents — preserve existing fields, update role/goal
    merged_agents: list[_m.AgentModel] = []
    for node in state["nodes"]:
        if node["type"] != "agent":
            continue
        role = node["label"]
        goal = node.get("subtitle", "")
        if role in existing_agents:
            agent = existing_agents[role]
            agent.role = role
            agent.goal = goal
        else:
            agent = _m.AgentModel(role=role, goal=goal)
        merged_agents.append(agent)

    # Merge tasks — preserve existing fields, update name/description/
    # expected_output/context
    merged_tasks: list[_m.TaskModel] = []
    for node in state["nodes"]:
        if node["type"] != "task":
            continue
        task_name = node["label"]
        description = node.get("subtitle", "")
        expected_output = node.get(
            "expected_output", f"Output from {task_name}"
        )
        context = sorted(edge_context.get(task_name, []))

        if task_name in existing_tasks:
            task = existing_tasks[task_name]
            task.name = task_name
            task.description = description
            task.expected_output = expected_output
            task.context = context
            # agent_role is not synced from canvas — preserve existing
        else:
            task = _m.TaskModel(
                name=task_name,
                description=description,
                expected_output=expected_output,
                context=context,
            )
        merged_tasks.append(task)

    crew.agents = merged_agents
    crew.tasks = merged_tasks
    app.storage.user["crew_model"] = crew.model_dump()


# ═══════════════════════════════════════════════════════════════════════════
#  Canvas HTML Builder
# ═══════════════════════════════════════════════════════════════════════════


def _build_canvas_html(state: dict[str, Any]) -> str:
    """Generate the full HTML string for nodes + SVG edges inside a
    zoomed / scrollable container."""
    nodes_html_parts: list[str] = []
    for node in state["nodes"]:
        color = AGENT_COLOR if node["type"] == "agent" else TASK_COLOR
        shape = (
            "border-radius:50%;"
            if node["type"] == "agent"
            else "border-radius:8px;"
        )
        selected_border = ""
        if node["id"] == state.get("selected_id"):
            selected_border = (
                f"border:3px solid {SELECTED_COLOR};"
                f"box-shadow:0 0 8px {SELECTED_COLOR}88;"
            )
        connecting_border = ""
        if node["id"] == state.get("connecting_from"):
            connecting_border = f"border:3px dashed {color};"

        w = _node_w(state, node)
        h = _node_h(state, node)
        node_id_escaped = html.escape(str(node["id"]), quote=True)
        node_label_escaped = html.escape(str(node.get("label", "")), quote=True)
        node_subtitle_escaped = html.escape(
            str(node.get("subtitle", "")), quote=True
        )
        nodes_html_parts.append(
            f'<div class="cn-node" data-id="{node_id_escaped}" '
            f'style="position:absolute;left:{node["x"]}px;top:{node["y"]}px;'
            f'width:{w}px;height:{h}px;background:{color}18;'
            f'border:2px solid {color};{shape}{selected_border}{connecting_border}'
            f'display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;cursor:pointer;box-sizing:border-box;'
            f'transition:box-shadow 0.15s;" '
            f'onclick="window._gcClick=this.dataset.id">'
            f'<div style="font-weight:700;font-size:12px;text-align:center;'
            f'padding:4px 8px;color:{color}">{node_label_escaped}</div>'
            f'<div style="font-size:10px;color:#666;text-align:center;'
            f'padding:0 8px 4px;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;max-width:{w - 16}px">'
            f'{node_subtitle_escaped}</div>'
            f"</div>"
        )

    # SVG edges
    edges_svg_parts: list[str] = [
        '<svg style="position:absolute;top:0;left:0;'
        f'width:{CANVAS_W}px;height:{CANVAS_H}px;pointer-events:none;z-index:0">'
        '<defs>'
        '<marker id="gc-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{EDGE_COLOR}"/></marker>'
        '<marker id="gc-arrow-err" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{ERROR_COLOR}"/></marker>'
        "</defs>"
    ]

    for edge in state["edges"]:
        src = _find_node(state, edge["from"])
        tgt = _find_node(state, edge["to"])
        if not src or not tgt:
            continue
        x1, y1 = _node_center(state, src)
        x2, y2 = _node_center(state, tgt)

        if x1 == x2 and y1 == y2:
            continue

        # Start from bottom edge of source → top edge of target
        sx, sy = x1, y1 + _node_h(state, src) // 2
        ex, ey = x2, y2 - _node_h(state, tgt) // 2

        is_cycle = edge.get("cycle", False)
        stroke = ERROR_COLOR if is_cycle else EDGE_COLOR
        marker = "url(#gc-arrow-err)" if is_cycle else "url(#gc-arrow)"
        stroke_w = 3 if is_cycle else 2

        edges_svg_parts.append(
            f'<line x1="{sx}" y1="{sy}" x2="{ex}" y2="{ey}" '
            f'stroke="{stroke}" stroke-width="{stroke_w}" '
            f'marker-end="{marker}" stroke-linecap="round"/>'
        )

    edges_svg_parts.append("</svg>")

    zoom = state.get("zoom", 1.0)
    return (
        f'<div id="gc-root" style="position:relative;width:{CANVAS_W}px;'
        f'height:{CANVAS_H}px;transform:scale({zoom});transform-origin:0 0;">'
        + "".join(edges_svg_parts)
        + "".join(nodes_html_parts)
        + "</div>"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  UI Components
# ═══════════════════════════════════════════════════════════════════════════


def _render_palette(state: dict[str, Any]) -> None:
    """Sidebar palette with node-type creation buttons."""
    with ui.card().classes("q-pa-sm").style("min-width: 160px;"):
        ui.label("Palette").classes(THEME.typography.CARD_TITLE)
        ui.separator()

        ui.button(
            "Add Agent",
            icon="person",
            on_click=lambda: _on_add_node(state, "agent"),
        ).props("flat align=left").classes("full-width q-mt-sm")

        ui.button(
            "Add Task",
            icon="assignment",
            on_click=lambda: _on_add_node(state, "task"),
        ).props("flat align=left").classes("full-width q-mt-xs")

        ui.separator()
        ui.label("Edge creation").classes("text-caption text-grey q-mt-sm")
        ui.label("1. Click source node to select").classes("text-caption text-grey")
        ui.label("2. Click same node again").classes("text-caption text-grey")
        ui.label("3. Click target node to connect").classes("text-caption text-grey")

        ui.button(
            "Cancel Connect",
            icon="close",
            on_click=lambda: _cancel_connect(state),
        ).props("flat size=sm").classes("full-width q-mt-sm")


def _on_add_node(state: dict[str, Any], node_type: str) -> None:
    """Add a node of *node_type* and refresh."""
    add_node(state, node_type)
    sync_to_crew_model(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


def _cancel_connect(state: dict[str, Any]) -> None:
    """Cancel edge-creation mode."""
    state["connecting_from"] = None
    _save_state(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


@ui.refreshable
def _render_toolbar() -> None:
    """Toolbar with auto-layout, zoom, undo/redo, DAG validate."""
    state = _get_state()
    with ui.row().classes("items-center gap-2 q-mb-sm"):
        ui.button(
            "Auto Layout",
            icon="auto_fix_high",
            on_click=lambda: _do_auto_layout(state),
        ).props("flat size=sm")

        ui.button(
            icon="zoom_in",
            on_click=lambda: _zoom(state, 0.1),
        ).props("flat round size=sm")

        ui.label(
            f"{int(state.get('zoom', 1.0) * 100)}%"
        ).classes("text-caption")

        ui.button(
            icon="zoom_out",
            on_click=lambda: _zoom(state, -0.1),
        ).props("flat round size=sm")

        ui.button(
            "Fit",
            icon="fit_screen",
            on_click=lambda: _fit_screen(state),
        ).props("flat size=sm")

        ui.separator().props("vertical")

        ui.button(
            icon="undo",
            on_click=lambda: _do_undo(state),
        ).props(
            f"flat round size=sm "
            f"{'' if state['undo_stack'] else 'disable'}"
        )

        ui.button(
            icon="redo",
            on_click=lambda: _do_redo(state),
        ).props(
            f"flat round size=sm "
            f"{'' if state['redo_stack'] else 'disable'}"
        )

        ui.separator().props("vertical")

        ui.button(
            "Validate DAG",
            icon="check_circle",
            on_click=lambda: _validate_and_show(state),
        ).props("flat size=sm")

        ui.separator().props("vertical")

        sel = state.get("selected_id")
        ui.button(
            "Delete Selected",
            icon="delete",
            color="negative",
            on_click=lambda: _delete_selected(state),
        ).props(f"flat size=sm {'' if sel else 'disable'}")

        ui.button(
            "Sync from Builder",
            icon="sync",
            on_click=lambda: _sync_and_refresh(state),
        ).props("flat size=sm")


def _do_auto_layout(state: dict[str, Any]) -> None:
    """Trigger auto-layout and refresh."""
    auto_layout(state)
    sync_to_crew_model(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


def _zoom(state: dict[str, Any], delta: float) -> None:
    """Adjust zoom by *delta*, clamped to [0.25, 2.0]."""
    state["zoom"] = max(0.25, min(2.0, state.get("zoom", 1.0) + delta))
    _save_state(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


def _fit_screen(state: dict[str, Any]) -> None:
    """Reset zoom to 1.0."""
    state["zoom"] = 1.0
    _save_state(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


def _do_undo(state: dict[str, Any]) -> None:
    """Perform undo and refresh."""
    if undo(state):
        sync_to_crew_model(state)
        _render_canvas_area.refresh()  # type: ignore[attr-defined]
        _render_toolbar.refresh()  # type: ignore[attr-defined]


def _do_redo(state: dict[str, Any]) -> None:
    """Perform redo and refresh."""
    if redo(state):
        sync_to_crew_model(state)
        _render_canvas_area.refresh()  # type: ignore[attr-defined]
        _render_toolbar.refresh()  # type: ignore[attr-defined]


def _validate_and_show(state: dict[str, Any]) -> None:
    """Run DAG validation and show result via a notification."""
    _mark_cycle_edges(state)
    _save_state(state)
    cycle = detect_cycle(state)
    if cycle:
        ui.notify(
            f"⚠️ DAG is invalid — cycle detected: {' → '.join(cycle)}",
            type="negative",
            position="top",
            timeout=6000,
        )
    else:
        ui.notify(
            "✅ DAG is valid — no cycles detected.",
            type="positive",
            position="top",
            timeout=3000,
        )
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


def _delete_selected(state: dict[str, Any]) -> None:
    """Delete the currently selected node (with confirmation)."""
    sel = state.get("selected_id")
    if not sel:
        return

    async def _do_delete() -> None:
        delete_node(state, sel)
        sync_to_crew_model(state)
        _render_canvas_area.refresh()  # type: ignore[attr-defined]
        _render_toolbar.refresh()  # type: ignore[attr-defined]
        dialog.close()

    with ui.dialog() as dialog, ui.card():
        ui.label("Delete Node?").classes(THEME.typography.CARD_TITLE)
        ui.label(
            f"Delete '{sel}' and all its connected edges?"
        ).classes("q-mb-md")
        with ui.row().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", color="negative", on_click=_do_delete)
    dialog.open()


def _sync_and_refresh(state: dict[str, Any]) -> None:
    """Sync nodes/edges from the crew_model and re-render."""
    sync_from_crew_model(state)
    _render_canvas_area.refresh()  # type: ignore[attr-defined]
    _render_toolbar.refresh()  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════════════
#  Canvas Area (refreshable visual)
# ═══════════════════════════════════════════════════════════════════════════


@ui.refreshable
def _render_canvas_area() -> None:
    """Render the visual DAG component — SVG edges + HTML nodes inside a
    scrollable viewport."""
    state = _get_state()
    canvas_html = _build_canvas_html(state)
    with ui.card().classes("w-full").style(
        "overflow: hidden; padding: 0; margin-top: 8px;"
    ):
        ui.html(canvas_html).style(
            "width:100%;height:600px;overflow:auto;"
            "background:#f5f5f5;border-radius:8px;position:relative;"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Node Click Handlers
# ═══════════════════════════════════════════════════════════════════════════


def _handle_node_click(state: dict[str, Any], node_id: str) -> None:
    """Handle a click on a canvas node.

    - If in edge-creation mode (``connecting_from`` set), complete the edge.
    - Otherwise, select the clicked node.
    - Clicking the already-selected node enters edge-creation mode.
    """
    if not node_id:
        return

    connecting = state.get("connecting_from")
    if connecting is not None:
        result = create_edge(state, connecting, node_id)
        if result == _EDGE_SELF:
            ui.notify(
                "❌ Cannot connect a node to itself.",
                type="warning", position="top", timeout=3000,
            )
        elif result == _EDGE_DUPE:
            ui.notify(
                "⚠️ Edge already exists.",
                type="warning", position="top", timeout=3000,
            )
        elif result.startswith("cycle:"):
            cycle_path = result.split(":", 1)[1]
            ui.notify(
                f"❌ Would create cycle: {cycle_path}",
                type="negative", position="top", timeout=5000,
            )
        else:
            sync_to_crew_model(state)
            ui.notify(
                f"✅ Edge created: {connecting} → {node_id}",
                type="positive", position="top", timeout=2000,
            )
        state["connecting_from"] = None
        _save_state(state)
        return

    if state.get("selected_id") == node_id:
        state["connecting_from"] = node_id
        ui.notify(
            f"🔗 Now click a target node to connect from '{node_id}'...",
            type="info", position="top", timeout=3000,
        )
    else:
        state["selected_id"] = node_id

    _save_state(state)


# ═══════════════════════════════════════════════════════════════════════════
#  Page Entry Point
# ═══════════════════════════════════════════════════════════════════════════

_JS_BRIDGE_INJECTED: bool = False


def _inject_js_bridge() -> None:
    """Inject the JavaScript click bridge into the page body (once)."""
    global _JS_BRIDGE_INJECTED
    if _JS_BRIDGE_INJECTED:
        return
    _JS_BRIDGE_INJECTED = True
    ui.add_body_html("""
<script>
(function(){if(window._gcBridge)return;window._gcBridge=true;
window._gcClick=null;
document.addEventListener('click',function(e){
  var n=e.target.closest('.cn-node');
  window._gcClick=n?n.dataset.id:null;
});
})();
</script>
""")


def render_canvas() -> None:
    """Render the full Canvas page: toolbar + palette + DAG visual area.

    Call this inside an ``app.py`` ``render_page()`` content callback.
    """
    state = _get_state()

    # Sync from crew_model on first load if canvas is empty
    if not state["nodes"]:
        sync_from_crew_model(state)

    # Inject JS click bridge (once)
    _inject_js_bridge()

    # Poll for node clicks
    async def _poll_click() -> None:
        js = "var c=window._gcClick;window._gcClick=null;return c||'';"
        result = await ui.run_javascript(js, respond=True)
        if result and isinstance(result, str) and result.strip():
            _handle_node_click(state, result)
            _render_canvas_area.refresh()  # type: ignore[attr-defined]
            _render_toolbar.refresh()  # type: ignore[attr-defined]

    ui.timer(0.3, _poll_click)

    with ui.row().classes("w-full items-start gap-0"):
        _render_palette(state)
        with ui.column().classes("flex-1 q-pl-md"):
            _render_toolbar()
            _render_canvas_area()
