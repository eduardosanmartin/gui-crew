"""builder.py - crew, agent, and task configuration forms for gui-crew.

Provides a tabbed builder interface that reads from and writes to
``app.storage.user["crew_model"]``.  Every form validates against the
Pydantic models in ``models.py`` before persisting.

Public surface
--------------
* ``render_builder()`` - main entry point for the Builder page.
* ``BUILTIN_TOOLS`` - catalogue of known CrewAI tools for the tool selector.
"""

from __future__ import annotations

from typing import Any

from nicegui import app, ui

import models as m
from styles import THEME

# ============================================================================
#  Tool Catalogue
# ============================================================================

BUILTIN_TOOLS: list[dict[str, str]] = [
    {"name": "SerperDevTool", "description": "Google Search API tool"},
    {"name": "WebsiteSearchTool", "description": "Scrape and search websites"},
    {"name": "DirectorySearchTool", "description": "Search local directories"},
    {"name": "FileReadTool", "description": "Read files from disk"},
    {"name": "FileWriterTool", "description": "Write files to disk"},
    {"name": "CodeInterpreterTool", "description": "Execute Python code sandboxed"},
    {"name": "CSVSearchTool", "description": "Search CSV files"},
    {"name": "DOCXSearchTool", "description": "Search DOCX files"},
    {"name": "PDFSearchTool", "description": "Search PDF files"},
    {"name": "JSONSearchTool", "description": "Search JSON files"},
    {"name": "SeleniumScrapingTool", "description": "Browser-based scraping"},
    {"name": "GithubSearchTool", "description": "Search GitHub repositories"},
    {"name": "YoutubeChannelSearchTool", "description": "Search YouTube channels"},
    {"name": "YoutubeVideoSearchTool", "description": "Search YouTube videos"},
]


# ============================================================================
#  State helpers
# ============================================================================

def _crew_model() -> m.CrewModel:
    """Return the current crew model from session storage.

    Creates a default ``CrewModel`` when none exists and persists it.
    """
    raw = app.storage.user.get("crew_model")
    if raw is None:
        cm = m.CrewModel(name="New Crew")
        app.storage.user["crew_model"] = cm.model_dump()
        return cm
    if isinstance(raw, dict):
        try:
            return m.CrewModel(**raw)
        except Exception:
            cm = m.CrewModel(name="New Crew")
            app.storage.user["crew_model"] = cm.model_dump()
            return cm
    # Already a CrewModel instance (NiceGUI can keep objects across renders)
    if isinstance(raw, m.CrewModel):
        return raw
    cm = m.CrewModel(name="New Crew")
    app.storage.user["crew_model"] = cm.model_dump()
    return cm


def _persist(cm: m.CrewModel) -> int:
    """Validate *cm* fully and persist to session storage.

    Returns 0 on success, 1 if validation failed (notification shown).
    """
    try:
        validated = m.CrewModel(**cm.model_dump())
        app.storage.user["crew_model"] = validated.model_dump()
        return 0
    except Exception as exc:
        ui.notify(f"Validation error: {exc}", type="negative", position="top")
        return 1


# ============================================================================
#  Shared UI helpers
# ============================================================================

def _section(label: str) -> None:
    """Render a section sub-heading."""
    ui.label(label).classes(THEME.typography.CARD_TITLE + " q-mt-md q-mb-sm")


def _full_input(**kw: Any) -> ui.input:
    """Return a full-width outlined input."""
    return ui.input(**kw).props(THEME.component.INPUT["props"]).classes(
        THEME.component.INPUT["classes"]
    )


def _full_textarea(**kw: Any) -> ui.textarea:
    """Return a full-width outlined textarea."""
    return ui.textarea(**kw).props(THEME.component.INPUT["props"]).classes(
        THEME.component.INPUT["classes"]
    )


def _full_select(**kw: Any) -> ui.select:
    """Return a full-width outlined select."""
    return ui.select(**kw).props(THEME.component.INPUT["props"]).classes(
        THEME.component.INPUT["classes"]
    )


