from __future__ import annotations

from .models import SectionSpec
from .sections.agent_pipeline_demo import AGENT_PIPELINE_DEMO
from .sections.closing_cta import CLOSING_CTA
from .sections.hero_webgl import HERO_WEBGL
from .sections.scroll_reveal_section import SCROLL_REVEAL_SECTION

SECTION_LIBRARY: tuple[SectionSpec, ...] = (HERO_WEBGL, SCROLL_REVEAL_SECTION, AGENT_PIPELINE_DEMO, CLOSING_CTA)


def get_section(slug: str) -> SectionSpec | None:
    for section in SECTION_LIBRARY:
        if section.slug == slug:
            return section
    return None
