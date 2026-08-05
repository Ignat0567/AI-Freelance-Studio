from __future__ import annotations

from pptx.dml.color import RGBColor


def hex_to_rgbcolor(hex_value: str) -> RGBColor:
    return RGBColor.from_string(hex_value.lstrip("#"))


def set_solid_background(slide, hex_value: str) -> None:
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = hex_to_rgbcolor(hex_value)
