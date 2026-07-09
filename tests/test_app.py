"""Unit tests for gui-crew app shell — routing, layout, and session state.

Tests cover:
- Session default structure and initialisation.
- Theme toggle logic (light ↔ dark flip).
- Navigation item definitions.
- Route registration (all expected paths present).

Storage-dependent tests mock ``Storage.user`` at the class level to avoid
requiring a full NiceGUI server context.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from app import (
    DEFAULT_USER_STATE,
    NAV_ITEMS,
    init_session_defaults,
    toggle_theme,
)
from nicegui import app
from nicegui.storage import Storage


# ═══════════════════════════════════════════════
#  Session Defaults (no storage needed)
# ═══════════════════════════════════════════════

class TestDefaultUserState:
    """``DEFAULT_USER_STATE`` structure — pure data, no runtime deps."""

    REQUIRED_KEYS = {
        "crew_model",
        "mode",
        "ui_prefs",
        "wizard_state",
        "history",
        "templates_custom",
        "crewai_version",
    }

    def test_has_all_required_keys(self) -> None:
        assert set(DEFAULT_USER_STATE.keys()) >= self.REQUIRED_KEYS

    def test_crew_model_is_none_initially(self) -> None:
        assert DEFAULT_USER_STATE["crew_model"] is None

    def test_mode_is_builder(self) -> None:
        assert DEFAULT_USER_STATE["mode"] == "builder"

    def test_ui_prefs_has_theme_light(self) -> None:
        prefs = DEFAULT_USER_STATE["ui_prefs"]
        assert isinstance(prefs, dict)
        assert prefs["theme"] == "light"

    def test_ui_prefs_has_advanced_false(self) -> None:
        prefs = DEFAULT_USER_STATE["ui_prefs"]
        assert prefs["advanced"] is False

    def test_wizard_state_starts_at_zero(self) -> None:
        ws = DEFAULT_USER_STATE["wizard_state"]
        assert isinstance(ws, dict)
        assert ws["step"] == 0
        assert ws["data"] == {}

    def test_history_is_empty_list(self) -> None:
        assert DEFAULT_USER_STATE["history"] == []

    def test_templates_custom_is_empty_dict(self) -> None:
        assert DEFAULT_USER_STATE["templates_custom"] == {}

    def test_crewai_version_is_empty_string(self) -> None:
        assert DEFAULT_USER_STATE["crewai_version"] == ""


# ═══════════════════════════════════════════════
#  Shared helper — build a mock that looks like ``app.storage.user``
# ═══════════════════════════════════════════════

class _FakeStorage(dict):
    """A dict subclass that behaves like NiceGUI's ``app.storage.user``.

    Wraps a real ``dict`` so that ``__contains__``, ``__getitem__``,
    ``__setitem__``, ``keys``, and ``get`` all work correctly without
    requiring the NiceGUI request context.
    """


def _make_mock_user(initial: dict[str, object] | None = None) -> _FakeStorage:
    """Build a fake storage dict pre-populated with *initial* values."""
    return _FakeStorage(initial) if initial else _FakeStorage()


# ═══════════════════════════════════════════════
#  Session Initialisation (storage mocked)
# ═══════════════════════════════════════════════

class TestInitSessionDefaults:
    """``init_session_defaults()`` behaviour — mock ``Storage.user`` property."""

    def test_populates_all_keys_when_empty(self) -> None:
        mock_user = _make_mock_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=mock_user
        ):
            init_session_defaults()
        for key in DEFAULT_USER_STATE:
            assert key in mock_user, f"Missing key: {key}"

    def test_does_not_overwrite_existing_keys(self) -> None:
        mock_user = _make_mock_user({
            "mode": "canvas",
            "crew_model": {"name": "TestCrew"},
        })
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=mock_user
        ):
            init_session_defaults()

        assert mock_user["mode"] == "canvas", (
            "Existing key 'mode' was overwritten"
        )
        assert mock_user["crew_model"] == {"name": "TestCrew"}, (
            "Existing key 'crew_model' was overwritten"
        )
        assert "history" in mock_user

    def test_idempotent(self) -> None:
        mock_user = _make_mock_user()
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=mock_user
        ):
            init_session_defaults()
        snapshot = dict(mock_user)
        with patch.object(
            Storage, "user", new_callable=PropertyMock, return_value=mock_user
        ):
            init_session_defaults()
        assert mock_user == snapshot


# ═══════════════════════════════════════════════
#  Theme Toggle (storage + dark_mode mocked)
# ═══════════════════════════════════════════════

class TestThemeToggle:
    """``toggle_theme()`` logic — mock ``Storage.user`` and ``ui.dark_mode()``."""

    def test_flips_light_to_dark(self) -> None:
        mock_user = _make_mock_user({
            "ui_prefs": {"theme": "light", "advanced": False},
        })
        mock_dark = MagicMock()
        with (
            patch.object(
                Storage, "user", new_callable=PropertyMock, return_value=mock_user
            ),
            patch("app.ui.dark_mode", return_value=mock_dark),
        ):
            toggle_theme()

        assert mock_user["ui_prefs"]["theme"] == "dark"
        assert mock_dark.value is True

    def test_flips_dark_to_light(self) -> None:
        mock_user = _make_mock_user({
            "ui_prefs": {"theme": "dark", "advanced": False},
        })
        mock_dark = MagicMock()
        with (
            patch.object(
                Storage, "user", new_callable=PropertyMock, return_value=mock_user
            ),
            patch("app.ui.dark_mode", return_value=mock_dark),
        ):
            toggle_theme()

        assert mock_user["ui_prefs"]["theme"] == "light"
        assert mock_dark.value is False

    def test_preserves_other_prefs(self) -> None:
        mock_user = _make_mock_user({
            "ui_prefs": {"theme": "light", "advanced": True},
        })
        mock_dark = MagicMock()
        with (
            patch.object(
                Storage, "user", new_callable=PropertyMock, return_value=mock_user
            ),
            patch("app.ui.dark_mode", return_value=mock_dark),
        ):
            toggle_theme()

        assert mock_user["ui_prefs"]["advanced"] is True

    def test_defaults_to_light_when_no_prefs(self) -> None:
        """When ui_prefs doesn't exist, treat as light and flip to dark."""
        mock_user = _make_mock_user()
        mock_dark = MagicMock()
        with (
            patch.object(
                Storage, "user", new_callable=PropertyMock, return_value=mock_user
            ),
            patch("app.ui.dark_mode", return_value=mock_dark),
        ):
            init_session_defaults()
            toggle_theme()

        assert mock_user["ui_prefs"]["theme"] == "dark"