# ============================================================================
#  Crew Form
# ============================================================================

def _render_crew_form(cm: m.CrewModel) -> None:
    """Render the crew-level configuration form."""
    refs: dict[str, Any] = {}

    def _update_hierarchical_visibility() -> None:
        show = refs["process"].value == "hierarchical"
        refs["hierarchical_section"].set_visibility(show)

    def _on_save() -> None:
        cm.name = refs["name"].value.strip() or cm.name
        cm.description = refs["desc"].value or ""
        cm.process = refs["process"].value or "sequential"  # type: ignore[assignment]
        cm.planning = refs["planning"].value
        cm.verbose = refs["verbose"].value
        cm.memory = refs["memory_enabled"].value
        if cm.process == "hierarchical":
            mgr = refs["manager_agent_role"].value.strip()
            cm.manager_agent_role = mgr if mgr else None
        else:
            cm.manager_agent_role = None
        _persist(cm)  # notification handled inside _persist on failure

    _section("Crew Identity")
    refs["name"] = _full_input(
        label="Crew Name *",
        value=cm.name,
        validation={"Name is required": lambda v: bool(v and v.strip())},
    )
    refs["desc"] = _full_textarea(label="Description", value=cm.description)

    _section("Execution")
    refs["process"] = _full_select(
        label="Process Type",
        options=["sequential", "hierarchical"],
        value=cm.process,
        on_change=_update_hierarchical_visibility,
    )
    refs["planning"] = ui.switch("Planning", value=cm.planning)
    refs["verbose"] = ui.switch("Verbose", value=cm.verbose)

    _section("Memory")
    refs["memory_enabled"] = ui.switch("Enable Memory", value=bool(cm.memory))

    _section("Hierarchical Settings")
    refs["hierarchical_section"] = ui.column().classes("w-full q-gutter-sm")
    with refs["hierarchical_section"]:
        refs["manager_agent_role"] = _full_input(
            label="Manager Agent Role",
            value=cm.manager_agent_role or "",
        )
    _update_hierarchical_visibility()

    _section("Inputs")
    with ui.column().classes("w-full q-gutter-sm"):
        for iv in cm.inputs:
            with ui.row().classes("items-center q-gutter-sm w-full"):
                ui.label(iv.name).classes("text-body2")
                ui.label(f"({iv.type})").classes("text-caption text-grey-6")
                if iv.default is not None:
                    ui.label(f"default: {iv.default}").classes("text-caption")
        if not cm.inputs:
            ui.label("No input variables defined.").classes("text-caption text-grey-6")

    ui.button("Save Crew Config", icon="save", on_click=_on_save).props(
        THEME.component.BTN_PRIMARY["props"]
    )


# ============================================================================
#  Agent Forms
# ============================================================================

