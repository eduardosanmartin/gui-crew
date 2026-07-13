"""gui-crew application entry point — routing, layout, and session state.

Provides the NiceGUI shell that all feature modules plug into.  The shell owns:
- Route definitions  (``/``, ``/builder``, ``/canvas``, ``/observability``, ``/operations``)
- Main layout        (header with nav drawer + content area)
- Session state       (``app.storage.user`` defaults)
- Theme toggle        (light ↔ dark mode)

Feature modules (``builder.py``, ``canvas.py``, etc.) supply the content
callables — for now they render placeholders.
"""

from __future__ import annotations

import os
from typing import Callable

from nicegui import app, ui
from nicegui.event import Event

import observability
from crew_engine import ProtocolEvent
from styles import THEME

# -- Event bus bridge --------------------------------------------------------
# Single module-level bus that crew_engine callbacks can push into and
# observability panels subscribe to.  Wired here so app.py owns the lifecycle.

crew_event_bus: Event[ProtocolEvent] = Event()  # type: ignore[valid-type]
observability.crew_event_bus = crew_event_bus

# ═══════════════════════════════════════════════
#  Session Defaults
# ═══════════════════════════════════════════════

DEFAULT_USER_STATE: dict[str, object] = {
    "crew_model": None,
    "mode": "builder",
    "ui_prefs": {"theme": "light", "advanced": False},
    "wizard_state": {"step": 0, "data": {}},
    "history": [],
    "templates_custom": {},
    "crewai_version": "",
}


def init_session_defaults() -> None:
    """Ensure every key in ``DEFAULT_USER_STATE`` exists in the user session.

    Called at the start of every page render so that even first-time visitors
    get a valid session shape.  Existing keys are never overwritten.
    """
    for key, default_value in DEFAULT_USER_STATE.items():
        if key not in app.storage.user:
            app.storage.user[key] = default_value


# ═══════════════════════════════════════════════
#  Theme Toggle
# ═══════════════════════════════════════════════

def toggle_theme() -> None:
    """Flip between light and dark mode, persisting the preference."""
    prefs: dict = app.storage.user.get("ui_prefs", {})  # type: ignore[assignment]
    current: str = prefs.get("theme", "light")
    new_theme: str = "dark" if current == "light" else "light"
    prefs["theme"] = new_theme
    app.storage.user["ui_prefs"] = prefs
    ui.dark_mode().value = (new_theme == "dark")


def _theme_icon() -> str:
    """Return the appropriate Material icon name for the current theme state."""
    prefs: dict = app.storage.user.get("ui_prefs", {})  # type: ignore[assignment]
    return "dark_mode" if prefs.get("theme", "light") == "light" else "light_mode"


# ═══════════════════════════════════════════════
#  Layout Helpers
# ═══════════════════════════════════════════════

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("Builder", "/builder", "build"),
    ("Canvas", "/canvas", "account_tree"),
    ("Observability", "/observability", "visibility"),
    ("Operations", "/operations", "settings"),
]


def _render_header() -> None:
    """Render the top app bar with title and theme toggle."""
    with ui.header(elevated=True).classes("bg-primary text-white"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("GUI-CREW").classes(THEME.typography.HEADER_TITLE)
            ui.button(
                icon=_theme_icon(),
                on_click=toggle_theme,
            ).props(THEME.component.BTN_ICON["props"])


def _render_nav_drawer() -> None:
    """Render the left navigation drawer with links to all views."""
    with ui.left_drawer().classes("bg-grey-1"):
        with ui.column().classes("gap-1 p-2"):
            for label, path, icon in NAV_ITEMS:
                ui.button(
                    label,
                    icon=icon,
                    on_click=lambda p=path: ui.navigate.to(p),
                ).props(THEME.component.NAV_ITEM["props"])


def render_page(title: str, content_fn: Callable[[], None]) -> None:
    """Shared page shell: session init → header → drawer → content area.

    Every ``@ui.page`` handler delegates here so that the layout stays DRY.
    """
    init_session_defaults()
    _render_header()
    _render_nav_drawer()

    with ui.column().classes(THEME.spacing.PAGE):
        ui.label(title).classes(THEME.typography.SECTION_TITLE)
        content_fn()


# ═══════════════════════════════════════════════
#  Placeholder Content Callables
# ═══════════════════════════════════════════════

def _render_builder_placeholder() -> None:
    ui.label("Crew configuration forms will appear here.").classes(
        THEME.typography.BODY
    )


def _render_canvas_placeholder() -> None:
    ui.label("DAG canvas editor will appear here.").classes(
        THEME.typography.BODY
    )


def _render_observability() -> None:
    """Render the observability dashboard via the dedicated module."""
    # Determine active crew_id from session or event bus state.
    # For now, pass None (empty state) — future PRs will wire active crew
    # selection from the event bus subscription.
    observability.render_observability(crew_id=None)


def _render_operations_placeholder() -> None:
    ui.label("Playground, templates, history will appear here.").classes(
        THEME.typography.BODY
    )


# ═══════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════

@ui.page("/")
def index() -> None:
    """Root redirect — send visitors straight to the Builder."""
    ui.navigate.to("/builder")


@ui.page("/builder")
def builder() -> None:
    """Builder view — crew, agent, and task configuration forms."""
    render_page("Builder", _render_builder_placeholder)


@ui.page("/canvas")
def canvas_page() -> None:
    """Canvas view — DAG editor for visual crew topology."""
    import canvas as _canvas

    render_page("Canvas", _canvas.render_canvas)


@ui.page("/observability")
def observability() -> None:
    """Observability view — real-time execution dashboard."""
    render_page("Observability", _render_observability)


@ui.page("/operations")
def operations() -> None:
    """Operations view — playground, templates, history, import/export."""
    render_page("Operations", _render_operations_placeholder)


# ═══════════════════════════════════════════════
#  Server Entry Point
# ═══════════════════════════════════════════════

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="GUI-CREW",
        storage_secret=os.environ.get(
            "STORAGE_SECRET", "gui-crew-dev-secret-change-me"
        ),
    )
