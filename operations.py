"""Operations toolkit for gui-crew — templates, history, import/export, single-task testing.

Provides all operational tools for managing CrewAI configurations from the
Operations page.  Template data and history persistence are pure-Python;
NiceGUI rendering is confined to ``render_operations()`` and its helpers.

Public surface
--------------
* ``render_operations()`` — main page renderer for ``/operations`` route.
* ``BUILTIN_TEMPLATES`` — dict of 5 pre-defined crew templates.
* ``save_custom_template(name, description)`` — persist current crew as a template.
* ``save_run_record(record)`` — write a ``RunRecord`` JSON file to ``history/``.
* ``load_history()`` — scan ``history/`` and return ``list[RunRecord]``.
* ``export_crew_jsonc()`` / ``export_crew_yaml()`` — serialise active crew.
* ``import_crew_file(content, filename)`` — parse and return a ``CrewModel``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nicegui import app, ui

import models
from styles import THEME

_LOG = logging.getLogger(__name__)

# ============================================================================
#  Built-in Template Gallery  (Task 2.9)
# ============================================================================

BUILTIN_TEMPLATES: dict[str, models.CrewModel] = {
    "Research Crew": models.CrewModel(
        name="Research Crew",
        description="Research a topic and produce a comprehensive report.",
        process="sequential",
        inputs=[models.InputVar(name="topic", type="str", description="Topic to research")],
        agents=[
            models.AgentModel(
                role="Researcher",
                goal="Research {topic} thoroughly using available tools",
                backstory="Expert researcher with deep analytical skills.",
                tools=[models.ToolRef(kind="builtin", name="SerperDevTool")],
            ),
            models.AgentModel(
                role="Writer",
                goal="Synthesise research findings into a clear report",
                backstory="Skilled technical writer who turns complex data into accessible prose.",
                markdown=True,
            ),
        ],
        tasks=[
            models.TaskModel(
                name="research",
                description="Research {topic} using available tools and compile findings.",
                expected_output="A detailed research document covering all aspects of {topic}.",
                agent_role="Researcher",
            ),
            models.TaskModel(
                name="write_report",
                description="Write a comprehensive report based on the research findings.",
                expected_output="A well-structured markdown report with introduction, body, and conclusion.",
                agent_role="Writer",
                context=["research"],
                markdown=True,
            ),
        ],
    ),
    "Code Review Crew": models.CrewModel(
        name="Code Review Crew",
        description="Review code for quality, security, and best practices.",
        process="sequential",
        inputs=[models.InputVar(name="code", type="str", description="Code to review")],
        agents=[
            models.AgentModel(
                role="Code Reviewer",
                goal="Review {code} for bugs, security issues, and style violations",
                backstory="Senior software engineer with 15 years of code review experience.",
                allow_code_execution=True,
            ),
        ],
        tasks=[
            models.TaskModel(
                name="code_review",
                description="Review the provided code: {code}. Check for bugs, security vulnerabilities, style issues, and suggest improvements.",
                expected_output="A detailed code review report with findings categorised by severity.",
                agent_role="Code Reviewer",
            ),
        ],
    ),
    "Content Writer Crew": models.CrewModel(
        name="Content Writer Crew",
        description="Create high-quality content for blogs, articles, or social media.",
        process="sequential",
        inputs=[
            models.InputVar(name="topic", type="str", description="Content topic"),
            models.InputVar(name="audience", type="str", description="Target audience"),
        ],
        agents=[
            models.AgentModel(
                role="Content Writer",
                goal="Write engaging content about {topic} for {audience}",
                backstory="Professional content writer specialising in {topic}.",
            ),
            models.AgentModel(
                role="Editor",
                goal="Polish and refine the written content for clarity and impact",
                backstory="Experienced editor with an eye for grammar, style, and flow.",
            ),
        ],
        tasks=[
            models.TaskModel(
                name="write_content",
                description="Write engaging content about {topic} targeting {audience}.",
                expected_output="A draft article or post about {topic}.",
                agent_role="Content Writer",
                markdown=True,
            ),
            models.TaskModel(
                name="edit_content",
                description="Edit and refine the draft content for grammar, style, and readability.",
                expected_output="A polished, publication-ready article.",
                agent_role="Editor",
                context=["write_content"],
                markdown=True,
            ),
        ],
    ),
    "Data Analysis Crew": models.CrewModel(
        name="Data Analysis Crew",
        description="Analyse datasets and produce actionable insights.",
        process="sequential",
        inputs=[models.InputVar(name="dataset_description", type="str", description="Dataset to analyse")],
        agents=[
            models.AgentModel(
                role="Data Analyst",
                goal="Analyse {dataset_description} and extract meaningful insights",
                backstory="Data scientist skilled in statistical analysis and visualisation.",
                allow_code_execution=True,
            ),
        ],
        tasks=[
            models.TaskModel(
                name="analyse_data",
                description="Analyse the dataset: {dataset_description}. Find patterns, trends, and anomalies. Produce a summary of key findings.",
                expected_output="An analysis report with key insights, trends, and recommendations.",
                agent_role="Data Analyst",
            ),
        ],
    ),
    "Customer Support Crew": models.CrewModel(
        name="Customer Support Crew",
        description="Handle customer inquiries with empathy and accuracy.",
        process="sequential",
        inputs=[models.InputVar(name="customer_issue", type="str", description="Customer issue to resolve")],
        agents=[
            models.AgentModel(
                role="Support Agent",
                goal="Resolve {customer_issue} with empathy and accuracy",
                backstory="Experienced customer support professional who puts customers first.",
                memory=True,
            ),
        ],
        tasks=[
            models.TaskModel(
                name="handle_inquiry",
                description="Customer inquiry: {customer_issue}. Provide a helpful, empathetic, and accurate response.",
                expected_output="A friendly resolution message addressing the customer's issue.",
                agent_role="Support Agent",
            ),
        ],
    ),
}


# ============================================================================
#  History Persistence  (Task 2.11)
# ============================================================================

_HISTORY_DIR = "history"


def _history_path() -> Path:
    """Return the absolute path to the history directory."""
    return Path(_HISTORY_DIR).resolve()


def save_run_record(
    record: models.RunRecord,
    base_dir: str | Path = _HISTORY_DIR,
) -> Path:
    """Save a ``RunRecord`` to ``<base_dir>/<crew_name>/<timestamp>.json``.

    Parameters
    ----------
    record : models.RunRecord
        The run record to persist.
    base_dir : str | Path
        Root directory for history storage.

    Returns
    -------
    Path
        The file path where the record was written.
    """
    base = Path(base_dir).resolve()
    crew_dir = base / _safe_filename(record.crew_name)
    crew_dir.mkdir(parents=True, exist_ok=True)

    import secrets

    ts_base = record.timestamp.strftime("%Y%m%d_%H%M%S_%f")
    # Atomic filename: use exclusive-create (``x`` mode) with a retry loop
    # so concurrent calls never clobber each other.
    content = json.dumps(
        record.model_dump(mode="json"),
        indent=2,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    for attempt in range(100):
        suffix = f"_{secrets.token_hex(4)}" if attempt > 0 else ""
        filename = f"{ts_base}{suffix}.json"
        filepath = crew_dir / filename
        try:
            with open(filepath, "xb") as f:
                f.write(content)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError(
            f"Could not create unique filename in {crew_dir} "
            f"after 100 attempts"
        )
    _LOG.info("Saved run record to %s", filepath)
    return filepath


def load_history(
    base_dir: str | Path = _HISTORY_DIR,
) -> list[models.RunRecord]:
    """Scan ``base_dir`` and load all ``RunRecord`` JSON files.

    Parameters
    ----------
    base_dir : str | Path
        Root directory for history storage.

    Returns
    -------
    list[models.RunRecord]
        All parsed run records, sorted newest-first.
    """
    base = Path(base_dir).resolve()
    if not base.is_dir():
        return []

    records: list[models.RunRecord] = []
    for json_file in sorted(
        base.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            records.append(models.RunRecord(**data))
        except Exception as exc:
            _LOG.warning("Skipping invalid history file %s: %s", json_file, exc)

    return records


def _as_utc(dt: datetime) -> datetime:
    """Normalise a datetime to UTC, assuming UTC for naive values."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def filter_history(
    records: list[models.RunRecord],
    *,
    crew_name: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[models.RunRecord]:
    """Filter a list of run records by optional criteria.

    Parameters
    ----------
    records : list[models.RunRecord]
        The full history list.
    crew_name : str | None
        If set, keep only records matching this crew name (case-insensitive).
    status : str | None
        If set, keep only records with this status.
    date_from : datetime | None
        If set, keep only records on or after this date.
    date_to : datetime | None
        If set, keep only records on or before this date.

    Returns
    -------
    list[models.RunRecord]
        Filtered list, same sort order as input.
    """
    result = records
    if crew_name:
        cname_lower = crew_name.lower()
        result = [r for r in result if cname_lower in r.crew_name.lower()]
    if status:
        result = [r for r in result if r.status == status]
    if date_from is not None or date_to is not None:
        # Normalise both sides to UTC so mixed naive/aware datetimes
        # never raise ``TypeError``.  Naive inputs are assumed UTC.
        d_from = _as_utc(date_from) if date_from else date_from
        d_to = _as_utc(date_to) if date_to else date_to

        result = [
            r for r in result
            if (d_from is None or _as_utc(r.timestamp) >= d_from)
            and (d_to is None or _as_utc(r.timestamp) <= d_to)
        ]
    return result


