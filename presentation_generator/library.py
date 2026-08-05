from __future__ import annotations

from .models import SlideSpec
from .slides import BULLETS_SLIDE, CLOSING_CTA_SLIDE, TITLE_SLIDE, TWO_COLUMN_SLIDE

SLIDE_LIBRARY: tuple[SlideSpec, ...] = (TITLE_SLIDE, BULLETS_SLIDE, TWO_COLUMN_SLIDE, CLOSING_CTA_SLIDE)

_BY_SLUG = {slide.slug: slide for slide in SLIDE_LIBRARY}


def get_slide(slug: str) -> SlideSpec | None:
    return _BY_SLUG.get(slug)
