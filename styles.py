"""Theme system for gui-crew — colors, typography, spacing, component styles, and token rendering.

Provides a single source of truth for all visual styling.  UI components import
from here instead of hardcoding Quasar classes or hex colours, which keeps the
look consistent and makes theme changes localised.

Design principles
-----------------
- Minimalist: only include tokens that are actually used.
- Quasar-first: class names and props follow Quasar / Material Design conventions.
- Dark-mode-aware: separate ``PALETTE`` for light and ``DARK_PALETTE`` for dark.
- Token distinction: thinking tokens render italic-dimmed; answer tokens render
  crisp.  This is critical for the Observability micro layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


# ═══════════════════════════════════════════════
#  Colour Palette
# ═══════════════════════════════════════════════

class Palette:
    """Material Design colour palette — light theme."""

    PRIMARY: ClassVar[str] = "#1976D2"
    SECONDARY: ClassVar[str] = "#26A69A"
    ACCENT: ClassVar[str] = "#9C27B0"
    SUCCESS: ClassVar[str] = "#4CAF50"
    WARNING: ClassVar[str] = "#FB8C00"
    ERROR: ClassVar[str] = "#E53935"
    INFO: ClassVar[str] = "#2196F3"

    # Neutrals
    GREY_1: ClassVar[str] = "#F5F5F5"
    GREY_2: ClassVar[str] = "#EEEEEE"
    GREY_3: ClassVar[str] = "#E0E0E0"
    GREY_8: ClassVar[str] = "#424242"
    GREY_9: ClassVar[str] = "#212121"

    # Backgrounds & text
    BG_LIGHT: ClassVar[str] = "#FFFFFF"
    BG_DARK: ClassVar[str] = "#121212"
    BG_DARK_SURFACE: ClassVar[str] = "#1E1E1E"
    TEXT_PRIMARY: ClassVar[str] = "#212121"
    TEXT_SECONDARY: ClassVar[str] = "#757575"
    TEXT_ON_DARK: ClassVar[str] = "#FFFFFF"


# ---------------------------------------------------------------------------
# Dark-mode palette overrides — lighter tones with higher contrast on dark bg.
# ---------------------------------------------------------------------------

class DarkPalette:
    """Material Design colour palette — dark theme."""

    PRIMARY: ClassVar[str] = "#90CAF9"
    SECONDARY: ClassVar[str] = "#80CBC4"
    ACCENT: ClassVar[str] = "#CE93D8"
    SUCCESS: ClassVar[str] = "#81C784"
    WARNING: ClassVar[str] = "#FFB74D"
    ERROR: ClassVar[str] = "#EF9A9A"
    INFO: ClassVar[str] = "#64B5F6"

    # Neutrals
    GREY_1: ClassVar[str] = "#2C2C2C"
    GREY_2: ClassVar[str] = "#383838"
    GREY_3: ClassVar[str] = "#424242"
    GREY_8: ClassVar[str] = "#E0E0E0"
    GREY_9: ClassVar[str] = "#F5F5F5"

    # Backgrounds & text
    BG_LIGHT: ClassVar[str] = "#121212"
    BG_DARK: ClassVar[str] = "#0A0A0A"
    BG_DARK_SURFACE: ClassVar[str] = "#1E1E1E"
    TEXT_PRIMARY: ClassVar[str] = "#F5F5F5"
    TEXT_SECONDARY: ClassVar[str] = "#BDBDBD"
    TEXT_ON_DARK: ClassVar[str] = "#FFFFFF"


# ═══════════════════════════════════════════════
#  Quasar Colour Map
# ═══════════════════════════════════════════════

QUASAR_COLORS: dict[str, str] = {
    "primary": Palette.PRIMARY,
    "secondary": Palette.SECONDARY,
    "accent": Palette.ACCENT,
    "positive": Palette.SUCCESS,
    "negative": Palette.ERROR,
    "info": Palette.INFO,
    "warning": Palette.WARNING,
}

DARK_QUASAR_COLORS: dict[str, str] = {
    "primary": DarkPalette.PRIMARY,
    "secondary": DarkPalette.SECONDARY,
    "accent": DarkPalette.ACCENT,
    "positive": DarkPalette.SUCCESS,
    "negative": DarkPalette.ERROR,
    "info": DarkPalette.INFO,
    "warning": DarkPalette.WARNING,
}


# ═══════════════════════════════════════════════
#  Typography Scale
# ═══════════════════════════════════════════════

class Typography:
    """Quasar typography classes — map to Material Design type scale."""

    H1: ClassVar[str] = "text-h1"
    H2: ClassVar[str] = "text-h2"
    H3: ClassVar[str] = "text-h3"
    H4: ClassVar[str] = "text-h4"
    H5: ClassVar[str] = "text-h5"
    H6: ClassVar[str] = "text-h6"
    BODY: ClassVar[str] = "text-body1"
    BODY2: ClassVar[str] = "text-body2"
    CAPTION: ClassVar[str] = "text-caption"
    OVERLINE: ClassVar[str] = "text-overline"

    # Semantic helpers
    HEADER_TITLE: ClassVar[str] = "text-h4 font-bold"
    SECTION_TITLE: ClassVar[str] = "text-h5 font-bold"
    CARD_TITLE: ClassVar[str] = "text-h6"


# ═══════════════════════════════════════════════
#  Spacing System
# ═══════════════════════════════════════════════

class Spacing:
    """Quasar padding / margin classes — 4 px base grid."""

    XS: ClassVar[str] = "q-pa-xs"    #  4 px
    SM: ClassVar[str] = "q-pa-sm"    #  8 px
    MD: ClassVar[str] = "q-pa-md"    # 16 px
    LG: ClassVar[str] = "q-pa-lg"    # 24 px
    XL: ClassVar[str] = "q-pa-xl"    # 48 px

    # Gap (flex / grid gutters)
    GAP_XS: ClassVar[str] = "q-gutter-xs"
    GAP_SM: ClassVar[str] = "q-gutter-sm"
    GAP_MD: ClassVar[str] = "q-gutter-md"
    GAP_LG: ClassVar[str] = "q-gutter-lg"
    GAP_XL: ClassVar[str] = "q-gutter-xl"

    # Content padding (page-level)
    PAGE: ClassVar[str] = "q-pa-md"


# ═══════════════════════════════════════════════
#  Component Styles — Prop / Class Presets
# ═══════════════════════════════════════════════

class Component:
    """Reusable Quasar prop and class presets for UI components."""

    # Cards
    CARD: ClassVar[dict[str, str]] = {
        "props": "flat bordered",
        "classes": "q-pa-md rounded-borders",
    }

    # Forms
    FORM: ClassVar[dict[str, str]] = {
        "classes": "q-pa-md column",
        "style": "gap: 16px;",
    }

    # Buttons
    BTN_PRIMARY: ClassVar[dict[str, str]] = {
        "props": "unelevated color=primary",
        "classes": "q-mr-sm",
    }
    BTN_SECONDARY: ClassVar[dict[str, str]] = {
        "props": "outline color=secondary",
        "classes": "q-mr-sm",
    }
    BTN_ICON: ClassVar[dict[str, str]] = {
        "props": "flat round",
    }

    # Inputs
    INPUT: ClassVar[dict[str, str]] = {
        "props": "outlined dense",
        "classes": "full-width",
    }

    # Navigation
    NAV_ITEM: ClassVar[dict[str, str]] = {
        "props": "flat align=left",
        "classes": "full-width",
    }
    NAV_ITEM_ACTIVE: ClassVar[dict[str, str]] = {
        "props": "flat align=left color=primary",
        "classes": "full-width",
    }


# ═══════════════════════════════════════════════
#  Token Styles — Observability Micro Layer
# ═══════════════════════════════════════════════

class Token:
    """Visual distinction between *thinking* tokens and *answer* tokens.

    Thinking tokens (internal reasoning, chain-of-thought) render italic +
    dimmed so the user can quickly differentiate them from the final answer.
    Answer tokens render at full opacity in the default body style.
    """

    THINKING: ClassVar[dict[str, str]] = {
        "classes": "text-italic text-grey-6",
        "style": "font-style: italic; opacity: 0.7; padding: 2px 0;",
        "color": Palette.GREY_8,
    }

    ANSWER: ClassVar[dict[str, str]] = {
        "classes": "text-body1",
        "style": "opacity: 1; padding: 2px 0;",
        "color": Palette.GREY_9,
    }

    DARK_THINKING: ClassVar[dict[str, str]] = {
        "classes": "text-italic text-grey-5",
        "style": "font-style: italic; opacity: 0.6; padding: 2px 0;",
        "color": DarkPalette.GREY_8,
    }

    DARK_ANSWER: ClassVar[dict[str, str]] = {
        "classes": "text-body1",
        "style": "opacity: 1; padding: 2px 0;",
        "color": DarkPalette.GREY_9,
    }


# ═══════════════════════════════════════════════
#  Theme Dataclass — Convenience Aggregate
# ═══════════════════════════════════════════════

@dataclass(frozen=True)
class ThemeConfig:
    """Read-only aggregate of all theme tokens.

    Usage::

        from styles import THEME
        ui.label("Hello").classes(THEME.typography.H3)
    """

    palette: type[Palette] = Palette
    dark_palette: type[DarkPalette] = DarkPalette
    typography: type[Typography] = Typography
    spacing: type[Spacing] = Spacing
    component: type[Component] = Component
    token: type[Token] = Token
    quasar_colors: dict[str, str] = field(default_factory=lambda: dict(QUASAR_COLORS))
    dark_quasar_colors: dict[str, str] = field(
        default_factory=lambda: dict(DARK_QUASAR_COLORS),
    )


# Singleton — import this everywhere.
THEME = ThemeConfig()