def _safe_filename(name: str) -> str:
    """Sanitise a crew name for use as a directory name."""
    return re.sub(r"[^\w\-_]", "_", name.strip() or "unknown")


# ============================================================================
#  Comparison Helpers  (Task 2.13)
# ============================================================================

def _diff_highlight(
    left_val: Any,
    right_val: Any,
) -> tuple[str, str]:
    """Return CSS classes to highlight differences between two values."""
    if left_val != right_val:
        return "bg-warning", "bg-warning"
    return "", ""


# ============================================================================
#  Import / Export  (Tasks 2.14, 2.15)
# ============================================================================

def export_crew_jsonc(crew_model: models.CrewModel) -> str:
    """Export a crew model as CrewAI-compatible JSON (with .jsonc extension).

    Parameters
    ----------
    crew_model : models.CrewModel
        The crew to export.

    Returns
    -------
    str
        Prettified JSON string.
    """
    return crew_model.to_crewai_json(indent=2)


def export_crew_yaml(crew_model: models.CrewModel) -> str:
    """Export a crew model as CrewAI-compatible YAML.

    Parameters
    ----------
    crew_model : models.CrewModel
        The crew to export.

    Returns
    -------
    str
        YAML string.
    """
    return crew_model.to_crewai_yaml()


def import_crew_file(content: str, filename: str) -> models.CrewModel:
    """Parse a crew file and return a ``CrewModel``.

    Parameters
    ----------
    content : str
        Raw file content (JSON, JSONC, or YAML).
    filename : str
        Original filename, used to detect format.

    Returns
    -------
    models.CrewModel
        Parsed crew model.

    Raises
    ------
    ValueError
        If the file cannot be parsed or contains validation errors.
    """
    ext = Path(filename).suffix.lower().lstrip(".")

    if ext in ("json", "jsonc"):
        # Strip JSONC comments (// and /* */)
        content = _strip_bom(content)
        cleaned = _strip_jsonc_comments(content)
        try:
            return models.CrewModel.from_crewai_json(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ) from exc
        except Exception as exc:
            raise ValueError(f"Failed to parse crew JSON: {exc}") from exc

    elif ext in ("yaml", "yml"):
        try:
            return models.CrewModel.from_crewai_yaml(content)
        except Exception as exc:
            raise ValueError(f"Failed to parse crew YAML: {exc}") from exc

    else:
        raise ValueError(
            f"Unsupported file format: .{ext}. "
            f"Supported: .json, .jsonc, .yaml, .yml"
        )


