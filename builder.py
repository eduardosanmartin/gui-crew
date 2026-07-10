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

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

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

logger = logging.getLogger(__name__)


def _all_tool_options() -> list[dict[str, str]]:
    """Return combined built-in and custom tool options for selectors."""
    custom = app.storage.user.get("custom_tools", [])
    result = list(BUILTIN_TOOLS)
    for ct in custom:
        desc = (
            ct.get("params", {}).get("description", "")
            if isinstance(ct.get("params"), dict)
            else ""
        )
        result.append({"name": ct["name"], "description": desc, "_custom": "1"})
    return result


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
            logger.warning(
                "Corrupt crew_model in storage, falling back to default. "
                "Raw data preserved under 'crew_model_corrupt' key."
            )
            ui.notify(
                "Crew data was corrupted and has been reset. "
                "Your previous data is preserved for recovery.",
                type="warning",
                position="top",
                close_button="OK",
            )
            app.storage.user["crew_model_corrupt"] = raw
            cm = m.CrewModel(name="New Crew")
            app.storage.user["crew_model"] = cm.model_dump()
            return cm
    # Already a CrewModel instance (NiceGUI can keep objects across renders)
    if isinstance(raw, m.CrewModel):
        return raw
    logger.warning("Unexpected crew_model type %s, resetting.", type(raw).__name__)
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
        cm.knowledge_sources = ks_ref["data"]
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

    # Knowledge Sources sub-form
    _section("Knowledge Sources")
    ks_ref: dict[str, list[dict[str, Any]]] = {"data": cm.knowledge_sources.copy()}
    ks_label = ui.label(
        f"{len(cm.knowledge_sources)} source(s) configured" if cm.knowledge_sources
        else "No sources configured"
    ).classes("text-body2 q-ml-sm")

    def _on_ks_saved(new_ks: list[dict[str, Any]]) -> None:
        ks_ref["data"] = new_ks
        ks_label.set_text(
            f"{len(new_ks)} source(s) configured" if new_ks else "No sources configured"
        )

    ui.button(
        "Configure Knowledge Sources",
        icon="menu_book",
        on_click=lambda: _render_knowledge_sub_form(
            ks_ref["data"], on_save=_on_ks_saved,
        ),
    ).props("flat q-mb-sm")

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
        edit_agent = agent.model_copy(deep=True) if agent else m.AgentModel(role="", goal="")

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label("Add Agent" if is_new else f"Edit Agent: {edit_agent.role}").classes(
                THEME.typography.SECTION_TITLE
            )

            role = _full_input(label="Role *", value=edit_agent.role)
            goal = _full_input(label="Goal *", value=edit_agent.goal)
            _render_variable_preview(goal, cm.inputs)
            backstory = _full_textarea(label="Backstory", value=edit_agent.backstory)
            _render_variable_preview(backstory, cm.inputs)
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
                for tool_info in _all_tool_options():
                    tname = tool_info["name"]
                    label = f"{tname} - {tool_info['description']}"
                    if tool_info.get("_custom"):
                        label += " (custom)"
                    tool_selections[tname] = ui.checkbox(
                        label,
                        value=tname in agent_tool_names,
                    )

            # LLM sub-form button
            _section("LLM Configuration")
            llm_ref: dict[str, m.LLMModel | None] = {
                "data": edit_agent.llm.model_copy(deep=True) if edit_agent.llm else None
            }
            llm_btn_label = ui.label(
                f"Model: {edit_agent.llm.model}" if edit_agent.llm
                else "Not configured"
            ).classes("text-body2 q-ml-sm")

            def _on_llm_saved(new_llm: m.LLMModel | None) -> None:
                llm_ref["data"] = new_llm
                if new_llm:
                    llm_btn_label.set_text(f"Model: {new_llm.model}")
                else:
                    llm_btn_label.set_text("Not configured")

            ui.button(
                "Configure LLM",
                icon="psychology",
                on_click=lambda: _render_llm_sub_form(
                    llm_ref["data"],
                    on_save=_on_llm_saved,
                    title=f"LLM for {edit_agent.role or 'Agent'}",
                ),
            ).props("flat")

            # Memory sub-form button
            _section("Memory")
            mem_ref: dict[str, bool | m.MemoryConfig] = {
                "data": edit_agent.memory.model_copy(deep=True) if isinstance(edit_agent.memory, m.MemoryConfig)
                else edit_agent.memory
            }
            mem_btn_label = ui.label(
                "Enabled" if edit_agent.memory else "Disabled"
            ).classes("text-body2 q-ml-sm")

            def _on_mem_saved(new_mem: bool | m.MemoryConfig) -> None:
                mem_ref["data"] = new_mem
                mem_btn_label.set_text("Enabled" if new_mem else "Disabled")

            ui.button(
                "Configure Memory",
                icon="memory",
                on_click=lambda: _render_memory_sub_form(
                    mem_ref["data"],
                    on_save=_on_mem_saved,
                    title=f"Memory for {edit_agent.role or 'Agent'}",
                ),
            ).props("flat")

            def _save_agent() -> None:
                nonlocal edit_agent
                # Collect values from form controls
                role_val = (role.value or "").strip()
                goal_val = (goal.value or "").strip()
                backstory_val = backstory.value or ""
                allow_del_val = allow_del.value
                allow_code_val = allow_code.value
                max_iter_val = int(max_iter.value) if max_iter.value and max_iter.value > 0 else None
                multimodal_val = multimodal.value

                # Validate before mutating edit_agent
                if not role_val:
                    ui.notify("Agent role is required", type="negative")
                    return
                if not goal_val:
                    ui.notify("Agent goal is required", type="negative")
                    return

                # Apply values to edit_agent (copy — safe to mutate now)
                edit_agent.role = role_val
                edit_agent.goal = goal_val
                edit_agent.backstory = backstory_val
                edit_agent.allow_delegation = allow_del_val
                edit_agent.allow_code_execution = allow_code_val
                edit_agent.max_iter = max_iter_val
                edit_agent.multimodal = multimodal_val

                # LLM from sub-form
                edit_agent.llm = llm_ref["data"]

                # Memory from sub-form
                edit_agent.memory = mem_ref["data"]

                # Tools
                selected = [
                    m.ToolRef(kind="builtin", name=tn)
                    for tn, cb in tool_selections.items()
                    if cb.value
                ]
                edit_agent.tools = selected

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
        edit_task = task.model_copy(deep=True) if task else m.TaskModel(
            name="", description="", expected_output=""
        )

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label("Add Task" if is_new else f"Edit Task: {edit_task.name}").classes(
                THEME.typography.SECTION_TITLE
            )

            task_name = _full_input(label="Name *", value=edit_task.name)
            task_desc = _full_textarea(label="Description *", value=edit_task.description)
            _render_variable_preview(task_desc, cm.inputs)
            task_output = _full_textarea(label="Expected Output *", value=edit_task.expected_output)
            _render_variable_preview(task_output, cm.inputs)

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
                for tool_info in _all_tool_options():
                    tname = tool_info["name"]
                    label = tname
                    if tool_info.get("_custom"):
                        label += " (custom)"
                    task_tool_selections[tname] = ui.checkbox(
                        label,
                        value=tname in task_tool_names,
                    )

            def _save_task() -> None:
                nonlocal edit_task
                # Collect values from form controls
                name_val = (task_name.value or "").strip()
                desc_val = (task_desc.value or "").strip()
                output_val = (task_output.value or "").strip()
                agent_role_val = (task_agent.value or "").strip() or None
                output_file_val = output_file.value.strip() or None
                human_input_val = human_input.value
                async_exec_val = async_exec.value
                markdown_val = markdown.value
                guardrail_retries = int(gr_count.value) if gr_count.value is not None else 3

                # Context - collect from checkboxes
                context_val = [
                    tn for tn, cb in ctx_selections.items() if cb.value
                ]

                # Task tools
                tools_val = [
                    m.ToolRef(kind="builtin", name=tn)
                    for tn, cb in task_tool_selections.items()
                    if cb.value
                ]

                # Validate before mutating edit_task
                if not name_val:
                    ui.notify("Task name is required", type="negative")
                    return
                if not desc_val:
                    ui.notify("Task description is required", type="negative")
                    return
                if not output_val:
                    ui.notify("Expected output is required", type="negative")
                    return

                # Validate output_file path early
                if output_file_val:
                    try:
                        m.TaskModel(
                            name=name_val,
                            description=desc_val,
                            expected_output=output_val,
                            output_file=output_file_val,
                        )
                    except Exception as exc:
                        ui.notify(f"Output file error: {exc}", type="negative")
                        return

                # Apply values to edit_task (copy — safe to mutate now)
                edit_task.name = name_val
                edit_task.description = desc_val
                edit_task.expected_output = output_val
                edit_task.agent_role = agent_role_val
                edit_task.output_file = output_file_val
                edit_task.human_input = human_input_val
                edit_task.async_execution = async_exec_val
                edit_task.markdown = markdown_val
                edit_task.guardrail_max_retries = guardrail_retries
                edit_task.context = context_val
                edit_task.tools = tools_val

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

    # Add custom tool creation button
    _section("Custom Tools")
    ui.button(
        "Add Custom Tool",
        icon="add",
        on_click=_open_custom_tool_dialog,
    ).props(THEME.component.BTN_PRIMARY["props"])


