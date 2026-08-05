from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from .library import SLIDE_LIBRARY, get_slide

_MAX_CONTENT_FIELD_LENGTH = 300


@dataclass(frozen=True, slots=True)
class SelectedSlide:
    slug: str
    content: dict[str, str]


def _slide_catalog_text() -> str:
    lines = []
    for slide in SLIDE_LIBRARY:
        fields = ", ".join(f"{name} ({description})" for name, description in slide.content_schema)
        lines.append(f"- slug: {slide.slug}\n  what it is: {slide.description}\n  when to use: {slide.when_to_use}\n  content fields to fill: {fields}")
    return "\n".join(lines)


def _selection_prompt(topic: str) -> str:
    return (
        "You are selecting and writing copy for a curated library of pre-built PowerPoint slide "
        "layouts. You do not design any layout — you only pick slides and fill in their content "
        "fields with copy that fits this topic.\n\n"
        f"Presentation topic: {topic}\n\n"
        "Available slides:\n" + _slide_catalog_text() + "\n\n"
        "Respond with ONLY strict JSON, no prose, in exactly this shape:\n"
        '{"slides": [{"slug": "title", "content": {"headline": "...", "subhead": "..."}}, ...]}\n'
        "Rules:\n"
        "- Use every slug from the available slides list above exactly once, in a sensible deck order "
        "(title first, closing call-to-action last).\n"
        "- Fill in every listed content field for each slide with short, concrete copy for this specific "
        "topic — no placeholders.\n"
        "- Do not invent slugs or content fields beyond what was listed."
    )


def _parse_json_object(raw_response: str) -> object:
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("ai_response_not_valid_json") from exc


def select_presentation_slides(topic: str, ai_ask: Callable[[str], str]) -> tuple[SelectedSlide, ...]:
    raw_response = ai_ask(_selection_prompt(topic))
    payload = _parse_json_object(raw_response)
    raw_slides = payload.get("slides") if isinstance(payload, dict) else None
    if not isinstance(raw_slides, list):
        raise ValueError("ai_response_missing_slides_list")

    selected: list[SelectedSlide] = []
    for entry in raw_slides:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str):
            continue
        slide = get_slide(slug)
        if slide is None:
            continue
        raw_content = entry.get("content")
        if not isinstance(raw_content, dict):
            continue
        declared_fields = set(slide.content_fields())
        content = {
            key: str(value).strip()[:_MAX_CONTENT_FIELD_LENGTH]
            for key, value in raw_content.items()
            if key in declared_fields and isinstance(value, str) and str(value).strip()
        }
        selected.append(SelectedSlide(slug=slug, content=content))

    if not selected:
        raise ValueError("ai_returned_no_valid_slides")
    return tuple(selected)
