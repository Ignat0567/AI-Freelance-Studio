from __future__ import annotations

from dataclasses import dataclass
import re

_HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

PALETTE_FIELDS = ("primary", "secondary", "accent", "background", "surface", "text", "muted")


@dataclass(frozen=True, slots=True)
class ColorPalette:
    primary: str
    secondary: str
    accent: str
    background: str
    surface: str
    text: str
    muted: str

    def __post_init__(self) -> None:
        for field_name in PALETTE_FIELDS:
            value = getattr(self, field_name)
            if not _HEX_PATTERN.match(value):
                raise ValueError(f"ColorPalette.{field_name} must be a #rrggbb hex color, got {value!r}")


@dataclass(frozen=True, slots=True)
class FontPairing:
    slug: str
    heading_family: str
    body_family: str
    google_fonts_import_url: str


@dataclass(frozen=True, slots=True)
class DesignTokens:
    light: ColorPalette
    dark: ColorPalette
    font_pairing: FontPairing
