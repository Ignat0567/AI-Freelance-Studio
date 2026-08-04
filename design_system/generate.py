from __future__ import annotations

from collections.abc import Callable
import json
import re

from .defaults import DEFAULT_TOKENS
from .fonts import FONT_LIBRARY, get_font_pairing
from .models import PALETTE_FIELDS, ColorPalette, DesignTokens

_HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _catalog_text() -> str:
    return "\n".join(f"- {pairing.slug}: heading {pairing.heading_family}, body {pairing.body_family}" for pairing in FONT_LIBRARY)


def _build_prompt(context_text: str) -> str:
    return (
        "You are choosing a color palette and font pairing for a website, based on the project "
        "description below. Respond with ONLY strict JSON, no prose, in exactly this shape:\n"
        '{"light": {"primary": "#rrggbb", "secondary": "#rrggbb", "accent": "#rrggbb", '
        '"background": "#rrggbb", "surface": "#rrggbb", "text": "#rrggbb", "muted": "#rrggbb"}, '
        '"dark": {"primary": "#rrggbb", "secondary": "#rrggbb", "accent": "#rrggbb", '
        '"background": "#rrggbb", "surface": "#rrggbb", "text": "#rrggbb", "muted": "#rrggbb"}, '
        '"font_pairing": "<slug>"}\n\n'
        f"Project description:\n{context_text}\n\n"
        "Available font pairings (choose exactly one slug):\n" + _catalog_text() + "\n\n"
        "Rules:\n"
        "- Every color must be a 6-digit hex code starting with #.\n"
        "- The dark palette must be a genuine dark theme (dark background, light text), not a copy of the light one.\n"
        "- Choose colors that fit the project's tone; do not just default to generic blue."
    )


def _palette_from_dict(raw: object, fallback: ColorPalette) -> ColorPalette:
    if not isinstance(raw, dict):
        return fallback
    fields = {}
    for field_name in PALETTE_FIELDS:
        value = raw.get(field_name)
        fields[field_name] = value if isinstance(value, str) and _HEX_PATTERN.match(value) else getattr(fallback, field_name)
    return ColorPalette(**fields)


def generate_design_tokens(context_text: str, ai_ask: Callable[[str], str]) -> DesignTokens:
    """One narrow AI call for a palette + font pairing.

    Deliberately never raises: unlike section content, a broken color call has a
    safe per-field default to fall back to, so a bad AI response should degrade
    the palette, not fail the whole site build.
    """
    try:
        raw_response = ai_ask(_build_prompt(context_text))
        text = raw_response.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        payload = json.loads(text)
    except Exception:
        return DEFAULT_TOKENS

    if not isinstance(payload, dict):
        return DEFAULT_TOKENS

    light = _palette_from_dict(payload.get("light"), DEFAULT_TOKENS.light)
    dark = _palette_from_dict(payload.get("dark"), DEFAULT_TOKENS.dark)
    font_slug = payload.get("font_pairing")
    font_pairing = get_font_pairing(font_slug) if isinstance(font_slug, str) else None
    if font_pairing is None:
        font_pairing = DEFAULT_TOKENS.font_pairing

    return DesignTokens(light=light, dark=dark, font_pairing=font_pairing)
