# canvas-editor Specification

## Purpose

An n8n-style directed acyclic graph (DAG) visual topology editor for CrewAI crews. Users drag and
drop nodes (agents, tasks) and connect them with edges (data flow, task dependencies). Complements
the Builder: Builder configures individual nodes, Canvas shows relationships.

## Requirements

### Requirement: Node Drag and Drop

The system MUST allow users to drag agent and task nodes from a palette onto the canvas to build a
crew topology. Nodes SHALL render with distinct shapes per entity type.

#### Scenario: Add a new agent node

- GIVEN the canvas is empty
- WHEN the user drags an "Agent" node from the palette onto the canvas
- THEN a new node SHALL appear at the drop position
- AND it SHALL display a placeholder name ("New Agent") and a "+" button to open the Builder form

#### Scenario: Remove a node

- GIVEN a canvas with multiple nodes
- WHEN the user selects a node and presses "Delete" or clicks the remove button
- THEN the node and all its connected edges SHALL be removed
- AND a confirmation dialog SHALL appear if the node has connected edges

### Requirement: Edge Creation

The system MUST allow users to draw directed edges between nodes by dragging from an output handle
to an input handle. Edges SHALL represent task context dependencies (task → task) and agent
assignments (agent ↔ task).

#### Scenario: Connect two tasks with a dependency edge

- GIVEN two task nodes "Research" and "Write Report" exist on the canvas
- WHEN the user drags from "Research"'s output handle to "Write Report"'s input handle
- THEN a directed edge SHALL appear
- AND "Write Report" SHALL show "Research" in its context dependencies

#### Scenario: Prevent self-connecting edges

- GIVEN a single task node on the canvas
- WHEN the user attempts to drag an edge from the node's output back to its own input
- THEN the edge creation SHALL be rejected
- AND a tooltip SHALL display "A task cannot depend on itself"

### Requirement: Automatic Layout

The system SHOULD provide an "Auto-layout" button that rearranges nodes into a top-down
hierarchical layout reflecting the DAG dependency order.

#### Scenario: Auto-layout a messy canvas

- GIVEN nodes are scattered randomly on the canvas
- WHEN the user clicks "Auto-layout"
- THEN nodes SHALL rearrange into a top-down flow with root tasks at the top and leaf tasks at the bottom
- AND existing edge routing SHALL adjust to minimize edge crossings

### Requirement: Bidirectional Builder Sync

The system MUST keep the canvas topology and the Builder's configuration in sync. Changes in one
view SHALL reflect immediately in the other.

#### Scenario: Add agent in Builder, see node in Canvas

- GIVEN the Builder and Canvas are both open (split view or separate tabs)
- WHEN the user adds a new agent "Editor" via the Builder form
- THEN a new "Editor" agent node SHALL appear in the Canvas
- AND it SHALL be placed automatically at a free canvas position

#### Scenario: Delete edge in Canvas, update Builder context

- GIVEN task B depends on task A (shown as an edge in Canvas)
- WHEN the user deletes the edge A → B in the Canvas
- THEN task B's `context` list in the Builder SHALL remove task A
- AND the UI SHALL show a confirmation before removing

### Requirement: DAG Validation

The system MUST validate the canvas topology on save or before execution and SHALL reject invalid
configurations.

#### Scenario: Detect cycle in task graph

- GIVEN tasks A → B → C form a chain
- WHEN the user adds edge C → A
- THEN the system SHALL display "Circular dependency detected: A → B → C → A"
- AND the edge SHALL be highlighted in red

### Edge Cases and Error Handling

| Condition | Expected Behavior |
|---|---|
| Connecting a node type that doesn't support edges to another | Edge creation disabled; tooltip explains incompatibility |
| Deleting an agent node that has tasks assigned | Warning: "3 tasks assigned to this agent will lose their assignment" |
| Very large crew (20+ agents, 50+ tasks) | Canvas SHALL support zoom/pan; auto-layout SHALL not block UI |
| Duplicate node names | Warning icon on duplicate nodes; save validation error |
| Browser tab close with unsaved changes | Browser `beforeunload` prompt |

### Non-Functional Requirements

- Canvas SHALL render smoothly with up to 100 nodes
- Zoom SHALL support 25%–200% range with fit-to-screen button
- All canvas operations MUST be undoable (Ctrl+Z) with minimum 50 undo steps
- The canvas SHALL be keyboard-accessible for basic operations

### Dependencies

- `crew-builder`: node configuration opens the Builder forms
- `models.py`: node data model
- Quasar/Vue DAG rendering library (NiceGUI integration)