# ═══════════════════════════════════════════════
#  Navigation Items (no storage needed)
# ═══════════════════════════════════════════════

class TestNavItems:
    """``NAV_ITEMS`` definition."""

    def test_all_four_views_present(self) -> None:
        labels = {item[0] for item in NAV_ITEMS}
        assert labels >= {"Builder", "Canvas", "Observability", "Operations"}

    def test_all_routes_are_slash_prefixed(self) -> None:
        for _, path, _ in NAV_ITEMS:
            assert path.startswith("/"), f"Route {path!r} must start with '/'"

    def test_all_have_icon(self) -> None:
        for _, _, icon in NAV_ITEMS:
            assert isinstance(icon, str)
            assert len(icon) > 0

    def test_builder_is_first(self) -> None:
        assert NAV_ITEMS[0][0] == "Builder"


# ═══════════════════════════════════════════════
#  Route Registration (no storage needed)
# ═══════════════════════════════════════════════

class TestRoutes:
    """All expected ``@ui.page`` routes are registered."""

    EXPECTED_PATHS = {"/", "/builder", "/canvas", "/observability", "/operations"}

    def _registered_paths(self) -> set[str]:
        """Return the set of registered NiceGUI route paths.

        In NiceGUI 3.x, ``app.routes`` is a list of ``Route`` objects
        or tuples with a ``.path`` attribute.
        """
        paths: set[str] = set()
        for route in app.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
            elif isinstance(route, (tuple, list)) and len(route) >= 1:
                paths.add(str(route[0]))
        return paths

    def test_all_expected_routes_registered(self) -> None:
        registered = self._registered_paths()
        missing = self.EXPECTED_PATHS - registered
        assert not missing, (
            f"Missing routes: {missing}. Registered: {registered}"
        )

    def test_builder_route_registered(self) -> None:
        assert "/builder" in self._registered_paths()

    def test_root_route_registered(self) -> None:
        assert "/" in self._registered_paths()


# ═══════════════════════════════════════════════
#  Import Sanity (no storage needed)
# ═══════════════════════════════════════════════

class TestImports:
    """Module-level sanity — app imports without side-effects."""

    def test_app_imports_styles(self) -> None:
        """app.py must import THEME from styles.py."""
        import app as app_module
        assert hasattr(app_module, "THEME"), "app.py should import THEME"

    def test_styles_theme_is_importable(self) -> None:
        from styles import THEME as styles_theme
        assert styles_theme is not None

    def test_app_has_page_handlers(self) -> None:
        """All route handler functions must be importable."""
        import app as app_module
        for name in ("index", "builder", "canvas", "observability", "operations"):
            assert hasattr(app_module, name), f"Missing handler: {name}"
            fn = getattr(app_module, name)
            assert callable(fn), f"{name} is not callable"