def _open_custom_tool_dialog() -> None:
    """Open a dialog to create a custom tool definition."""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Create Custom Tool").classes(THEME.typography.SECTION_TITLE)
        tool_name = _full_input(label="Tool Name *")
        tool_desc = _full_input(label="Description")
        args_schema_text = _full_textarea(
            label="Args Schema (JSON)",
            value='{\n  "param_name": {"type": "string", "description": "..."}\n}',
        )
        ui.label(
            "Define the tool's Pydantic args_schema as a JSON object. "
            "Each key is a parameter name with type and description."
        ).classes("text-caption text-grey-6")

        def _save_custom() -> None:
            name_val = (tool_name.value or "").strip()
            if not name_val:
                ui.notify("Tool name is required", type="negative")
                return
            desc_val = tool_desc.value or ""
            # Parse args_schema
            schema_val: dict[str, Any] | None = None
            raw_schema = (args_schema_text.value or "").strip()
            if raw_schema:
                import json
                try:
                    parsed = json.loads(raw_schema)
                    if isinstance(parsed, dict):
                        schema_val = parsed
                    else:
                        ui.notify("Args schema must be a JSON object", type="negative")
                        return
                except json.JSONDecodeError as exc:
                    ui.notify(f"Invalid JSON in args schema: {exc}", type="negative")
                    return

            tool_ref = m.ToolRef(
                kind="custom",
                name=name_val,
                params={"description": desc_val} if desc_val else {},
                args_schema=schema_val,
            )
            # Persist to storage so tool selectors can find it
            custom_tools = app.storage.user.get("custom_tools", [])
            custom_tools.append(tool_ref.model_dump())
            app.storage.user["custom_tools"] = custom_tools
            ui.notify(
                f"Custom tool '{name_val}' created. It is now available in the agent/task tool selectors.",
                type="positive",
            )
            dialog.close()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props(
                THEME.component.BTN_SECONDARY["props"]
            )
            ui.button("Create Tool", icon="save", on_click=_save_custom).props(
                THEME.component.BTN_PRIMARY["props"]
            )

    dialog.open()


