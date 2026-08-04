from __future__ import annotations

from fastapi import APIRouter

from design_system import FONT_LIBRARY
from website_sections import SECTION_LIBRARY

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


def _section_summary(section) -> dict:
    return {
        "slug": section.slug,
        "display_name": section.display_name,
        "description": section.description,
        "when_to_use": section.when_to_use,
        "content_schema": [{"field": name, "description": description} for name, description in section.content_schema],
        "npm_dependencies": list(section.npm_dependencies),
    }


def _font_pairing_summary(pairing) -> dict:
    return {
        "slug": pairing.slug,
        "heading_family": pairing.heading_family,
        "body_family": pairing.body_family,
        "google_fonts_import_url": pairing.google_fonts_import_url,
    }


@router.get("/sections")
def list_sections() -> dict:
    return {"sections": [_section_summary(section) for section in SECTION_LIBRARY]}


@router.get("/font-pairings")
def list_font_pairings() -> dict:
    return {"font_pairings": [_font_pairing_summary(pairing) for pairing in FONT_LIBRARY]}