def _strip_bom(text: str) -> str:
    """Remove UTF-8 BOM (byte-order mark) from the beginning of a string."""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _strip_jsonc_comments(text: str) -> str:
    """Remove ``//`` line comments and ``/* */`` block comments from JSONC.

    Uses a single-pass state machine that tracks string boundaries and
    escape sequences (``\\``, ``\\"``) so that ``//`` and ``/*`` inside
    strings are never mistaken for comments.
    """
    result: list[str] = []
    i = 0
    length = len(text)
    in_string = False
    in_block = False
    escape = False

    while i < length:
        ch = text[i]

        # --- inside a block comment: consume until */ ---
        if in_block:
            if ch == "*" and i + 1 < length and text[i + 1] == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue

        # --- inside a string literal: handle escapes carefully ---
        if in_string:
            if escape:
                escape = False
                result.append(ch)
                i += 1
                continue
            if ch == "\\":
                escape = True
                result.append(ch)
                i += 1
                continue
            if ch == '"':
                in_string = False
                result.append(ch)
                i += 1
                continue
            result.append(ch)
            i += 1
            continue

        # --- outside string / block comment ---
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < length:
            if text[i + 1] == "/":
                # Line comment: skip to end of line
                i += 2
                while i < length and text[i] != "\n":
                    i += 1
                if i < length and text[i] == "\n":
                    result.append("\n")
                    i += 1
                continue
            if text[i + 1] == "*":
                # Block comment: skip until */
                in_block = True
                i += 2
                continue

        result.append(ch)
        i += 1

    return "".join(result)