# ============================================================================
#  LLM / Memory / Knowledge Sub-forms  (Task 1.24)
# ============================================================================

def _render_llm_sub_form(
    existing: m.LLMModel | None,
    on_save: Callable[[m.LLMModel | None], None],
    title: str = "LLM Configuration",
) -> None:
    """Open a dialog to configure a specific LLM model.

    Args:
        existing: The current LLM configuration, or *None*.
        on_save: Called with the new ``LLMModel`` (or *None* to clear).
        title: Dialog heading.
    """
    current = existing.model_copy(deep=True) if existing else m.LLMModel()

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label(title).classes(THEME.typography.SECTION_TITLE)

        model_ref = _full_input(label="Model", value=current.model)
        temp_ref = ui.number(
            label="Temperature",
            value=current.temperature,
            min=0.0,
            max=2.0,
            step=0.1,
        )
        base_url_ref = _full_input(label="Base URL", value=current.base_url or "")
        api_key_ref = _full_input(label="API Key Env Var", value=current.api_key_env or "")

        def _save() -> None:
            model_val = (model_ref.value or "").strip()
            if not model_val:
                ui.notify("Model name is required", type="negative")
                return
            new_llm = m.LLMModel(
                model=model_val,
                temperature=temp_ref.value if temp_ref.value is not None else None,
                base_url=base_url_ref.value.strip() or None,
                api_key_env=api_key_ref.value.strip() or None,
            )
            on_save(new_llm)
            dialog.close()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button(
                "Clear LLM",
                on_click=lambda: (on_save(None), dialog.close()),
            ).props("flat color=negative")
            ui.button("Cancel", on_click=dialog.close).props(
                THEME.component.BTN_SECONDARY["props"]
            )
            ui.button("Save", icon="save", on_click=_save).props(
                THEME.component.BTN_PRIMARY["props"]
            )

    dialog.open()


def _render_memory_sub_form(
    existing: bool | m.MemoryConfig,
    on_save: Callable[[bool | m.MemoryConfig], None],
    title: str = "Memory Configuration",
) -> None:
    """Open a dialog to configure memory settings for an agent or crew.

    Args:
        existing: The current memory config (bool or ``MemoryConfig``).
        on_save: Called with the new config.
        title: Dialog heading.
    """
    is_configured = isinstance(existing, m.MemoryConfig)
    current = existing if is_configured else m.MemoryConfig()

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label(title).classes(THEME.typography.SECTION_TITLE)

        enabled_ref = ui.switch("Enable Memory", value=is_configured or bool(existing))

        def _update_advanced_visibility() -> None:
            advanced.set_visibility(enabled_ref.value)

        recency = ui.number(
            label="Recency Weight",
            value=current.recency_weight,
            min=0.0,
            max=1.0,
            step=0.1,
        )
        semantic = ui.number(
            label="Semantic Weight",
            value=current.semantic_weight,
            min=0.0,
            max=1.0,
            step=0.1,
        )
        importance = ui.number(
            label="Importance Weight",
            value=current.importance_weight,
            min=0.0,
            max=1.0,
            step=0.1,
        )
        half_life = ui.number(
            label="Recency Half-Life (days)",
            value=current.recency_half_life_days,
            min=1,
        )

        advanced = ui.column().classes("w-full q-gutter-sm")
        with advanced:
            recency
            semantic
            importance
            half_life

        _update_advanced_visibility()
        enabled_ref.on("update:model-value", lambda _: _update_advanced_visibility())

        def _save() -> None:
            if enabled_ref.value:
                cfg = m.MemoryConfig(
                    enabled=True,
                    recency_weight=recency.value if recency.value is not None else None,
                    semantic_weight=semantic.value if semantic.value is not None else None,
                    importance_weight=importance.value if importance.value is not None else None,
                    recency_half_life_days=(
                        int(half_life.value) if half_life.value is not None else None
                    ),
                )
                on_save(cfg)
            else:
                on_save(False)
            dialog.close()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props(
                THEME.component.BTN_SECONDARY["props"]
            )
            ui.button("Save", icon="save", on_click=_save).props(
                THEME.component.BTN_PRIMARY["props"]
            )

    dialog.open()