def _render_agent_list(cm: m.CrewModel) -> None:
    """Render the agent list with add / edit / delete controls."""

    def _delete_agent(idx: int) -> None:
        del cm.agents[idx]
        _persist(cm)
        agent_list.refresh()

    @ui.refreshable
    def agent_list() -> None:
        _section("Agents")
        if not cm.agents:
            ui.label("No agents defined yet.").classes("text-caption text-grey-6 q-mb-md")
        else:
            for i, agent in enumerate(cm.agents):
                with ui.card().props(THEME.component.CARD["props"]).classes("w-full q-mb-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(agent.role).classes(THEME.typography.CARD_TITLE)
                        with ui.row().classes("q-gutter-xs"):
                            ui.button(
                                icon="edit",
                                on_click=lambda a=agent, idx=i: _open_agent_dialog(a, idx),
                            ).props(THEME.component.BTN_ICON["props"])
                            ui.button(
                                icon="delete",
                                on_click=lambda idx=i: _delete_agent(idx),
                            ).props(THEME.component.BTN_ICON["props"] + " color=negative")
                    ui.label(f"Goal: {agent.goal}").classes("text-body2")
                    if agent.backstory:
                        ui.label(f"Backstory: {agent.backstory[:80]}...").classes(
                            "text-caption text-grey-7"
                        )
                    if agent.tools:
                        tools_str = ", ".join(t.name for t in agent.tools)
                        ui.label(f"Tools: {tools_str}").classes("text-caption text-grey-6")

        ui.button("Add Agent", icon="add", on_click=lambda: _open_agent_dialog(None, -1)).props(
            THEME.component.BTN_PRIMARY["props"]
        )

    def _open_agent_dialog(agent: m.AgentModel | None, idx: int) -> None:
        is_new = agent is None
        edit_agent = agent or m.AgentModel(role="", goal="")

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label("Add Agent" if is_new else f"Edit Agent: {edit_agent.role}").classes(
                THEME.typography.SECTION_TITLE
            )

            role = _full_input(label="Role *", value=edit_agent.role)
            goal = _full_input(label="Goal *", value=edit_agent.goal)
            backstory = _full_textarea(label="Backstory", value=edit_agent.backstory)
            allow_del = ui.switch("Allow Delegation", value=edit_agent.allow_delegation)
            allow_code = ui.switch("Allow Code Execution", value=edit_agent.allow_code_execution)
            max_iter = ui.number(
                label="Max Iterations",
                value=edit_agent.max_iter or 0,
                min=0,
            )
            multimodal = ui.switch("Multimodal", value=edit_agent.multimodal)

            # Tools multi-select
            _section("Tools")
            agent_tool_names = {t.name for t in edit_agent.tools}
            tool_selections: dict[str, bool] = {}
            with ui.column().classes("q-gutter-xs w-full"):
                for tool_info in BUILTIN_TOOLS:
                    tname = tool_info["name"]
                    tool_selections[tname] = ui.checkbox(
                        f"{tname} - {tool_info['description']}",
                        value=tname in agent_tool_names,
                    )

            # LLM
            _section("LLM Configuration")
            llm_model = _full_input(
                label="Model",
                value=edit_agent.llm.model if edit_agent.llm else "",
            )
            llm_temp = ui.number(
                label="Temperature",
                value=edit_agent.llm.temperature if edit_agent.llm else None,
                min=0.0,
                max=2.0,
                step=0.1,
            )

            def _save_agent() -> None:
                nonlocal edit_agent
                edit_agent.role = (role.value or "").strip()
                edit_agent.goal = (goal.value or "").strip()
                edit_agent.backstory = backstory.value or ""
                edit_agent.allow_delegation = allow_del.value
                edit_agent.allow_code_execution = allow_code.value
                edit_agent.max_iter = int(max_iter.value) if max_iter.value and max_iter.value > 0 else None
                edit_agent.multimodal = multimodal.value

                # LLM
                if llm_model.value:
                    edit_agent.llm = m.LLMModel(
                        model=llm_model.value,
                        temperature=llm_temp.value if llm_temp.value else None,
                    )
                else:
                    edit_agent.llm = None

                # Tools
                selected = [
                    m.ToolRef(kind="builtin", name=tn)
                    for tn, cb in tool_selections.items()
                    if cb.value
                ]
                edit_agent.tools = selected

                # Validate agent fields
                if not edit_agent.role:
                    ui.notify("Agent role is required", type="negative")
                    return
                if not edit_agent.goal:
                    ui.notify("Agent goal is required", type="negative")
                    return

                if is_new:
                    cm.agents.append(edit_agent)
                else:
                    cm.agents[idx] = edit_agent

                _persist(cm)
                dialog.close()
                agent_list.refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("Cancel", on_click=dialog.close).props(
                    THEME.component.BTN_SECONDARY["props"]
                )
                ui.button("Save Agent", icon="save", on_click=_save_agent).props(
                    THEME.component.BTN_PRIMARY["props"]
                )

        dialog.open()

    agent_list()


# ============================================================================
#  Task Forms
# ============================================================================

def _render_task_list(cm: m.CrewModel) -> None:
    """Render the task list with add / edit / delete controls."""

    def _delete_task(idx: int) -> None:
        del cm.tasks[idx]
        # Also clean up stale context references
        remaining = {t.name for t in cm.tasks}
        for t in cm.tasks:
            t.context = [c for c in t.context if c in remaining]
        _persist(cm)
        task_list.refresh()

    @ui.refreshable
    def task_list() -> None:
        _section("Tasks")
        if not cm.tasks:
            ui.label("No tasks defined yet.").classes("text-caption text-grey-6 q-mb-md")
        else:
            for i, task in enumerate(cm.tasks):
                with ui.card().props(THEME.component.CARD["props"]).classes("w-full q-mb-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(task.name).classes(THEME.typography.CARD_TITLE)
                        with ui.row().classes("q-gutter-xs"):
                            ui.button(
                                icon="edit",
                                on_click=lambda t=task, idx=i: _open_task_dialog(t, idx),
                            ).props(THEME.component.BTN_ICON["props"])
                            ui.button(
                                icon="delete",
                                on_click=lambda idx=i: _delete_task(idx),
                            ).props(THEME.component.BTN_ICON["props"] + " color=negative")
                    ui.label(f"Agent: {task.agent_role or '(unassigned)'}").classes("text-body2")
                    if task.context:
                        ui.label(f"Depends on: {', '.join(task.context)}").classes(
                            "text-caption text-grey-7"
                        )
                    if task.guardrails:
                        ui.label(f"Guardrails: {', '.join(task.guardrails)}").classes(
                            "text-caption text-grey-6"
                        )

        ui.button("Add Task", icon="add", on_click=lambda: _open_task_dialog(None, -1)).props(
            THEME.component.BTN_PRIMARY["props"]
        )

    def _open_task_dialog(task: m.TaskModel | None, idx: int) -> None:
        is_new = task is None
        edit_task = task or m.TaskModel(
            name="", description="", expected_output=""
        )

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label("Add Task" if is_new else f"Edit Task: {edit_task.name}").classes(
                THEME.typography.SECTION_TITLE
            )

            task_name = _full_input(label="Name *", value=edit_task.name)
            task_desc = _full_textarea(label="Description *", value=edit_task.description)
            task_output = _full_textarea(label="Expected Output *", value=edit_task.expected_output)

            # Agent assignment
            agent_options = {a.role: a.role for a in cm.agents}
            agent_options["(unassigned)"] = ""
            task_agent = _full_select(
                label="Assigned Agent",
                options=agent_options,
                value=edit_task.agent_role or "",
            )

            # Context (dependencies)
            _section("Dependencies")
            other_tasks = [t for t in cm.tasks if t.name != edit_task.name]
            ctx_selections: dict[str, ui.checkbox] = {}
            with ui.column().classes("q-gutter-xs w-full"):
                for t in other_tasks:
                    ctx_selections[t.name] = ui.checkbox(
                        f"Depends on: {t.name}",
                        value=t.name in edit_task.context,
                    )
                if not other_tasks and not is_new:
                    ui.label("No other tasks to depend on.").classes("text-caption text-grey-6")

            _section("Output")
            output_file = _full_input(label="Output File", value=edit_task.output_file or "")
            human_input = ui.switch("Require Human Input", value=edit_task.human_input)
            async_exec = ui.switch("Async Execution", value=edit_task.async_execution)
            markdown = ui.switch("Markdown Output", value=edit_task.markdown)

            _section("Guardrails")
            gr_count = ui.number(
                label="Max Retries",
                value=edit_task.guardrail_max_retries,
                min=0,
            )

            # Task tools
            _section("Task Tools")
            task_tool_names = {t.name for t in edit_task.tools}
            task_tool_selections: dict[str, ui.checkbox] = {}
            with ui.column().classes("q-gutter-xs w-full"):
                for tool_info in BUILTIN_TOOLS:
                    tname = tool_info["name"]
                    task_tool_selections[tname] = ui.checkbox(
                        f"{tname}",
                        value=tname in task_tool_names,
                    )

            def _save_task() -> None:
                nonlocal edit_task
                edit_task.name = (task_name.value or "").strip()
                edit_task.description = (task_desc.value or "").strip()
                edit_task.expected_output = (task_output.value or "").strip()
                edit_task.agent_role = (task_agent.value or "").strip() or None
                edit_task.output_file = output_file.value.strip() or None
                edit_task.human_input = human_input.value
                edit_task.async_execution = async_exec.value
                edit_task.markdown = markdown.value
                edit_task.guardrail_max_retries = int(gr_count.value) if gr_count.value else 3

                # Context - collect from checkboxes
                edit_task.context = [
                    tn for tn, cb in ctx_selections.items() if cb.value
                ]

                # Task tools
                edit_task.tools = [
                    m.ToolRef(kind="builtin", name=tn)
                    for tn, cb in task_tool_selections.items()
                    if cb.value
                ]

                # Validate
                if not edit_task.name:
                    ui.notify("Task name is required", type="negative")
                    return
                if not edit_task.description:
                    ui.notify("Task description is required", type="negative")
                    return
                if not edit_task.expected_output:
                    ui.notify("Expected output is required", type="negative")
                    return

                # Validate output_file path
                if edit_task.output_file:
                    try:
                        m.TaskModel(
                            name=edit_task.name,
                            description=edit_task.description,
                            expected_output=edit_task.expected_output,
                            output_file=edit_task.output_file,
                        )
                    except Exception as exc:
                        ui.notify(f"Output file error: {exc}", type="negative")
                        return

                if is_new:
                    cm.tasks.append(edit_task)
                else:
                    cm.tasks[idx] = edit_task

                # Validate full crew with cross-field checks
                errs = _persist(cm)
                if not errs:
                    dialog.close()
                    task_list.refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("Cancel", on_click=dialog.close).props(
                    THEME.component.BTN_SECONDARY["props"]
                )
                ui.button("Save Task", icon="save", on_click=_save_task).props(
                    THEME.component.BTN_PRIMARY["props"]
                )

        dialog.open()

    task_list()


# ============================================================================
#  Tool Catalogue Tab
# ============================================================================

def _render_tool_catalog() -> None:
    """Render the built-in tool catalogue with search/filter."""

    _section("Built-in CrewAI Tools")
    search = _full_input(label="Search tools...")

    @ui.refreshable
    def tool_grid() -> None:
        query = (search.value or "").strip().lower()
        filtered = BUILTIN_TOOLS
        if query:
            filtered = [
                t for t in BUILTIN_TOOLS
                if query in t["name"].lower() or query in t["description"].lower()
            ]
        with ui.column().classes("w-full q-gutter-sm"):
            for tool in filtered:
                with ui.card().props(THEME.component.CARD["props"]).classes("w-full"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label(tool["name"]).classes(THEME.typography.CARD_TITLE)
                        ui.badge("built-in", color="primary")
                    ui.label(tool["description"]).classes("text-body2 text-grey-7")
            if not filtered:
                ui.label("No tools match your search.").classes("text-caption text-grey-6")

    search.on("update:model-value", lambda _: tool_grid.refresh())
    tool_grid()


# ============================================================================
#  Main Builder Entry Point
# ============================================================================

def render_builder() -> None:
    """Render the complete Builder page with tabbed interface.

    Called from ``app.py``'s ``/builder`` route.
    """
    cm = _crew_model()

    with ui.tabs().classes("w-full") as tabs:
        crew_tab = ui.tab("Crew", icon="group")
        agents_tab = ui.tab("Agents", icon="person")
        tasks_tab = ui.tab("Tasks", icon="checklist")
        tools_tab = ui.tab("Tools", icon="build")

    with ui.tab_panels(tabs, value=crew_tab).classes("w-full q-mt-md"):
        with ui.tab_panel(crew_tab):
            _render_crew_form(cm)
        with ui.tab_panel(agents_tab):
            _render_agent_list(cm)
        with ui.tab_panel(tasks_tab):
            _render_task_list(cm)
        with ui.tab_panel(tools_tab):
            _render_tool_catalog()