# ============================================================================
#  Single-Task Test  (Task 2.17)
# ============================================================================

def run_single_task(
    crew_model: models.CrewModel,
    task_name: str,
    mock_context: str,
) -> str:
    """Execute a single task in isolation with mock context.

    Uses ``CrewEngine.test_task()`` under the hood.

    Parameters
    ----------
    crew_model : models.CrewModel
        The crew containing the task.
    task_name : str
        Name of the task to execute.
    mock_context : str
        Mock context text to feed to the task.

    Returns
    -------
    str
        The task output text.
    """
    from crew_engine import CrewEngine

    engine = CrewEngine()
    return engine.test_task(crew_model, task_name, mock_context)


# ============================================================================
#  UI Rendering — Template Gallery  (Tasks 2.9, 2.10)
# ============================================================================

def _render_template_gallery() -> None:
    """Render the built-in and custom template gallery."""
    ui.label("Template Gallery").classes(THEME.typography.CARD_TITLE)

    # Built-in templates
    with ui.expansion("Built-in Templates", value=True).classes("w-full"):
        with ui.row().classes("gap-4 flex-wrap"):
            for name, crew in BUILTIN_TEMPLATES.items():
                with (ui.card().classes("w-64")
                      .props(THEME.component.CARD["props"])):
                    ui.label(name).classes(THEME.typography.H6)
                    ui.label(crew.description).classes("text-caption text-grey-6")
                    ui.label(
                        f"{len(crew.agents)} agent(s), {len(crew.tasks)} task(s)"
                    ).classes("text-caption")
                    ui.button(
                        "Use Template",
                        icon="content_copy",
                        on_click=lambda _name=name: _load_template(_name),
                    ).props(THEME.component.BTN_PRIMARY["props"])

    # Custom templates
    with ui.expansion("Custom Templates", value=False).classes("w-full"):
        custom: dict = app.storage.user.get("custom_templates", {}) or {}
        if custom:
            with ui.row().classes("gap-4 flex-wrap"):
                for name, info in custom.items():
                    with ui.card().classes("w-64").props(
                        THEME.component.CARD["props"]
                    ):
                        ui.label(name).classes(THEME.typography.H6)
                        ui.badge("Custom", color="accent")
                        desc = info.get("description", "")
                        if desc:
                            ui.label(desc).classes("text-caption text-grey-6")
                        ui.button(
                            "Use Template",
                            icon="content_copy",
                            on_click=lambda _name=name: _load_template(_name),
                        ).props(THEME.component.BTN_PRIMARY["props"])
        else:
            ui.label(
                "No custom templates yet. Configure a crew and click "
                "\"Save as Template\"."
            ).classes("text-caption text-grey-6")

    # Save-as-template button
    ui.separator()
    ui.button(
        "Save as Template",
        icon="bookmark",
        on_click=_save_template_dialog,
    ).props(THEME.component.BTN_SECONDARY["props"])


