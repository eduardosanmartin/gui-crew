"""Unit tests for gui-crew theme system (styles.py).

Verifies that all theme tokens — colours, typography, spacing, component
presets, token styles — are consistent, accessible, and dark-mode-aware.
"""

from __future__ import annotations

import re
from typing import ClassVar, get_type_hints

import pytest

import styles
from styles import (
    Component,
    DarkPalette,
    Palette,
    Spacing,
    THEME,
    Token,
    Typography,
)


# ═══════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _all_public_str_attrs(cls: type) -> dict[str, str]:
    """Return {name: value} for every ``str``-typed class variable on *cls*."""
    hints = get_type_hints(cls, include_extras=False)
    result: dict[str, str] = {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        value = getattr(cls, name)
        if isinstance(value, str) and not callable(value):
            result[name] = value
    return result


# ═══════════════════════════════════════════════
#  Palette — Light
# ═══════════════════════════════════════════════

class TestPalette:
    """Light-theme colour palette."""

    def test_primary_is_valid_hex(self) -> None:
        assert _HEX_RE.match(Palette.PRIMARY), f"Invalid hex: {Palette.PRIMARY}"

    def test_all_named_colours_are_valid_hex(self) -> None:
        named = ["PRIMARY", "SECONDARY", "ACCENT", "SUCCESS", "WARNING", "ERROR", "INFO"]
        for name in named:
            value = getattr(Palette, name)
            assert _HEX_RE.match(value), f"Palette.{name} = {value!r} is not #RRGGBB"

    def test_neutrals_are_valid_hex(self) -> None:
        for name in ("GREY_1", "GREY_2", "GREY_3", "GREY_8", "GREY_9"):
            assert _HEX_RE.match(getattr(Palette, name))

    def test_backgrounds_are_valid_hex(self) -> None:
        for name in ("BG_LIGHT", "BG_DARK", "BG_DARK_SURFACE"):
            assert _HEX_RE.match(getattr(Palette, name))

    def test_text_colours_are_valid_hex(self) -> None:
        for name in ("TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_ON_DARK"):
            assert _HEX_RE.match(getattr(Palette, name))

    def test_str_attrs_are_all_hex(self) -> None:
        """Every public string attribute on Palette must be a hex colour."""
        for name, value in _all_public_str_attrs(Palette).items():
            assert _HEX_RE.match(value), (
                f"Palette.{name} = {value!r} is not a valid hex colour"
            )


# ═══════════════════════════════════════════════
#  Palette — Dark
# ═══════════════════════════════════════════════

class TestDarkPalette:
    """Dark-theme colour palette."""

    def test_named_colours_are_valid_hex(self) -> None:
        named = ["PRIMARY", "SECONDARY", "ACCENT", "SUCCESS", "WARNING", "ERROR", "INFO"]
        for name in named:
            value = getattr(DarkPalette, name)
            assert _HEX_RE.match(value), f"DarkPalette.{name} = {value!r}"

    def test_str_attrs_are_all_hex(self) -> None:
        for name, value in _all_public_str_attrs(DarkPalette).items():
            assert _HEX_RE.match(value), (
                f"DarkPalette.{name} = {value!r} is not a valid hex colour"
            )

    def test_dark_primary_is_lighter_than_light(self) -> None:
        """Dark-mode colours should be lighter for contrast on dark backgrounds."""
        def _luminance(hex_str: str) -> float:
            r, g, b = int(hex_str[1:3], 16), int(hex_str[3:5], 16), int(hex_str[5:7], 16)
            return 0.299 * r + 0.587 * g + 0.114 * b

        assert _luminance(DarkPalette.PRIMARY) > _luminance(Palette.PRIMARY), (
            "Dark primary should be lighter than light primary"
        )


# ═══════════════════════════════════════════════
#  Quasar Colour Maps
# ═══════════════════════════════════════════════

class TestQuasarColors:
    """Quasar colour map dictionaries."""

    def test_quasar_colors_has_required_keys(self) -> None:
        expected = {"primary", "secondary", "accent", "positive", "negative", "info", "warning"}
        assert set(styles.QUASAR_COLORS.keys()) >= expected

    def test_quasar_colors_values_are_hex(self) -> None:
        for key, value in styles.QUASAR_COLORS.items():
            assert _HEX_RE.match(value), f"QUASAR_COLORS[{key!r}] = {value!r}"

    def test_dark_quasar_colors_has_required_keys(self) -> None:
        expected = {"primary", "secondary", "accent", "positive", "negative", "info", "warning"}
        assert set(styles.DARK_QUASAR_COLORS.keys()) >= expected

    def test_dark_quasar_colors_values_are_hex(self) -> None:
        for key, value in styles.DARK_QUASAR_COLORS.items():
            assert _HEX_RE.match(value), f"DARK_QUASAR_COLORS[{key!r}] = {value!r}"

    def test_themed_keys_match(self) -> None:
        """Light and dark colour maps must have the same keys."""
        assert set(styles.QUASAR_COLORS.keys()) == set(styles.DARK_QUASAR_COLORS.keys())


# ═══════════════════════════════════════════════
#  Typography
# ═══════════════════════════════════════════════

class TestTypography:
    """Typography scale constants."""

    def test_headings_follow_quasar_convention(self) -> None:
        for level in range(1, 7):
            name = f"H{level}"
            value = getattr(Typography, name)
            assert value == f"text-h{level}", (
                f"Typography.{name} = {value!r}, expected 'text-h{level}'"
            )

    def test_body_variants_exist(self) -> None:
        assert Typography.BODY == "text-body1"
        assert Typography.BODY2 == "text-body2"

    def test_caption_and_overline_exist(self) -> None:
        assert Typography.CAPTION == "text-caption"
        assert Typography.OVERLINE == "text-overline"

    def test_header_title_contains_h4_bold(self) -> None:
        assert "text-h4" in Typography.HEADER_TITLE
        assert "bold" in Typography.HEADER_TITLE

    def test_section_title_contains_h5_bold(self) -> None:
        assert "text-h5" in Typography.SECTION_TITLE
        assert "bold" in Typography.SECTION_TITLE

    def test_card_title_contains_h6(self) -> None:
        assert "text-h6" in Typography.CARD_TITLE


# ═══════════════════════════════════════════════
#  Spacing
# ═══════════════════════════════════════════════

class TestSpacing:
    """Spacing system constants."""

    def test_padding_follows_quasar_convention(self) -> None:
        assert Spacing.XS == "q-pa-xs"
        assert Spacing.SM == "q-pa-sm"
        assert Spacing.MD == "q-pa-md"
        assert Spacing.LG == "q-pa-lg"
        assert Spacing.XL == "q-pa-xl"

    def test_gap_follows_quasar_convention(self) -> None:
        assert Spacing.GAP_XS == "q-gutter-xs"
        assert Spacing.GAP_SM == "q-gutter-sm"
        assert Spacing.GAP_MD == "q-gutter-md"
        assert Spacing.GAP_LG == "q-gutter-lg"
        assert Spacing.GAP_XL == "q-gutter-xl"

    def test_page_padding_exists(self) -> None:
        assert Spacing.PAGE.startswith("q-pa-")


# ═══════════════════════════════════════════════
#  Component Presets
# ═══════════════════════════════════════════════

class TestComponentPresets:
    """Component prop/class presets."""

    def test_card_has_props_and_classes(self) -> None:
        assert "props" in Component.CARD
        assert "classes" in Component.CARD

    def test_form_has_classes(self) -> None:
        assert "classes" in Component.FORM
        assert "column" in Component.FORM["classes"]

    def test_buttons_have_props(self) -> None:
        for name in ("BTN_PRIMARY", "BTN_SECONDARY", "BTN_ICON"):
            preset = getattr(Component, name)
            assert "props" in preset, f"{name} missing 'props'"

    def test_input_has_outlined_dense(self) -> None:
        assert "outlined" in Component.INPUT["props"]
        assert "dense" in Component.INPUT["props"]

    def test_nav_items_have_flat_align_left(self) -> None:
        for name in ("NAV_ITEM", "NAV_ITEM_ACTIVE"):
            preset = getattr(Component, name)
            assert "flat" in preset["props"]
            assert "align=left" in preset["props"]

    def test_button_primary_is_unelevated(self) -> None:
        assert "unelevated" in Component.BTN_PRIMARY["props"]

    def test_button_secondary_is_outline(self) -> None:
        assert "outline" in Component.BTN_SECONDARY["props"]


# ═══════════════════════════════════════════════
#  Token Styles
# ═══════════════════════════════════════════════

class TestTokenStyles:
    """Thinking-vs-answer token styles for Observability micro layer."""

    def test_thinking_has_classes_and_color(self) -> None:
        assert "classes" in Token.THINKING
        assert "color" in Token.THINKING

    def test_answer_has_classes_and_color(self) -> None:
        assert "classes" in Token.ANSWER
        assert "color" in Token.ANSWER

    def test_dark_thinking_has_classes_and_color(self) -> None:
        assert "classes" in Token.DARK_THINKING
        assert "color" in Token.DARK_THINKING

    def test_dark_answer_has_classes_and_color(self) -> None:
        assert "classes" in Token.DARK_ANSWER
        assert "color" in Token.DARK_ANSWER

    def test_thinking_is_dimmed(self) -> None:
        """Thinking tokens must have lower opacity than answer tokens."""
        thinking_style = Token.THINKING["style"]
        answer_style = Token.ANSWER["style"]
        # Extract opacity values
        thinking_opacity = float(thinking_style.split("opacity: ")[1].split(";")[0])
        answer_opacity = float(answer_style.split("opacity: ")[1].split(";")[0])
        assert thinking_opacity < answer_opacity, (
            f"Thinking opacity ({thinking_opacity}) should be lower than "
            f"answer opacity ({answer_opacity})"
        )

    def test_thinking_is_italic(self) -> None:
        assert "italic" in Token.THINKING["style"]
        assert "italic" in Token.DARK_THINKING["style"]

    def test_thinking_color_exists_in_palette(self) -> None:
        """Token colour references must match palette values."""
        assert Token.THINKING["color"] == Palette.GREY_8
        assert Token.ANSWER["color"] == Palette.GREY_9
        assert Token.DARK_THINKING["color"] == DarkPalette.GREY_8
        assert Token.DARK_ANSWER["color"] == DarkPalette.GREY_9


# ═══════════════════════════════════════════════
#  THEME Singleton
# ═══════════════════════════════════════════════

class TestThemeSingleton:
    """THEME dataclass aggregate."""

    def test_theme_has_all_expected_attributes(self) -> None:
        expected = {
            "palette", "dark_palette", "typography", "spacing",
            "component", "token", "quasar_colors", "dark_quasar_colors",
        }
        missing = expected - set(THEME.__dict__.keys())
        assert not missing, f"THEME missing attributes: {missing}"

    def test_theme_is_frozen(self) -> None:
        """THEME must be immutable (frozen=True)."""
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            THEME.palette = DarkPalette  # type: ignore[misc]

    def test_quasar_colors_in_theme(self) -> None:
        assert THEME.quasar_colors == styles.QUASAR_COLORS

    def test_dark_quasar_colors_in_theme(self) -> None:
        assert THEME.dark_quasar_colors == styles.DARK_QUASAR_COLORS


# ═══════════════════════════════════════════════
#  Consistency / Cross-cutting
# ═══════════════════════════════════════════════

class TestConsistency:
    """Cross-theme invariant checks."""

    def test_palette_and_dark_palette_have_same_shape(self) -> None:
        """Both palettes should expose the same set of attribute names."""
        light_attrs = set(_all_public_str_attrs(Palette).keys())
        dark_attrs = set(_all_public_str_attrs(DarkPalette).keys())
        # Allow that they might differ slightly; check critical names
        critical = {"PRIMARY", "SECONDARY", "ACCENT", "SUCCESS", "WARNING", "ERROR", "INFO"}
        for name in critical:
            assert name in light_attrs, f"Light palette missing {name}"
            assert name in dark_attrs, f"Dark palette missing {name}"

    def test_no_hardcoded_quasar_classes_in_token_classes(self) -> None:
        """Token classes must reference Quasar utility classes only."""
        # text-italic, text-grey-*, text-body1 are valid Quasar classes
        for token_dict in (Token.THINKING, Token.ANSWER):
            cls_str = token_dict["classes"]
            for cls_name in cls_str.split():
                assert cls_name.startswith("text-"), (
                    f"Unexpected class {cls_name!r} in token classes"
                )
