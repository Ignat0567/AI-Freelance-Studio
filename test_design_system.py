from __future__ import annotations

import json

import pytest

from design_system import DEFAULT_TOKENS, FONT_LIBRARY, ColorPalette, get_font_pairing, generate_design_tokens, render_tokens_css
from design_system.generate import _build_prompt

pytestmark = pytest.mark.unit


def test_color_palette_accepts_valid_hex():
    palette = ColorPalette(primary="#112233", secondary="#445566", accent="#778899", background="#000000", surface="#ffffff", text="#111111", muted="#999999")
    assert palette.primary == "#112233"


def test_color_palette_rejects_invalid_hex():
    with pytest.raises(ValueError, match="primary"):
        ColorPalette(primary="not-a-color", secondary="#445566", accent="#778899", background="#000000", surface="#ffffff", text="#111111", muted="#999999")


def test_font_library_has_unique_slugs_and_get_font_pairing_works():
    slugs = [pairing.slug for pairing in FONT_LIBRARY]
    assert len(slugs) == len(set(slugs))
    assert get_font_pairing(FONT_LIBRARY[0].slug) is FONT_LIBRARY[0]
    assert get_font_pairing("does-not-exist") is None


def test_default_tokens_are_internally_valid():
    assert DEFAULT_TOKENS.font_pairing in FONT_LIBRARY
    assert DEFAULT_TOKENS.light.background != DEFAULT_TOKENS.dark.background


def test_render_tokens_css_contains_expected_variables_and_font_import():
    css = render_tokens_css(DEFAULT_TOKENS)

    assert f"@import url('{DEFAULT_TOKENS.font_pairing.google_fonts_import_url}')" in css
    assert f"--color-primary: {DEFAULT_TOKENS.light.primary};" in css
    assert f"--color-primary: {DEFAULT_TOKENS.dark.primary};" in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert f"--font-heading: {DEFAULT_TOKENS.font_pairing.heading_family};" in css


def _valid_response(font_slug="editorial-serif"):
    return json.dumps(
        {
            "light": {"primary": "#101010", "secondary": "#202020", "accent": "#e07a3f", "background": "#f8f8f8", "surface": "#ffffff", "text": "#0a0a0a", "muted": "#888888"},
            "dark": {"primary": "#f0f0f0", "secondary": "#d0d0d0", "accent": "#e07a3f", "background": "#0a0a0a", "surface": "#161616", "text": "#f8f8f8", "muted": "#777777"},
            "font_pairing": font_slug,
        }
    )


def test_generate_design_tokens_accepts_a_fully_valid_response():
    tokens = generate_design_tokens("a cozy bakery website", ai_ask=lambda _prompt: _valid_response())

    assert tokens.light.primary == "#101010"
    assert tokens.dark.background == "#0a0a0a"
    assert tokens.font_pairing.slug == "editorial-serif"


def test_generate_design_tokens_falls_back_per_field_on_bad_hex():
    payload = json.loads(_valid_response())
    payload["light"]["accent"] = "not-a-hex-color"
    response = json.dumps(payload)

    tokens = generate_design_tokens("ctx", ai_ask=lambda _prompt: response)

    assert tokens.light.accent == DEFAULT_TOKENS.light.accent
    assert tokens.light.primary == "#101010"  # other valid fields still come through


def test_generate_design_tokens_falls_back_to_default_font_pairing_on_unknown_slug():
    tokens = generate_design_tokens("ctx", ai_ask=lambda _prompt: _valid_response(font_slug="not-a-real-pairing"))
    assert tokens.font_pairing == DEFAULT_TOKENS.font_pairing


def test_generate_design_tokens_never_raises_on_garbage_response():
    tokens = generate_design_tokens("ctx", ai_ask=lambda _prompt: "not json at all")
    assert tokens == DEFAULT_TOKENS


def test_generate_design_tokens_never_raises_when_ai_ask_itself_raises():
    def _boom(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    tokens = generate_design_tokens("ctx", ai_ask=_boom)
    assert tokens == DEFAULT_TOKENS


def test_generate_design_tokens_accepts_fenced_json():
    fenced = f"```json\n{_valid_response()}\n```"
    tokens = generate_design_tokens("ctx", ai_ask=lambda _prompt: fenced)
    assert tokens.light.primary == "#101010"


def test_prompt_lists_every_font_pairing_slug():
    prompt = _build_prompt("some project")
    for pairing in FONT_LIBRARY:
        assert pairing.slug in prompt