def _load_template(name: str) -> None:
    """Load a template (built-in or custom) into the current crew model."""
    if name in BUILTIN_TEMPLATES:
        crew = BUILTIN_TEMPLATES[name]
        app.storage.user["crew_model"] = crew.model_dump(mode="json")
        ui.notify(f"Template '{name}' loaded into Builder", type="positive")
    else:
        custom: dict = app.storage.user.get("custom_templates", {}) or {}
        if name in custom:
            crew_dict = custom[name].get("crew", {})
            app.storage.user["crew_model"] = crew_dict
            ui.notify(f"Custom template '{name}' loaded into Builder", type="positive")
        else:
            ui.notify(f"Template '{name}' not found", type="negative")


async def _save_template_dialog() -> None:
    """Show a dialog to save the current crew as a custom template."""
    crew_dict = app.storage.user.get("crew_model")
    if not crew_dict:
        ui.notify("No crew configured. Build a crew first.", type="warning")
        return

    name_input = ui.input("Template Name", placeholder="My Research Crew").props(
        THEME.component.INPUT["props"]
    )
    desc_input = (
        ui.textarea("Description", placeholder="Optional description")
        .props(THEME.component.INPUT["props"])
        .classes("w-full")
    )

    with ui.dialog() as dialog, ui.card():
        ui.label("Save as Template").classes(THEME.typography.H6)
        name_input
        desc_input
        with ui.row().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=lambda: _persist_template(
                name_input.value or "Untitled",
                desc_input.value or "",
                crew_dict,
                dialog,
            )).props(THEME.component.BTN_PRIMARY["props"])

    dialog.open()


def _persist_template(
    name: str,
    description: str,
    crew_dict: dict,
    dialog: Any,
) -> None:
    """Persist the current crew as a custom template in session storage."""
    if not name.strip():
        ui.notify("Template name is required.", type="warning")
        return

    custom: dict = app.storage.user.get("custom_templates", {}) or {}
    custom[name.strip()] = {
        "description": description.strip(),
        "crew": crew_dict,
    }
    app.storage.user["custom_templates"] = custom
    dialog.close()
    ui.notify(f"Template '{name.strip()}' saved!", type="positive")
    # Refresh the template gallery
    _render_template_gallery.refresh()


# ============================================================================
#  UI Rendering — History  (Tasks 2.12, 2.13)
# ============================================================================

