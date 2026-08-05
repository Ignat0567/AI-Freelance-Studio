from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from design_system import ColorPalette, FontPairing

SlideBuilder = Callable[[Any, dict[str, str], ColorPalette, FontPairing], None]


@dataclass(frozen=True, slots=True)
class SlideSpec:
    """A real, hand-authored python-pptx slide layout an AI can select and fill with content.

    `builder(slide, content, palette, font_pairing)` draws real shapes/text onto an
    already-added blank `pptx` slide using the given design tokens for colors/fonts.
    """

    slug: str
    display_name: str
    description: str
    when_to_use: str
    content_schema: tuple[tuple[str, str], ...]
    builder: SlideBuilder

    def content_fields(self) -> tuple[str, ...]:
        return tuple(field_name for field_name, _description in self.content_schema)
