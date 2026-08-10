from __future__ import annotations

from .models import SectionSpec
from .sections.closing_cta import CLOSING_CTA
from .sections.hero_webgl import HERO_WEBGL
from .sections.scroll_reveal_section import SCROLL_REVEAL_SECTION

# agent_pipeline_demo.AGENT_PIPELINE_DEMO is deliberately excluded: it showcases
# AI Freelance Studio's own pipeline (Alex/Codex/BugCatcher/...) and was built for
# a Studio-authored showcase site, not for client projects. It stayed importable
# directly from website_sections.sections.agent_pipeline_demo for that future use,
# but must not ship on every client's cinematic site just because their brief
# happened to mention "3d"/"webgl"/"cinematic".
SECTION_LIBRARY: tuple[SectionSpec, ...] = (HERO_WEBGL, SCROLL_REVEAL_SECTION, CLOSING_CTA)


def get_section(slug: str) -> SectionSpec | None:
    for section in SECTION_LIBRARY:
        if section.slug == slug:
            return section
    return None