def _render_history() -> None:
    """Render the execution history list with filters and comparison."""
    ui.label("Execution History").classes(THEME.typography.CARD_TITLE)

    records = load_history()
    records = filter_history(records)  # initial — no filters

    # Store records in a mutable container so filtering can re-evaluate
    all_records: list[models.RunRecord] = load_history()
    selected_indices: set[int] = set()

    # --- Filter row ---
    with ui.row().classes("gap-4 items-center"):
        crew_filter = (
            ui.input("Crew Name")
            .props(THEME.component.INPUT["props"])
            .classes("w-48")
        )
        status_filter = (
            ui.select(
                label="Status",
                options=["", "success", "failed", "cancelled"],
                value="",
            )
            .props(THEME.component.INPUT["props"])
            .classes("w-40")
        )
        ui.button(
            "Apply Filters",
            icon="filter_alt",
            on_click=lambda: _apply_history_filters(
                all_records,
                crew_filter.value,
                status_filter.value,
                history_table,
            ),
        ).props(THEME.component.BTN_SECONDARY["props"])
        ui.button(
            "Clear Filters",
            icon="clear",
            on_click=lambda: _clear_history_filters(
                all_records,
                crew_filter,
                status_filter,
                history_table,
            ),
        ).props("flat")

    # --- History table ---
    columns = [
        {"name": "date", "label": "Date", "field": "date", "sortable": True},
        {"name": "crew", "label": "Crew Name", "field": "crew", "sortable": True},
        {"name": "status", "label": "Status", "field": "status", "sortable": True},
        {"name": "duration", "label": "Duration (ms)", "field": "duration", "sortable": True},
        {"name": "cost", "label": "Cost ($)", "field": "cost", "sortable": True},
        {"name": "tokens", "label": "Tokens", "field": "tokens", "sortable": True},
    ]

    rows = [
        {
            "key": r.timestamp.isoformat(),  # unique row identifier
            "date": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "crew": r.crew_name,
            "status": r.status,
            "duration": r.duration_ms,
            "cost": f"${r.cost:.4f}" if r.cost else "—",
            "tokens": r.token_usage.total_tokens,
        }
        for r in records
    ]

    history_table = (
        ui.table(
            columns=columns,
            rows=rows,
            row_key="key",
            selection="multiple",
        )
        .classes("w-full")
        .props("flat bordered")
    )

    # --- Compare button ---
    def _on_compare() -> None:
        selected = history_table.selected
        if not selected or len(selected) != 2:
            ui.notify("Select exactly 2 runs to compare.", type="warning")
            return
        selected_keys = [row[history_table.row_key] for row in selected]
        _show_comparison(all_records, selected_keys)

    ui.button(
        "Compare Selected",
        icon="compare",
        on_click=_on_compare,
    ).props(THEME.component.BTN_SECONDARY["props"])


def _apply_history_filters(
    all_records: list[models.RunRecord],
    crew_name: str | None,
    status: str | None,
    table: Any,
) -> None:
    """Apply filters and update the history table."""
    filtered = filter_history(
        all_records,
        crew_name=crew_name or None,
        status=status or None,
    )
    table.rows = [
        {
            "key": r.timestamp.isoformat(),
            "date": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "crew": r.crew_name,
            "status": r.status,
            "duration": r.duration_ms,
            "cost": f"${r.cost:.4f}" if r.cost else "—",
            "tokens": r.token_usage.total_tokens,
        }
        for r in filtered
    ]
    table.update()
    ui.notify(f"Showing {len(filtered)} record(s)", type="info")


def _clear_history_filters(
    all_records: list[models.RunRecord],
    crew_input: Any,
    status_select: Any,
    table: Any,
) -> None:
    """Clear filters and show all records."""
    crew_input.value = ""
    status_select.value = ""
    table.rows = [
        {
            "key": r.timestamp.isoformat(),
            "date": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "crew": r.crew_name,
            "status": r.status,
            "duration": r.duration_ms,
            "cost": f"${r.cost:.4f}" if r.cost else "—",
            "tokens": r.token_usage.total_tokens,
        }
        for r in all_records
    ]
    table.update()