def _render_knowledge_sub_form(
    existing: list[dict[str, Any]],
    on_save: Callable[[list[dict[str, Any]]], None],
) -> None:
    """Open a dialog to manage knowledge sources for a crew.

    Args:
        existing: The current list of knowledge source dicts.
        on_save: Called with the updated list.
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label("Knowledge Sources").classes(THEME.typography.SECTION_TITLE)

        # Store plain data only — no UI elements in source_rows
        source_rows: list[dict[str, Any]] = [
            {"name": src.get("name", ""), "kind": src.get("kind", "text")}
            for src in existing
        ]
        sources_list = ui.column().classes("w-full q-gutter-sm")

        def _add_source() -> None:
            source_rows.append({"name": "", "kind": "text"})
            _refresh_sources()

        def _refresh_sources() -> None:
            # Extract current values from UI elements before destroying them
            for row in source_rows:
                if hasattr(row["name"], "value"):
                    row["name"] = row["name"].value or ""
                if hasattr(row["kind"], "value"):
                    row["kind"] = row["kind"].value or "text"
            sources_list.clear()
            with sources_list:
                for i, row in enumerate(source_rows):
                    with ui.card().props(THEME.component.CARD["props"]).classes("w-full"):
                        with ui.row().classes("items-center justify-between w-full"):
                            row["name"] = _full_input(
                                label="Source Name", value=row["name"],
                            )
                            row["kind"] = _full_select(
                                label="Kind", options=["string", "text", "pdf"],
                                value=row["kind"],
                            )
                            ui.button(
                                icon="delete", on_click=lambda idx=i: _remove_source(idx),
                            ).props(THEME.component.BTN_ICON["props"] + " color=negative")

        def _remove_source(idx: int) -> None:
            del source_rows[idx]
            _refresh_sources()

        _refresh_sources()

        ui.button("+ Add Source", icon="add", on_click=_add_source).props("flat")

        def _save() -> None:
            result: list[dict[str, Any]] = []
            for row in source_rows:
                name_val = (row["name"].value or "").strip()
                if name_val:
                    result.append({
                        "name": name_val,
                        "kind": row["kind"].value,
                    })
            on_save(result)
            dialog.close()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props(
                THEME.component.BTN_SECONDARY["props"]
            )
            ui.button("Save Sources", icon="save", on_click=_save).props(
                THEME.component.BTN_PRIMARY["props"]
            )

    dialog.open()


# ============================================================================
#  Variable Interpolation Preview  (Task 1.27)
# ============================================================================

def _interpolate_preview(text: str, inputs: list[m.InputVar]) -> str:
    """Resolve ``{variable}`` placeholders using input default values.

    Args:
        text: The text containing ``{var}`` placeholders.
        inputs: Crew input variable definitions.

    Returns:
        The text with known variables replaced by their default values.
        Unknown variables are left as-is.
    """
    if not text or not inputs:
        return text
    defaults = {v.name: str(v.default) for v in inputs if v.default is not None}
    if not defaults:
        return text

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in defaults:
            return defaults[var_name]
        return match.group(0)

    return re.sub(r"(?<!\{)\{(\w+)\}(?!\})", _replace, text)


def _render_variable_preview(
    text_ref: ui.input | ui.textarea,
    inputs: list[m.InputVar],
) -> ui.label:
    """Attach a live interpolation preview label to a text input.

    The preview updates on every keystroke and shows how ``{variables}``
    resolve with the crew's input defaults.
    """
    preview_label = ui.label("").classes("text-caption text-grey-6 q-mt-xs")

    def _update_preview() -> None:
        current = text_ref.value or ""
        previewed = _interpolate_preview(current, inputs)
        if previewed and previewed != current:
            preview_label.set_text(f"Preview: {previewed}")
        else:
            preview_label.set_text("")

    text_ref.on("update:model-value", lambda _: _update_preview())
    _update_preview()
    return preview_label


# ============================================================================
#  Save / Load Controls  (Task 1.28)
# ============================================================================

def _render_save_load_bar(cm: m.CrewModel) -> None:
    """Render an explicit save / load control bar with status indicator."""
    last_saved_ref = {"timestamp": ""}

    def _do_save() -> None:
        result = _persist(cm)
        if result == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            last_saved_ref["timestamp"] = ts
            save_status.set_text(f"✓ Saved at {ts}")
            save_status.classes(
                remove="text-negative",
                add="text-positive text-caption",
            )
        else:
            save_status.set_text("✗ Save failed — see validation errors")
            save_status.classes(
                remove="text-positive",
                add="text-negative text-caption",
            )

    def _do_load() -> None:
        raw = app.storage.user.get("crew_model")
        if raw is None:
            ui.notify("No saved crew found in storage.", type="info")
            return
        # Force reload by clearing and re-fetching
        app.storage.user["crew_model"] = raw
        ui.notify("Crew loaded from browser storage. Refresh the page to see changes.", type="positive")

    with ui.card().props(THEME.component.CARD["props"]).classes("w-full q-mb-md"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Crew Storage").classes(THEME.typography.CARD_TITLE)
            with ui.row().classes("q-gutter-sm"):
                ui.button("Save Crew", icon="save", on_click=_do_save).props(
                    THEME.component.BTN_PRIMARY["props"]
                )
                ui.button("Load Crew", icon="refresh", on_click=_do_load).props(
                    THEME.component.BTN_SECONDARY["props"]
                )

        save_status = ui.label("").classes("text-caption q-mt-sm")
        # Show existing save state
        raw = app.storage.user.get("crew_model")
        if raw and isinstance(raw, dict) and raw.get("name"):
            save_status.set_text(f"Current: '{raw['name']}' in storage")
            save_status.classes(remove="text-negative", add="text-positive text-caption")


# ============================================================================
#  Guided Wizard Mode  (Task 1.25)
# ============================================================================

# Template definitions for the wizard
_WIZARD_TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {
        "name": "New Crew",
        "description": "",
        "process": "sequential",
    },
    "research": {
        "name": "Research Crew",
        "description": "A crew that researches topics and produces reports.",
        "process": "sequential",
        "agents": [
            {"role": "Researcher", "goal": "Find comprehensive information on the topic", "backstory": "Expert researcher with years of experience"},
            {"role": "Writer", "goal": "Produce a well-structured report", "backstory": "Skilled technical writer"},
        ],
        "tasks": [
            {"name": "Research", "description": "Research {topic} thoroughly", "expected_output": "Research findings", "agent_role": "Researcher"},
            {"name": "Write Report", "description": "Write a report on {topic}", "expected_output": "Final report", "agent_role": "Writer"},
        ],
    },
    "code_review": {
        "name": "Code Review Crew",
        "description": "A crew for reviewing and improving code.",
        "process": "sequential",
        "agents": [
            {"role": "Reviewer", "goal": "Identify bugs, security issues, and improvements", "backstory": "Senior engineer with code review expertise"},
            {"role": "Documenter", "goal": "Document findings and suggestions", "backstory": "Technical documentation specialist"},
        ],
        "tasks": [
            {"name": "Review Code", "description": "Review the provided code for issues", "expected_output": "List of findings", "agent_role": "Reviewer"},
            {"name": "Document", "description": "Create review summary document", "expected_output": "Review report", "agent_role": "Documenter"},
        ],
    },
}


def _render_wizard() -> None:
    """Render the guided wizard mode — 5-step crew creation flow."""
    cm = _crew_model()
    # Wizard state stored transiently during the wizard session
    wizard_data: dict[str, Any] = {
        "template": "blank",
        "name": cm.name,
        "description": cm.description,
        "process": cm.process,
        "agents": [a.model_dump() for a in cm.agents],
        "tasks": [t.model_dump() for t in cm.tasks],
        "inputs": [v.model_dump() for v in cm.inputs],
    }

    # Save/Load bar at top
    _render_save_load_bar(cm)

    # Step progress indicator
    step_labels = ["Template", "Goal", "Agents", "Tasks", "Review"]
    step_ref = {"current": 0}

    # Progress bar
    progress = ui.linear_progress(value=0.0).classes("w-full q-mb-md")
    step_label = ui.label("Step 1: Choose Template").classes(
        THEME.typography.SECTION_TITLE + " q-mb-md"
    )

    # Step indicator chips
    with ui.row().classes("q-gutter-sm q-mb-lg w-full justify-center") as step_chips:
        chip_refs: list[ui.badge] = []
        for i, sl in enumerate(step_labels):
            chip = ui.badge(
                f"{i + 1}. {sl}",
                color="grey" if i > 0 else "primary",
            )
            chip_refs.append(chip)

    def _update_progress() -> None:
        step = step_ref["current"]
        progress.set_value((step + 1) / len(step_labels))
        step_label.set_text(f"Step {step + 1}: {step_labels[step]}")
        for i, chip in enumerate(chip_refs):
            if i < step:
                chip.props("color=positive")
            elif i == step:
                chip.props("color=primary")
            else:
                chip.props("color=grey")

    # Step content area
    step_area = ui.column().classes("w-full")

    # Pre-create all steps (visibility toggled)
    with step_area:

        # --- Step 0: Choose Template ---
        step0 = ui.column().classes("w-full")
        with step0:
            ui.label("Choose a Starting Point").classes(THEME.typography.SECTION_TITLE)
            ui.label(
                "Select a template to get started quickly, or begin from scratch."
            ).classes("text-body2 q-mb-md")

            template_select = _full_select(
                label="Template",
                options={
                    "Blank Crew": "blank",
                    "Research Crew": "research",
                    "Code Review Crew": "code_review",
                },
                value=wizard_data["template"],
            )

            with ui.card().props(THEME.component.CARD["props"]).classes("w-full q-mt-md"):
                template_desc = ui.label("Start with an empty crew — you'll add agents and tasks manually.").classes("text-body2")

            def _update_template_desc() -> None:
                sel = template_select.value
                descriptions = {
                    "blank": "Start with an empty crew — you'll add agents and tasks manually.",
                    "research": "Researcher + Writer agents with predefined research and report tasks.",
                    "code_review": "Code Reviewer + Documenter agents with code review workflow.",
                }
                template_desc.set_text(descriptions.get(sel, descriptions["blank"]))
                wizard_data["template"] = sel
                # Populate agents/tasks from the selected template
                tmpl = _WIZARD_TEMPLATES.get(sel, _WIZARD_TEMPLATES["blank"])
                wizard_data["agents"] = [dict(ag) for ag in tmpl.get("agents", [])]
                wizard_data["tasks"] = [dict(tk) for tk in tmpl.get("tasks", [])]
                _wizard_agent_list.refresh()
                _wizard_task_list.refresh()

            template_select.on("update:model-value", lambda _: _update_template_desc())

        # --- Step 1: Goal ---
        step1 = ui.column().classes("w-full")
        step1.set_visibility(False)
        with step1:
            ui.label("Crew Goal & Identity").classes(THEME.typography.SECTION_TITLE)
            ui.label(
                "Define what this crew will do. Keep it simple — "
                "you can refine details later in Advanced mode."
            ).classes("text-body2 q-mb-md")

            wizard_name = _full_input(label="Crew Name *", value=wizard_data["name"])
            wizard_desc = _full_textarea(label="Description", value=wizard_data["description"])
            wizard_process = _full_select(
                label="Process Type",
                options=["sequential", "hierarchical"],
                value=wizard_data["process"],
            )

            ui.label(
                "Sequential: tasks run one after another. "
                "Hierarchical: a manager agent delegates tasks."
            ).classes("text-caption text-grey-6")

        # --- Step 2: Agents ---
        step2 = ui.column().classes("w-full")
        step2.set_visibility(False)
        with step2:
            ui.label("Add Agents").classes(THEME.typography.SECTION_TITLE)
            ui.label(
                "Define the agents that will work on your tasks. "
                "Each agent needs a role, goal, and optionally a backstory."
            ).classes("text-body2 q-mb-md")

            wizard_agents_col = ui.column().classes("w-full q-gutter-sm")

            @ui.refreshable
            def _wizard_agent_list() -> None:
                wizard_agents_col.clear()
                with wizard_agents_col:
                    for i, ag in enumerate(wizard_data["agents"]):
                        with ui.card().props(THEME.component.CARD["props"]).classes("w-full"):
                            with ui.row().classes("items-center justify-between w-full"):
                                ui.label(ag.get("role", f"Agent {i + 1}")).classes(THEME.typography.CARD_TITLE)
                                ui.button(
                                    icon="delete",
                                    on_click=lambda idx=i: _remove_wiz_agent(idx),
                                ).props(THEME.component.BTN_ICON["props"] + " color=negative")
                            ui.label(f"Goal: {ag.get('goal', '')}").classes("text-body2")
                            if ag.get("backstory"):
                                ui.label(f"Backstory: {ag['backstory'][:60]}...").classes("text-caption text-grey-7")

            def _remove_wiz_agent(idx: int) -> None:
                del wizard_data["agents"][idx]
                _wizard_agent_list.refresh()

            def _open_wiz_agent_dialog() -> None:
                with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                    ui.label("Add Agent").classes(THEME.typography.SECTION_TITLE)
                    ag_role = _full_input(label="Role *", value="")
                    ag_goal = _full_input(label="Goal *", value="")
                    ag_backstory = _full_textarea(label="Backstory", value="")
                    ag_tools_list = ui.column().classes("q-gutter-xs w-full")
                    tool_checks: dict[str, ui.checkbox] = {}
                    ui.label("Tools (optional):").classes("text-body2 q-mt-sm")
                    with ag_tools_list:
                        for tool_info in _all_tool_options():
                            label = tool_info["name"]
                            if tool_info.get("_custom"):
                                label += " (custom)"
                            tool_checks[tool_info["name"]] = ui.checkbox(
                                label, value=False,
                            )

                    def _save_wiz_agent() -> None:
                        role_val = (ag_role.value or "").strip()
                        goal_val = (ag_goal.value or "").strip()
                        if not role_val:
                            ui.notify("Agent role is required", type="negative")
                            return
                        if not goal_val:
                            ui.notify("Agent goal is required", type="negative")
                            return
                        sel_tools = [tn for tn, cb in tool_checks.items() if cb.value]
                        wizard_data["agents"].append({
                            "role": role_val,
                            "goal": goal_val,
                            "backstory": (ag_backstory.value or "").strip(),
                            "tools": sel_tools,
                        })
                        dialog.close()
                        _wizard_agent_list.refresh()

                    with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
                        ui.button("Cancel", on_click=dialog.close).props(
                            THEME.component.BTN_SECONDARY["props"]
                        )
                        ui.button("Add Agent", icon="add", on_click=_save_wiz_agent).props(
                            THEME.component.BTN_PRIMARY["props"]
                        )
                dialog.open()

            _wizard_agent_list()
            ui.button("+ Add Agent", icon="add", on_click=_open_wiz_agent_dialog).props(
                THEME.component.BTN_PRIMARY["props"] + " q-mt-sm"
            )

        # --- Step 3: Tasks ---
        step3 = ui.column().classes("w-full")
        step3.set_visibility(False)
        with step3:
            ui.label("Define Tasks").classes(THEME.typography.SECTION_TITLE)
            ui.label(
                "Define the tasks your agents will execute. "
                "Each task needs a name, description, and expected output."
            ).classes("text-body2 q-mb-md")

            wizard_tasks_col = ui.column().classes("w-full q-gutter-sm")

            @ui.refreshable
            def _wizard_task_list() -> None:
                wizard_tasks_col.clear()
                with wizard_tasks_col:
                    for i, tk in enumerate(wizard_data["tasks"]):
                        with ui.card().props(THEME.component.CARD["props"]).classes("w-full"):
                            with ui.row().classes("items-center justify-between w-full"):
                                ui.label(tk.get("name", f"Task {i + 1}")).classes(THEME.typography.CARD_TITLE)
                                ui.button(
                                    icon="delete",
                                    on_click=lambda idx=i: _remove_wiz_task(idx),
                                ).props(THEME.component.BTN_ICON["props"] + " color=negative")
                            ui.label(f"Agent: {tk.get('agent_role', '(unassigned)')}").classes("text-body2")
                            ui.label(f"Desc: {tk.get('description', '')[:60]}...").classes("text-caption text-grey-7")

            def _remove_wiz_task(idx: int) -> None:
                del wizard_data["tasks"][idx]
                _wizard_task_list.refresh()

            def _open_wiz_task_dialog() -> None:
                agent_opts = {"(unassigned)": ""}
                for ag in wizard_data["agents"]:
                    agent_opts[ag["role"]] = ag["role"]

                with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                    ui.label("Add Task").classes(THEME.typography.SECTION_TITLE)
                    tk_name = _full_input(label="Name *", value="")
                    tk_desc = _full_textarea(label="Description *", value="")
                    tk_output = _full_textarea(label="Expected Output *", value="")
                    tk_agent = _full_select(label="Assigned Agent", options=agent_opts, value="")

                    def _save_wiz_task() -> None:
                        name_val = (tk_name.value or "").strip()
                        desc_val = (tk_desc.value or "").strip()
                        output_val = (tk_output.value or "").strip()
                        if not name_val:
                            ui.notify("Task name is required", type="negative")
                            return
                        if not desc_val:
                            ui.notify("Task description is required", type="negative")
                            return
                        if not output_val:
                            ui.notify("Expected output is required", type="negative")
                            return
                        wizard_data["tasks"].append({
                            "name": name_val,
                            "description": desc_val,
                            "expected_output": output_val,
                            "agent_role": tk_agent.value or None,
                        })
                        dialog.close()
                        _wizard_task_list.refresh()

                    with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
                        ui.button("Cancel", on_click=dialog.close).props(
                            THEME.component.BTN_SECONDARY["props"]
                        )
                        ui.button("Add Task", icon="add", on_click=_save_wiz_task).props(
                            THEME.component.BTN_PRIMARY["props"]
                        )
                dialog.open()

            _wizard_task_list()
            ui.button("+ Add Task", icon="add", on_click=_open_wiz_task_dialog).props(
                THEME.component.BTN_PRIMARY["props"] + " q-mt-sm"
            )

        # --- Step 4: Review & Save ---
        step4 = ui.column().classes("w-full")
        step4.set_visibility(False)
        with step4:
            ui.label("Review & Save").classes(THEME.typography.SECTION_TITLE)
            ui.label(
                "Review your crew configuration below. "
                "Click 'Save Crew' to persist it, or go back to adjust."
            ).classes("text-body2 q-mb-md")

            review_card = ui.card().props(THEME.component.CARD["props"]).classes("w-full")

    # All step containers for easy visibility control
    all_steps = [step0, step1, step2, step3, step4]

    def _show_step(n: int) -> None:
        for i, s in enumerate(all_steps):
            s.set_visibility(i == n)
        step_ref["current"] = n
        _update_progress()
        # Update review card on step 4
        if n == 4:
            _refresh_review()

    def _refresh_review() -> None:
        review_card.clear()
        with review_card:
            ui.label(f"Crew: {wizard_data['name']}").classes(THEME.typography.CARD_TITLE)
            ui.label(f"Process: {wizard_data['process']}").classes("text-body2")
            if wizard_data["description"]:
                ui.label(f"Description: {wizard_data['description']}").classes("text-body2")

            ui.separator().classes("q-my-sm")
            ui.label(f"Agents ({len(wizard_data['agents'])})").classes(THEME.typography.CARD_TITLE)
            for ag in wizard_data["agents"]:
                ui.label(f"• {ag['role']} — {ag['goal'][:50]}...").classes("text-body2")

            ui.separator().classes("q-my-sm")
            ui.label(f"Tasks ({len(wizard_data['tasks'])})").classes(THEME.typography.CARD_TITLE)
            for tk in wizard_data["tasks"]:
                ui.label(f"• {tk['name']} → {tk.get('agent_role', 'unassigned')}").classes("text-body2")

    def _apply_wizard_to_model() -> None:
        """Convert wizard data back to CrewModel and persist.

        Builds a new CrewModel from wizard data, validates + persists it,
        and only mutates the live ``cm`` on success — never corrupts
        the in-memory model on validation failure.
        """
        # Build agents from wizard data
        agents = [
            m.AgentModel(
                role=ag_data["role"],
                goal=ag_data["goal"],
                backstory=ag_data.get("backstory", ""),
                tools=[
                    m.ToolRef(kind="builtin", name=tn)
                    for tn in ag_data.get("tools", [])
                ],
            )
            for ag_data in wizard_data["agents"]
        ]

        # Build tasks from wizard data
        tasks = [
            m.TaskModel(
                name=tk_data["name"],
                description=tk_data["description"],
                expected_output=tk_data["expected_output"],
                agent_role=tk_data.get("agent_role"),
            )
            for tk_data in wizard_data["tasks"]
        ]

        # Build new CrewModel and persist — validates everything
        new_cm = m.CrewModel(
            name=wizard_data["name"],
            description=wizard_data["description"],
            process=wizard_data["process"],  # type: ignore[arg-type]
            agents=agents,
            tasks=tasks,
        )
        if _persist(new_cm) != 0:
            return  # error notification handled inside _persist

        # Only mutate live cm on success
        cm.name = new_cm.name
        cm.description = new_cm.description
        cm.process = new_cm.process  # type: ignore[assignment]
        cm.agents.clear()
        cm.agents.extend(new_cm.agents)
        cm.tasks.clear()
        cm.tasks.extend(new_cm.tasks)
        ui.notify(
            f"Crew '{cm.name}' saved! Switch to Advanced mode for detailed editing.",
            type="positive",
            position="top",
        )

    # --- Navigation buttons ---
    with ui.row().classes("w-full justify-between q-mt-lg"):
        prev_btn = ui.button("← Back", on_click=lambda: _show_step(max(0, step_ref["current"] - 1))).props(
            THEME.component.BTN_SECONDARY["props"]
        )
        prev_btn.set_visibility(False)

        next_btn = ui.button("Next →", on_click=lambda: _navigate_next()).props(
            THEME.component.BTN_PRIMARY["props"]
        )

        save_btn = ui.button("Save Crew ✓", icon="save", on_click=_apply_wizard_to_model).props(
            "unelevated color=positive"
        )
        save_btn.set_visibility(False)

    def _navigate_next() -> None:
        step = step_ref["current"]
        if step < len(all_steps) - 1:
            _show_step(step + 1)
            if step + 1 == len(all_steps) - 1:
                next_btn.set_visibility(False)
                save_btn.set_visibility(True)
            prev_btn.set_visibility(True)

    def _handle_step_visibility() -> None:
        step = step_ref["current"]
        prev_btn.set_visibility(step > 0)
        if step == len(all_steps) - 1:
            next_btn.set_visibility(False)
            save_btn.set_visibility(True)
        else:
            next_btn.set_visibility(True)
            save_btn.set_visibility(False)

    # Override _show_step to also handle button visibility
    _orig_show_step = _show_step
    def _show_step_wrapped(n: int) -> None:
        _orig_show_step(n)
        _handle_step_visibility()

    # Replace _show_step in the prev/next button closures by reassignment hack
    # (the prev_btn/next_btn already captured _show_step; we update the global ref)
    show_step_ref: dict[str, Callable[[int], None]] = {"fn": _show_step_wrapped}

    # Show step 1 initially
    _show_step_wrapped(0)


# ============================================================================
#  Main Builder Entry Point
# ============================================================================

def render_builder() -> None:
    """Render the complete Builder page with tabbed interface.

    Called from ``app.py``'s ``/builder`` route.
    """
    cm = _crew_model()

    # Mode toggle: Wizard vs Advanced
    mode_ref = {"mode": "advanced"}  # default to advanced

    with ui.row().classes("w-full items-center justify-between q-mb-md"):
        ui.label(f"Crew Builder — {cm.name}").classes(THEME.typography.SECTION_TITLE)
        with ui.row().classes("q-gutter-sm"):
            mode_toggle = ui.toggle(
                options={"Advanced": "advanced", "Wizard": "wizard"},
                value="advanced",
            )

    # Save/Load bar (always visible)
    _render_save_load_bar(cm)

    # Mode-specific content area
    wizard_container = ui.column().classes("w-full")
    advanced_container = ui.column().classes("w-full")

    with advanced_container:
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

    with wizard_container:
        wizard_container.set_visibility(False)

    def _switch_mode() -> None:
        val = mode_toggle.value
        mode_ref["mode"] = val
        if val == "wizard":
            advanced_container.set_visibility(False)
            wizard_container.clear()
            with wizard_container:
                _render_wizard()
            wizard_container.set_visibility(True)
        else:
            wizard_container.set_visibility(False)
            advanced_container.set_visibility(True)

    mode_toggle.on("update:model-value", lambda _: _switch_mode())
