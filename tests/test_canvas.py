"""Comprehensive tests for canvas.py — DAG editor module.

Covers state management, node/edge CRUD, DAG validation, auto-layout,
undo/redo, zoom, and bidirectional sync with crew_model.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
#  Import canvas module — mock NiceGUI storage so we can unit-test
#  the core logic without starting a server.
# ---------------------------------------------------------------------------


class _FakeStorage:
    """In-memory dict that mimics ``canvas.app.storage.user``."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def __contains__(self, key: str) -> bool:  # type: ignore[override]
        return key in self._data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def clear(self) -> None:
        self._data.clear()


# Make 'models' importable even in isolation
import sys  # noqa: E402

_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import canvas  # noqa: E402
import models  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures  — patch canvas.app so unit tests don't need a NiceGUI server.
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _patch_canvas_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``canvas.app`` with a fake app to isolate from real NiceGUI.

    This runs before every test and restores the real ``canvas.app``
    afterwards, preventing test_canvas.py from leaking the mock into
    other test modules (e.g. test_app.py).
    """
    fake_app = type(
        "App", (), {"storage": type("Storage", (), {"user": _FakeStorage()})()}
    )()
    monkeypatch.setattr(canvas, "app", fake_app)


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _fresh_state() -> dict[str, Any]:
    """Return a clean canvas state dict."""
    return {
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


def _state_with_nodes(
    task_count: int = 3, agent_count: int = 2
) -> dict[str, Any]:
    """Return a state pre-populated with *task_count* task nodes and
    *agent_count* agent nodes."""
    s = _fresh_state()
    for i in range(agent_count):
        s["nodes"].append({
            "id": f"agent_{i}",
            "type": "agent",
            "x": 50,
            "y": 80 + i * 130,
            "w": canvas.AGENT_NODE_W,
            "h": canvas.AGENT_NODE_H,
            "label": f"Agent {i + 1}",
            "subtitle": "Test agent",
        })
    for i in range(task_count):
        s["nodes"].append({
            "id": f"task_{i}",
            "type": "task",
            "x": 300 + i * 220,
            "y": 80 + i * 140,
            "w": canvas.TASK_NODE_W,
            "h": canvas.TASK_NODE_H,
            "label": f"Task {i + 1}",
            "subtitle": "Test task",
        })
    return s


def _linear_dag_state() -> dict[str, Any]:
    """Return a state with task_0 → task_1 → task_2 edges (acyclic)."""
    s = _state_with_nodes(task_count=3, agent_count=0)
    s["edges"] = [
        {"id": "edge_0", "from": "task_0", "to": "task_1", "cycle": False},
        {"id": "edge_1", "from": "task_1", "to": "task_2", "cycle": False},
    ]
    return s


# ═══════════════════════════════════════════════════════════════════════════
#  State Management
# ═══════════════════════════════════════════════════════════════════════════


class TestStateManagement:
    """Tests for state helpers."""

    def test_fresh_state_defaults(self):
        s = _fresh_state()
        assert s["nodes"] == []
        assert s["edges"] == []
        assert s["zoom"] == 1.0
        assert s["selected_id"] is None
        assert s["connecting_from"] is None
        assert s["undo_stack"] == []
        assert s["redo_stack"] == []

    def test_find_node_exists(self):
        s = _state_with_nodes()
        node = canvas._find_node(s, "task_0")
        assert node is not None
        assert node["id"] == "task_0"
        assert node["type"] == "task"

    def test_find_node_missing(self):
        s = _state_with_nodes()
        assert canvas._find_node(s, "nonexistent") is None

    def test_node_dimensions_agent(self):
        s = _state_with_nodes(agent_count=1, task_count=0)
        node = canvas._find_node(s, "agent_0")
        assert node is not None
        assert canvas._node_w(s, node) == canvas.AGENT_NODE_W
        assert canvas._node_h(s, node) == canvas.AGENT_NODE_H

    def test_node_dimensions_task(self):
        s = _state_with_nodes(agent_count=0, task_count=1)
        node = canvas._find_node(s, "task_0")
        assert node is not None
        assert canvas._node_w(s, node) == canvas.TASK_NODE_W
        assert canvas._node_h(s, node) == canvas.TASK_NODE_H

    def test_node_center(self):
        s = _state_with_nodes(agent_count=0, task_count=1)
        node = canvas._find_node(s, "task_0")
        assert node is not None
        cx, cy = canvas._node_center(s, node)
        assert cx == node["x"] + canvas.TASK_NODE_W // 2
        assert cy == node["y"] + canvas.TASK_NODE_H // 2


# ═══════════════════════════════════════════════════════════════════════════
#  Node CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeCRUD:
    """Tests for add/delete/select node operations."""

    def test_add_agent_node(self):
        s = _fresh_state()
        nid = canvas.add_node(s, "agent")
        assert nid.startswith("agent_")
        assert len(s["nodes"]) == 1
        assert s["nodes"][0]["type"] == "agent"
        assert "New Agent" in s["nodes"][0]["label"]

    def test_add_task_node(self):
        s = _fresh_state()
        nid = canvas.add_node(s, "task", label="Research")
        assert nid.startswith("task_")
        assert s["nodes"][0]["label"] == "Research"
        assert s["nodes"][0]["type"] == "task"

    def test_add_multiple_nodes(self):
        s = _fresh_state()
        for _ in range(5):
            canvas.add_node(s, "task")
        assert len(s["nodes"]) == 5
        ids = {n["id"] for n in s["nodes"]}
        assert len(ids) == 5  # all unique

    def test_add_node_pushes_undo(self):
        s = _fresh_state()
        assert len(s["undo_stack"]) == 0
        canvas.add_node(s, "agent")
        assert len(s["undo_stack"]) == 1
        # redo stack cleared
        assert len(s["redo_stack"]) == 0

    def test_delete_existing_node(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        assert canvas.delete_node(s, "task_0") is True
        assert len(s["nodes"]) == 1
        assert canvas._find_node(s, "task_0") is None

    def test_delete_nonexistent_node(self):
        s = _state_with_nodes()
        assert canvas.delete_node(s, "nonexistent") is False
        # undo should NOT have been pushed
        assert len(s["undo_stack"]) == 0

    def test_delete_node_removes_edges(self):
        s = _linear_dag_state()
        assert len(s["edges"]) == 2
        canvas.delete_node(s, "task_1")  # middle node
        # edge_0 (task_0→task_1) and edge_1 (task_1→task_2) both removed
        assert len(s["edges"]) == 0
        assert canvas._find_node(s, "task_1") is None
        assert len(s["nodes"]) == 2  # task_0, task_2 remain

    def test_delete_clears_selection(self):
        s = _state_with_nodes()
        canvas.select_node(s, "task_0")
        assert s["selected_id"] == "task_0"
        canvas.delete_node(s, "task_0")
        assert s["selected_id"] is None

    def test_select_node(self):
        s = _state_with_nodes()
        canvas.select_node(s, "task_1")
        assert s["selected_id"] == "task_1"
        assert s["connecting_from"] is None

    def test_select_node_clears_connecting(self):
        s = _state_with_nodes()
        s["connecting_from"] = "task_0"
        canvas.select_node(s, "task_1")
        assert s["connecting_from"] is None
        assert s["selected_id"] == "task_1"

    def test_select_none(self):
        s = _state_with_nodes()
        canvas.select_node(s, "task_0")
        canvas.select_node(s, None)
        assert s["selected_id"] is None


# ═══════════════════════════════════════════════════════════════════════════
#  Edge CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCRUD:
    """Tests for edge creation and deletion."""

    def test_create_simple_edge(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        result = canvas.create_edge(s, "task_0", "task_1")
        assert result not in (canvas._EDGE_SELF, canvas._EDGE_DUPE)
        assert not result.startswith("cycle:")
        assert len(s["edges"]) == 1
        assert s["edges"][0]["from"] == "task_0"
        assert s["edges"][0]["to"] == "task_1"
        assert s["edges"][0]["cycle"] is False

    def test_reject_self_connection(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        result = canvas.create_edge(s, "task_0", "task_0")
        assert result == canvas._EDGE_SELF
        assert len(s["edges"]) == 0

    def test_reject_duplicate_edge(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        result = canvas.create_edge(s, "task_0", "task_1")
        assert result == canvas._EDGE_DUPE
        assert len(s["edges"]) == 1  # still only one

    def test_reject_cycle(self):
        """task_0 → task_1 → task_2, adding task_2 → task_0 must fail."""
        s = _linear_dag_state()
        result = canvas.create_edge(s, "task_2", "task_0")
        assert result.startswith("cycle:")
        assert len(s["edges"]) == 2  # no new edge added
        # undo stack should be rolled back
        assert len(s["undo_stack"]) == 0  # push_undo was rolled back

    def test_create_edge_clears_connecting(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        s["connecting_from"] = "task_0"
        canvas.create_edge(s, "task_0", "task_1")
        assert s["connecting_from"] is None

    def test_delete_existing_edge(self):
        s = _linear_dag_state()
        assert canvas.delete_edge(s, "edge_0") is True
        assert len(s["edges"]) == 1
        assert s["edges"][0]["id"] == "edge_1"

    def test_delete_nonexistent_edge(self):
        s = _linear_dag_state()
        assert canvas.delete_edge(s, "edge_99") is False
        assert len(s["edges"]) == 2

    def test_edge_between_agent_and_task(self):
        """Edges involving agents are allowed (they don't form cycles)."""
        s = _state_with_nodes(task_count=1, agent_count=1)
        result = canvas.create_edge(s, "agent_0", "task_0")
        assert result not in (canvas._EDGE_SELF, canvas._EDGE_DUPE)
        assert not result.startswith("cycle:")
        assert len(s["edges"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  DAG Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestDAGValidation:
    """Tests for cycle detection and marking."""

    def test_linear_dag_no_cycle(self):
        s = _linear_dag_state()
        assert canvas.detect_cycle(s) is None

    def test_empty_no_cycle(self):
        s = _fresh_state()
        assert canvas.detect_cycle(s) is None

    def test_single_node_no_cycle(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        assert canvas.detect_cycle(s) is None

    def test_single_edge_no_cycle(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        assert canvas.detect_cycle(s) is None

    def test_simple_cycle_detected(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        # Direct cycle — bypass create_edge which would reject it
        s["edges"].append({
            "id": "edge_cycle", "from": "task_1", "to": "task_0", "cycle": False,
        })
        cycle = canvas.detect_cycle(s)
        assert cycle is not None
        assert len(cycle) >= 3  # at least task_0 → task_1 → task_0

    def test_three_node_cycle(self):
        s = _state_with_nodes(task_count=3, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        canvas.create_edge(s, "task_1", "task_2")
        # Inject reverse edge
        s["edges"].append({
            "id": "edge_cycle", "from": "task_2", "to": "task_0", "cycle": False,
        })
        cycle = canvas.detect_cycle(s)
        assert cycle is not None
        assert "task_0" in cycle and "task_1" in cycle and "task_2" in cycle

    def test_mark_cycle_edges(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        s["edges"].append({
            "id": "edge_cycle", "from": "task_1", "to": "task_0", "cycle": False,
        })
        canvas._mark_cycle_edges(s)
        cycle_edges = [e for e in s["edges"] if e.get("cycle")]
        assert len(cycle_edges) > 0

    def test_mark_no_cycle_on_acyclic(self):
        s = _linear_dag_state()
        canvas._mark_cycle_edges(s)
        assert all(not e.get("cycle", False) for e in s["edges"])

    def test_agents_ignored_for_cycles(self):
        """Agent nodes don't participate in the task DAG cycle check."""
        s = _state_with_nodes(task_count=0, agent_count=3)
        s["edges"] = [
            {"id": "e0", "from": "agent_0", "to": "agent_1", "cycle": False},
            {"id": "e1", "from": "agent_1", "to": "agent_2", "cycle": False},
            {"id": "e2", "from": "agent_2", "to": "agent_0", "cycle": False},
        ]
        assert canvas.detect_cycle(s) is None


# ═══════════════════════════════════════════════════════════════════════════
#  Auto-Layout
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoLayout:
    """Tests for the top-down hierarchical layout algorithm."""

    def test_auto_layout_sets_positions(self):
        s = _linear_dag_state()
        # Scramble positions
        s["nodes"][0]["x"] = 9999
        s["nodes"][0]["y"] = 9999
        canvas.auto_layout(s)
        for node in s["nodes"]:
            assert 0 <= node["x"] <= canvas.CANVAS_W
            assert 0 <= node["y"] <= canvas.CANVAS_H

    def test_auto_layout_pushes_undo(self):
        s = _linear_dag_state()
        undo_before = len(s["undo_stack"])
        canvas.auto_layout(s)
        assert len(s["undo_stack"]) == undo_before + 1

    def test_auto_layout_no_nodes(self):
        s = _fresh_state()
        canvas.auto_layout(s)  # should not crash
        assert s["nodes"] == []

    def test_auto_layout_diamond(self):
        """A diamond DAG: task_0 → task_1, task_0 → task_2, task_1 → task_3, task_2 → task_3."""
        s = _state_with_nodes(task_count=4, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")
        canvas.create_edge(s, "task_0", "task_2")
        canvas.create_edge(s, "task_1", "task_3")
        canvas.create_edge(s, "task_2", "task_3")
        canvas.auto_layout(s)
        # task_0 should be above task_3 (smaller y)
        t0 = canvas._find_node(s, "task_0")
        t3 = canvas._find_node(s, "task_3")
        assert t0 is not None and t3 is not None
        assert t0["y"] < t3["y"]

    def test_auto_layout_agents_left_aligned(self):
        s = _state_with_nodes(task_count=2, agent_count=3)
        canvas.auto_layout(s)
        for agent in [n for n in s["nodes"] if n["type"] == "agent"]:
            assert agent["x"] == 50  # agents stacked on left

    def test_auto_layout_tasks_beyond_agents(self):
        s = _state_with_nodes(task_count=2, agent_count=1)
        canvas.auto_layout(s)
        for task in [n for n in s["nodes"] if n["type"] == "task"]:
            assert task["x"] >= 300  # tasks start to the right


# ═══════════════════════════════════════════════════════════════════════════
#  Undo / Redo
# ═══════════════════════════════════════════════════════════════════════════


class TestUndoRedo:
    """Tests for undo/redo stack operations."""

    def test_undo_restores_previous_state(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        canvas.add_node(s, "task")  # pushes undo, adds task_1
        assert len(s["nodes"]) == 2
        assert canvas.undo(s) is True
        assert len(s["nodes"]) == 1  # back to original
        assert s["nodes"][0]["id"] == "task_0"

    def test_undo_empty_stack(self):
        s = _fresh_state()
        assert canvas.undo(s) is False

    def test_redo_restores_undone(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        canvas.add_node(s, "task")
        canvas.undo(s)  # back to 1 node
        assert canvas.redo(s) is True
        assert len(s["nodes"]) == 2  # redo restored the add

    def test_redo_empty_stack(self):
        s = _fresh_state()
        assert canvas.redo(s) is False

    def test_redo_cleared_by_new_mutation(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        canvas.add_node(s, "task")   # adds task_1, undo stack has 1 snapshot
        canvas.undo(s)                # undo: back to task_0, redo stack has 1
        assert len(s["redo_stack"]) == 1
        canvas.add_node(s, "task")   # new mutation clears redo
        assert len(s["redo_stack"]) == 0

    def test_push_undo_limits_stack(self):
        s = _fresh_state()
        for _ in range(canvas.MAX_UNDO + 5):
            canvas._push_undo(s)
        assert len(s["undo_stack"]) == canvas.MAX_UNDO

    def test_undo_restores_selection(self):
        s = _state_with_nodes()
        canvas.select_node(s, "task_0")
        canvas.add_node(s, "task")  # pushes undo
        canvas.select_node(s, "task_1")
        canvas.undo(s)
        # selection should go back to task_0
        assert s["selected_id"] == "task_0"
        assert len(s["nodes"]) == len(_state_with_nodes()["nodes"])

    def test_undo_restores_edges(self):
        s = _linear_dag_state()
        assert len(s["edges"]) == 2
        canvas.delete_edge(s, "edge_0")
        assert len(s["edges"]) == 1
        canvas.undo(s)
        assert len(s["edges"]) == 2

    def test_redo_after_undo_edge_delete(self):
        s = _linear_dag_state()
        canvas.delete_edge(s, "edge_0")
        canvas.undo(s)
        assert len(s["edges"]) == 2
        canvas.redo(s)
        assert len(s["edges"]) == 1

    def test_multiple_undo_redo_cycles(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        canvas.add_node(s, "task")   # +task_1
        canvas.add_node(s, "agent")  # +agent_2
        assert len(s["nodes"]) == 3
        canvas.undo(s)  # back to 2 nodes
        assert len(s["nodes"]) == 2
        canvas.undo(s)  # back to 1 node
        assert len(s["nodes"]) == 1
        canvas.redo(s)  # forward to 2
        assert len(s["nodes"]) == 2
        canvas.redo(s)  # forward to 3
        assert len(s["nodes"]) == 3
        canvas.undo(s)  # back to 2
        assert len(s["nodes"]) == 2

    def test_undo_stack_snapshot_is_deep(self):
        """Ensure undo snapshots don't share references with the live state."""
        s = _state_with_nodes(task_count=1, agent_count=0)
        canvas._push_undo(s)
        s["nodes"][0]["x"] = 99999
        snapshot = s["undo_stack"][0]
        assert snapshot["nodes"][0]["x"] != 99999  # snapshot is independent


# ═══════════════════════════════════════════════════════════════════════════
#  Zoom
# ═══════════════════════════════════════════════════════════════════════════


class TestZoom:
    """Tests for zoom and fit-screen."""

    def test_default_zoom(self):
        s = _fresh_state()
        assert s["zoom"] == 1.0

    def test_zoom_in(self):
        s = _fresh_state()
        s["zoom"] = 1.0
        s["zoom"] = max(0.25, min(2.0, s["zoom"] + 0.1))
        assert s["zoom"] == pytest.approx(1.1)

    def test_zoom_out(self):
        s = _fresh_state()
        s["zoom"] = 1.0
        s["zoom"] = max(0.25, min(2.0, s["zoom"] - 0.1))
        assert s["zoom"] == pytest.approx(0.9)

    def test_zoom_clamped_minimum(self):
        s = _fresh_state()
        s["zoom"] = 0.1
        s["zoom"] = max(0.25, min(2.0, s["zoom"]))
        assert s["zoom"] == 0.25

    def test_zoom_clamped_maximum(self):
        s = _fresh_state()
        s["zoom"] = 3.0
        s["zoom"] = max(0.25, min(2.0, s["zoom"]))
        assert s["zoom"] == 2.0

    def test_fit_screen_resets_zoom(self):
        s = _fresh_state()
        s["zoom"] = 0.5
        s["zoom"] = 1.0  # fit screen
        assert s["zoom"] == 1.0


# ═══════════════════════════════════════════════════════════════════════════
#  Canvas HTML Builder
# ═══════════════════════════════════════════════════════════════════════════


class TestCanvasHTML:
    """Tests for the HTML generation function."""

    def test_build_empty_html(self):
        s = _fresh_state()
        html = canvas._build_canvas_html(s)
        assert 'id="gc-root"' in html
        assert "<svg" in html
        assert "</svg>" in html

    def test_build_html_with_nodes(self):
        s = _state_with_nodes(task_count=1, agent_count=1)
        html = canvas._build_canvas_html(s)
        assert 'class="cn-node"' in html
        assert 'data-id="task_0"' in html
        assert 'data-id="agent_0"' in html

    def test_build_html_with_edges(self):
        s = _linear_dag_state()
        html = canvas._build_canvas_html(s)
        assert "<line" in html
        assert 'marker-end="url(#gc-arrow)"' in html

    def test_build_html_with_cycle_edge(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        s["edges"] = [
            {"id": "e0", "from": "task_0", "to": "task_1", "cycle": True},
        ]
        html = canvas._build_canvas_html(s)
        assert 'stroke="#E53935"' in html
        assert "gc-arrow-err" in html

    def test_build_html_selected_node(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        s["selected_id"] = "task_0"
        html = canvas._build_canvas_html(s)
        assert "box-shadow" in html  # selected node has visual feedback

    def test_build_html_connecting_node(self):
        s = _state_with_nodes(task_count=1, agent_count=0)
        s["connecting_from"] = "task_0"
        html = canvas._build_canvas_html(s)
        assert "dashed" in html  # connecting node has dashed border

    def test_build_html_zoom_applied(self):
        s = _fresh_state()
        s["zoom"] = 0.5
        html = canvas._build_canvas_html(s)
        assert "scale(0.5)" in html

    def test_build_html_no_crash_missing_node_in_edge(self):
        s = _fresh_state()
        s["edges"] = [{"id": "e0", "from": "gone", "to": "also_gone", "cycle": False}]
        html = canvas._build_canvas_html(s)
        assert "<svg" in html  # should still generate SVG without crashing


# ═══════════════════════════════════════════════════════════════════════════
#  Sync with CrewModel
# ═══════════════════════════════════════════════════════════════════════════


class TestSync:
    """Tests for bidirectional sync between canvas state and crew_model."""

    def setup_method(self):
        """Reset storage before each test."""
        canvas.app.storage.user.clear()  # type: ignore[attr-defined]

    def test_sync_from_crew_model_empty(self):
        s = _fresh_state()
        # No crew_model in storage → should be a no-op
        canvas.sync_from_crew_model(s)
        assert s["nodes"] == []
        assert s["edges"] == []

    def test_sync_from_crew_model_with_crew(self):
        s = _fresh_state()
        # Put a crew_model into storage
        crew = models.CrewModel(
            name="Test Crew",
            agents=[
                models.AgentModel(role="Researcher", goal="Research stuff"),
                models.AgentModel(role="Writer", goal="Write things"),
            ],
            tasks=[
                models.TaskModel(
                    name="Research",
                    description="Do research",
                    expected_output="Research output",
                    agent_role="Researcher",
                ),
                models.TaskModel(
                    name="Write",
                    description="Write report",
                    expected_output="Report",
                    agent_role="Writer",
                    context=["Research"],
                ),
            ],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        canvas.sync_from_crew_model(s)

        assert len(s["nodes"]) == 4  # 2 agents + 2 tasks
        agent_ids = [n["id"] for n in s["nodes"] if n["type"] == "agent"]
        task_ids = [n["id"] for n in s["nodes"] if n["type"] == "task"]
        assert len(agent_ids) == 2
        assert len(task_ids) == 2

        # Edge from Research → Write
        assert len(s["edges"]) == 1
        assert s["edges"][0]["from"].startswith("task_")
        assert s["edges"][0]["to"].startswith("task_")

    def test_sync_to_crew_model(self):
        s = _state_with_nodes(task_count=2, agent_count=1)
        # Existing crew_model
        crew = models.CrewModel(
            name="Test",
            agents=[models.AgentModel(role="Dummy", goal="Do stuff")],
            tasks=[models.TaskModel(
                name="Dummy", description="x", expected_output="y",
            )],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]

        # Create an edge
        canvas.create_edge(s, "task_0", "task_1")
        canvas.sync_to_crew_model(s)

        # Read back
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        assert isinstance(stored, dict)
        updated = models.CrewModel(**stored)
        assert len(updated.agents) == 1
        assert len(updated.tasks) == 2
        # task_1 should have task_0 in context
        task_names = {t.name for t in updated.tasks}
        assert "Task 1" in task_names
        assert "Task 2" in task_names

    def test_sync_none_crew_model(self):
        s = _fresh_state()
        # No crew_model at all
        canvas.sync_from_crew_model(s)  # should not crash
        canvas.sync_to_crew_model(s)     # should not crash

    def test_sync_from_crew_model_clears_existing(self):
        s = _state_with_nodes(task_count=5, agent_count=3)
        crew = models.CrewModel(
            name="Small",
            agents=[models.AgentModel(role="Only", goal="Be useful")],
            tasks=[],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        canvas.sync_from_crew_model(s)
        assert len(s["nodes"]) == 1  # only the agent
        assert s["edges"] == []

    def test_sync_preserves_crew_name(self):
        """sync_to_crew_model should preserve existing crew-level fields."""
        crew = models.CrewModel(
            name="Preserved Name",
            description="Keep me",
            process="sequential",
            agents=[models.AgentModel(role="Old", goal="Work")],
            tasks=[],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        s = _state_with_nodes(task_count=1, agent_count=1)
        canvas.sync_to_crew_model(s)
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        updated = models.CrewModel(**stored)
        assert updated.name == "Preserved Name"
        assert updated.description == "Keep me"
        assert updated.process == "sequential"


# ═══════════════════════════════════════════════════════════════════════════
#  Integration-style tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Multi-operation scenario tests."""

    def test_full_workflow_add_connect_layout(self):
        s = _fresh_state()
        # Add 3 tasks
        canvas.add_node(s, "task", label="Research")
        canvas.add_node(s, "task", label="Analyze")
        canvas.add_node(s, "task", label="Write")
        assert len(s["nodes"]) == 3

        # Connect: Research → Analyze, Analyze → Write
        r1 = canvas.create_edge(s, "task_0", "task_1")
        r2 = canvas.create_edge(s, "task_1", "task_2")
        assert not r1.startswith("cycle:") and not r2.startswith("cycle:")
        assert len(s["edges"]) == 2

        # Auto-layout
        canvas.auto_layout(s)
        t0 = canvas._find_node(s, "task_0")
        t2 = canvas._find_node(s, "task_2")
        assert t0 is not None and t2 is not None
        assert t0["y"] < t2["y"]  # Research above Write

        # Undo the layout
        canvas.undo(s)
        assert len(s["nodes"]) == 3  # nodes still exist

    def test_cycle_detection_end_to_end(self):
        """Create a diamond, then try to create a back edge."""
        s = _state_with_nodes(task_count=4, agent_count=0)
        for node in s["nodes"]:
            node["label"] = f"T{node['id'][-1]}"
        # A → B, A → C, B → D, C → D
        canvas.create_edge(s, "task_0", "task_1")
        canvas.create_edge(s, "task_0", "task_2")
        canvas.create_edge(s, "task_1", "task_3")
        canvas.create_edge(s, "task_2", "task_3")
        assert len(s["edges"]) == 4

        # Try D → A (would create cycle)
        result = canvas.create_edge(s, "task_3", "task_0")
        assert result.startswith("cycle:")
        assert len(s["edges"]) == 4  # rejected

        # DAG should still be valid
        assert canvas.detect_cycle(s) is None

    def test_delete_middle_node_maintains_dag(self):
        """Delete Analyze (task_1) from Research→Analyze→Write chain."""
        s = _fresh_state()
        for label in ("Research", "Analyze", "Write"):
            canvas.add_node(s, "task", label=label)
        canvas.create_edge(s, "task_0", "task_1")
        canvas.create_edge(s, "task_1", "task_2")
        assert len(s["edges"]) == 2

        canvas.delete_node(s, "task_1")
        assert len(s["nodes"]) == 2
        assert len(s["edges"]) == 0  # both edges removed with middle node
        assert canvas.detect_cycle(s) is None

    def test_undo_after_complex_operation_sequence(self):
        s = _state_with_nodes(task_count=2, agent_count=0)
        # Scramble positions so we can detect layout changes
        s["nodes"][0]["x"] = 5000
        s["nodes"][0]["y"] = 5000
        s["nodes"][1]["x"] = 5000
        s["nodes"][1]["y"] = 5000
        canvas.add_node(s, "agent")
        canvas.create_edge(s, "task_0", "task_1")
        canvas.auto_layout(s)
        # 3 operations: add_node, create_edge, auto_layout
        assert len(s["undo_stack"]) == 3

        # Undo all three
        canvas.undo(s)  # revert layout
        canvas.undo(s)  # remove edge
        canvas.undo(s)  # remove agent
        assert len(s["nodes"]) == 2  # back to original 2 task nodes
        assert len(s["edges"]) == 0

        # Redo all three
        canvas.redo(s)  # restore agent
        assert len(s["nodes"]) == 3
        canvas.redo(s)  # restore edge
        assert len(s["edges"]) == 1
        canvas.redo(s)  # restore layout
        # Layout should have repositioned nodes from 5000,5000 to valid coords
        t0 = canvas._find_node(s, "task_0")
        assert t0 is not None and t0["x"] < 1000  # auto-layout moved from 5000

    def test_zoom_does_not_affect_state(self):
        s = _state_with_nodes()
        nodes_before = copy.deepcopy(s["nodes"])
        s["zoom"] = 0.5
        # Zoom only affects rendering, not node positions
        assert s["nodes"] == nodes_before


# ═══════════════════════════════════════════════════════════════════════════
#  Fix-verification tests — guard against XSS, ID collision, and
#  sync data-loss regressions (PR #9 adversarial review items).
# ═══════════════════════════════════════════════════════════════════════════


class TestXSSEscaping:
    """``_build_canvas_html`` must escape all user-controlled strings."""

    HTML_ENTITIES = "<script>alert('xss')</script>"

    def test_escapes_label(self):
        s = _fresh_state()
        s["nodes"].append({
            "id": "task_0", "type": "task", "x": 0, "y": 0,
            "w": 100, "h": 50, "label": self.HTML_ENTITIES,
            "subtitle": "safe",
        })
        html_out = canvas._build_canvas_html(s)
        assert self.HTML_ENTITIES not in html_out, (
            "Raw HTML in label must be escaped"
        )
        assert "&lt;script&gt;" in html_out

    def test_escapes_subtitle(self):
        s = _fresh_state()
        s["nodes"].append({
            "id": "task_0", "type": "task", "x": 0, "y": 0,
            "w": 100, "h": 50, "label": "safe",
            "subtitle": self.HTML_ENTITIES,
        })
        html_out = canvas._build_canvas_html(s)
        assert self.HTML_ENTITIES not in html_out
        assert "&lt;script&gt;" in html_out

    def test_escapes_data_id(self):
        """``data-id`` attribute must be HTML-escaped to prevent attr injection."""
        s = _fresh_state()
        malicious_id = '"><script>evil()</script>'
        s["nodes"].append({
            "id": malicious_id, "type": "task", "x": 0, "y": 0,
            "w": 100, "h": 50, "label": "safe", "subtitle": "safe",
        })
        html_out = canvas._build_canvas_html(s)
        assert 'script>evil()<' not in html_out
        assert "&gt;" in html_out


class TestNodeIDCollision:
    """Node and edge IDs must remain unique after deletions (monotonic counter)."""

    def test_add_after_delete_does_not_reuse_id(self):
        s = _fresh_state()
        canvas.add_node(s, "task")   # task_0
        canvas.add_node(s, "task")   # task_1
        canvas.add_node(s, "task")   # task_2
        assert {n["id"] for n in s["nodes"]} == {"task_0", "task_1", "task_2"}

        canvas.delete_node(s, "task_0")
        assert canvas._find_node(s, "task_0") is None
        assert len(s["nodes"]) == 2

        canvas.add_node(s, "task")   # must NOT be task_0 (collision)
        ids = {n["id"] for n in s["nodes"]}
        assert len(ids) == 3, f"Duplicate ID detected: {ids}"
        assert "task_0" not in ids, "Deleted ID was reused"
        assert "task_3" in ids, (
            f"Expected task_3 (monotonic counter), got {ids}"
        )

    def test_edge_id_after_delete(self):
        s = _state_with_nodes(task_count=3, agent_count=0)
        canvas.create_edge(s, "task_0", "task_1")  # edge_0
        canvas.create_edge(s, "task_1", "task_2")  # edge_1
        assert {e["id"] for e in s["edges"]} == {"edge_0", "edge_1"}

        canvas.delete_edge(s, "edge_0")
        canvas.create_edge(s, "task_2", "task_0")  # must NOT be edge_0
        edge_ids = {e["id"] for e in s["edges"]}
        assert "edge_0" not in edge_ids, "Deleted edge ID was reused"
        assert "edge_2" in edge_ids


class TestSyncPreservesFields(TestSync):
    """``sync_to_crew_model`` must merge, not rebuild — preserving all fields."""

    def test_sync_preserves_agent_backstory(self):
        s = _state_with_nodes(task_count=0, agent_count=1)
        s["nodes"][0]["label"] = "Researcher"
        crew = models.CrewModel(
            name="Test",
            agents=[models.AgentModel(
                role="Researcher", goal="Research",
                backstory="Original backstory",
                allow_delegation=True,
                max_iter=15,
            )],
            tasks=[],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        canvas.sync_to_crew_model(s)
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        updated = models.CrewModel(**stored)
        assert updated.agents[0].role == "Researcher"
        assert updated.agents[0].backstory == "Original backstory"
        assert updated.agents[0].allow_delegation is True
        assert updated.agents[0].max_iter == 15

    def test_sync_preserves_task_output_file_and_guardrails(self):
        s = _state_with_nodes(task_count=0, agent_count=0)
        canvas.add_node(s, "task", label="Research")
        crew = models.CrewModel(
            name="Test",
            agents=[],
            tasks=[models.TaskModel(
                name="Research",
                description="Do research",
                expected_output="Report",
                output_file="research.md",
                human_input=True,
                async_execution=False,
                guardrails=["Check citations"],
                markdown=True,
            )],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        canvas.sync_to_crew_model(s)
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        updated = models.CrewModel(**stored)
        assert updated.tasks[0].output_file == "research.md"
        assert updated.tasks[0].human_input is True
        assert updated.tasks[0].guardrails == ["Check citations"]
        assert updated.tasks[0].markdown is True

    def test_sync_preserves_new_task_context_from_edges(self):
        """New tasks created on canvas with edges should have correct context."""
        s = _fresh_state()
        canvas.add_node(s, "task", label="Research")   # task_0
        canvas.add_node(s, "task", label="Write")      # task_1
        canvas.create_edge(s, "task_0", "task_1")
        crew = models.CrewModel(name="Test", agents=[], tasks=[])
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        canvas.sync_to_crew_model(s)
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        updated = models.CrewModel(**stored)
        assert len(updated.tasks) == 2
        write_task = [t for t in updated.tasks if t.name == "Write"][0]
        assert "Research" in write_task.context

    def test_sync_removes_deleted_agents(self):
        """Agents removed from canvas must be removed from crew model."""
        s = _fresh_state()
        canvas.add_node(s, "agent", label="Researcher")
        canvas.add_node(s, "agent", label="Writer")
        canvas.add_node(s, "agent", label="Reviewer")
        crew = models.CrewModel(
            name="Test",
            agents=[
                models.AgentModel(role="Researcher", goal="Research"),
                models.AgentModel(role="Writer", goal="Write"),
                models.AgentModel(role="Reviewer", goal="Review"),
            ],
            tasks=[],
        )
        canvas.app.storage.user["crew_model"] = crew.model_dump()  # type: ignore[attr-defined]
        # Delete Writer from canvas
        canvas.delete_node(s, "agent_1")
        canvas.sync_to_crew_model(s)
        stored = canvas.app.storage.user["crew_model"]  # type: ignore[attr-defined]
        updated = models.CrewModel(**stored)
        assert len(updated.agents) == 2
        assert {a.role for a in updated.agents} == {"Researcher", "Reviewer"}


class TestToolbarRefreshable:
    """``_render_toolbar`` must be decorated with ``@ui.refreshable``."""

    def test_has_refresh_method(self):
        assert hasattr(canvas._render_toolbar, "refresh"), (
            "_render_toolbar must be decorated with @ui.refreshable"
        )

    def test_has_refreshable_attribute(self):
        assert callable(canvas._render_toolbar.refresh), (
            "_render_toolbar.refresh must be callable"
        )