def _show_comparison(
    records: list[models.RunRecord],
    selected_keys: list[str],
) -> None:
    """Open a side-by-side comparison dialog for two runs."""
    # Find the selected records by matching unique timestamp keys
    found: list[models.RunRecord] = []
    for key in selected_keys:
        for r in records:
            if r.timestamp.isoformat() == key:
                found.append(r)
                break

    if len(found) != 2:
        ui.notify("Could not find the selected records.", type="warning")
        return

    left, right = found[0], found[1]

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
        ui.label(f"Comparing: {left.crew_name}").classes(THEME.typography.H5)

        with ui.row().classes("w-full gap-4"):
            # Left column
            with ui.column().classes("w-1/2 border-r q-pa-md"):
                ui.label(left.timestamp.strftime("%Y-%m-%d %H:%M:%S")).classes(
                    "text-h6 text-bold"
                )
                ui.label(f"Status: {left.status}").classes(
                    _diff_highlight(left.status, right.status)[0]
                )
                ui.label(f"Duration: {left.duration_ms} ms").classes(
                    _diff_highlight(left.duration_ms, right.duration_ms)[0]
                )
                ui.label(
                    f"Tokens: {left.token_usage.total_tokens} "
                    f"(in: {left.token_usage.input_tokens}, "
                    f"out: {left.token_usage.output_tokens})"
                ).classes(
                    _diff_highlight(
                        left.token_usage.total_tokens,
                        right.token_usage.total_tokens,
                    )[0]
                )
                ui.label(f"Cost: ${left.cost:.6f}").classes(
                    _diff_highlight(left.cost, right.cost)[0]
                )

            # Right column
            with ui.column().classes("w-1/2 q-pa-md"):
                ui.label(right.timestamp.strftime("%Y-%m-%d %H:%M:%S")).classes(
                    "text-h6 text-bold"
                )
                ui.label(f"Status: {right.status}").classes(
                    _diff_highlight(left.status, right.status)[1]
                )
                ui.label(f"Duration: {right.duration_ms} ms").classes(
                    _diff_highlight(left.duration_ms, right.duration_ms)[1]
                )
                ui.label(
                    f"Tokens: {right.token_usage.total_tokens} "
                    f"(in: {right.token_usage.input_tokens}, "
                    f"out: {right.token_usage.output_tokens})"
                ).classes(
                    _diff_highlight(
                        left.token_usage.total_tokens,
                        right.token_usage.total_tokens,
                    )[1]
                )
                ui.label(f"Cost: ${right.cost:.6f}").classes(
                    _diff_highlight(left.cost, right.cost)[1]
                )

        ui.button("Close", on_click=dialog.close).props("flat")

    dialog.open()


# ============================================================================
#  UI Rendering — Import / Export  (Tasks 2.14, 2.15)
# ============================================================================

def _render_import_export() -> None:
    """Render the import/export panel."""
    ui.label("Import / Export").classes(THEME.typography.CARD_TITLE)

    with ui.row().classes("gap-4"):
        # Export section
        with ui.column().classes("gap-2"):
            ui.label("Export Crew").classes(THEME.typography.H6)

            def _export_jsonc() -> None:
                crew_dict = app.storage.user.get("crew_model")
                if not crew_dict:
                    ui.notify("No crew to export. Build a crew first.", type="warning")
                    return
                try:
                    crew = models.CrewModel(**crew_dict)
                    content = export_crew_jsonc(crew)
                    ui.download(
                        content.encode("utf-8"),
                        filename=f"{_safe_filename(crew.name)}.jsonc",
                        media_type="application/json",
                    )
                except Exception as exc:
                    ui.notify(f"Export failed: {exc}", type="negative")

            def _export_yaml() -> None:
                crew_dict = app.storage.user.get("crew_model")
                if not crew_dict:
                    ui.notify("No crew to export. Build a crew first.", type="warning")
                    return
                try:
                    crew = models.CrewModel(**crew_dict)
                    content = export_crew_yaml(crew)
                    ui.download(
                        content.encode("utf-8"),
                        filename=f"{_safe_filename(crew.name)}.yaml",
                        media_type="application/x-yaml",
                    )
                except Exception as exc:
                    ui.notify(f"Export failed: {exc}", type="negative")

            ui.button("Export as JSONC", icon="download", on_click=_export_jsonc).props(
                THEME.component.BTN_PRIMARY["props"]
            )
            ui.button("Export as YAML", icon="download", on_click=_export_yaml).props(
                THEME.component.BTN_SECONDARY["props"]
            )

        # Import section
        with ui.column().classes("gap-2"):
            ui.label("Import Crew").classes(THEME.typography.H6)

            def _import_file(event: Any) -> None:
                """Handle file upload and import crew configuration."""
                if not event or not hasattr(event, "content"):
                    ui.notify("No file selected.", type="warning")
                    return
                try:
                    content = event.content.read().decode("utf-8")
                    filename = getattr(event, "name", "crew.jsonc")
                    crew = import_crew_file(content, filename)
                    app.storage.user["crew_model"] = crew.model_dump(mode="json")
                    ui.notify(
                        f"Imported '{crew.name}' with {len(crew.agents)} agent(s) "
                        f"and {len(crew.tasks)} task(s).",
                        type="positive",
                    )
                except ValueError as exc:
                    ui.notify(str(exc), type="negative")
                except Exception as exc:
                    ui.notify(f"Import error: {exc}", type="negative")

            ui.upload(
                on_upload=_import_file,
                auto_upload=True,
                label="Choose a crew file (.json, .jsonc, .yaml, .yml)",
            ).props("accept=.json,.jsonc,.yaml,.yml").classes("w-80")


# ============================================================================
#  UI Rendering — Single-Task Test  (Task 2.17)
# ============================================================================

def _render_single_task_test() -> None:
    """Render the single-task test UI."""
    ui.label("Single-Task Test").classes(THEME.typography.CARD_TITLE)
    ui.label(
        "Test a single task in isolation with mock context — no full crew kickoff."
    ).classes("text-caption text-grey-6 q-mb-md")

    crew_dict = app.storage.user.get("crew_model")
    if not crew_dict:
        ui.label(
            "No crew configured. Build a crew first, then test individual tasks."
        ).classes("text-caption text-warning")
        return

    try:
        crew = models.CrewModel(**crew_dict)
    except Exception as exc:
        ui.label(f"Cannot parse crew model: {exc}").classes("text-caption text-negative")
        return

    task_names = [t.name for t in crew.tasks]
    if not task_names:
        ui.label("No tasks in the current crew.").classes("text-caption")
        return

    task_select = ui.select(
        label="Task",
        options=task_names,
        value=task_names[0],
    ).props(THEME.component.INPUT["props"]).classes("w-full")

    context_input = (
        ui.textarea(
            "Mock Context",
            placeholder="Enter mock context data for the task...",
        )
        .props(THEME.component.INPUT["props"])
        .classes("w-full")
    )

    output_label = (
        ui.label("Output will appear here after test...")
        .classes("text-caption text-grey-6 q-mt-md")
    )

    async def _run_test() -> None:
        task_name = task_select.value
        mock_context = context_input.value

        if not mock_context.strip():
            ui.notify(
                "Mock context is empty — task may fail without dependencies.",
                type="warning",
            )

        output_label.set_text(f"Running '{task_name}'...")
        try:
            # Force context_input to update (NiceGUI lazy eval)
            task_name = task_select.value
            mock_context = context_input.value
            result = run_single_task(crew, task_name, mock_context)
            output_label.set_text(f"Result:\n\n{result}")
        except Exception as exc:
            output_label.set_text(f"Error: {exc}")
            ui.notify(f"Test failed: {exc}", type="negative")

    ui.button(
        "Run Test",
        icon="play_arrow",
        on_click=_run_test,
    ).props(THEME.component.BTN_PRIMARY["props"])

    output_label


# ============================================================================
#  Main Render Entry Point
# ============================================================================

@ui.refreshable
def render_operations() -> None:
    """Render the full Operations page.

    Called by the ``/operations`` route via ``app.py``.  Renders four
    sections in an accordion layout:
    1. Template Gallery (built-in + custom)
    2. Execution History (with filters and comparison)
    3. Import / Export
    4. Single-Task Test
    """
    with ui.column().classes("w-full gap-4"):
        # Section 1 — Templates
        with ui.expansion("Templates", value=True).classes("w-full"):
            _render_template_gallery()

        # Section 2 — History
        with ui.expansion("Execution History", value=False).classes("w-full"):
            _render_history()

        # Section 3 — Import / Export
        with ui.expansion("Import / Export", value=False).classes("w-full"):
            _render_import_export()

        # Section 4 — Single-Task Test
        with ui.expansion("Single-Task Test", value=False).classes("w-full"):
            _render_single_task_test()
