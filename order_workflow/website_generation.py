from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from design_system import generate_design_tokens, render_tokens_css
from website_sections import SECTION_LIBRARY, get_section, materialize_site

from .execution_plan import ProductionExecutionPackage
from .models import AgentHandoff, ProjectBrief
from .workspace import ProjectWorkspace

_CINEMATIC_KEYWORDS = (
    "cinematic",
    "showcase",
    "webgl",
    "three.js",
    "threejs",
    "3d",
    "immersive",
    "gsap",
    "scroll storytelling",
    "scroll-storytelling",
    "animated landing",
    "digital agency",
)

_MAX_CONTENT_FIELD_LENGTH = 300


def detect_cinematic_website_intent(brief: ProjectBrief) -> bool:
    haystack_parts = [brief.goal, brief.recommended_stack.frontend]
    if brief.elena_design_concept is not None:
        haystack_parts.append(brief.elena_design_concept.visual_direction)
    haystack = " ".join(haystack_parts).lower()
    return any(keyword in haystack for keyword in _CINEMATIC_KEYWORDS)


@dataclass(frozen=True, slots=True)
class SelectedSection:
    slug: str
    content: dict[str, str]


def _section_catalog_text() -> str:
    lines = []
    for section in SECTION_LIBRARY:
        fields = ", ".join(f"{name} ({description})" for name, description in section.content_schema)
        lines.append(f"- slug: {section.slug}\n  what it is: {section.description}\n  when to use: {section.when_to_use}\n  content fields to fill: {fields}")
    return "\n".join(lines)


def _selection_prompt(brief: ProjectBrief, handoff: AgentHandoff) -> str:
    return (
        "You are selecting and writing copy for a curated library of pre-built, working "
        "React/Three.js/GSAP website sections. You do not write any code — you only pick "
        "sections and fill in their content fields with copy that fits this project.\n\n"
        f"Project goal: {brief.goal}\n"
        f"Context: {handoff.context_summary}\n"
        "Requirements:\n" + "\n".join(f"- {item}" for item in handoff.requirements) + "\n\n"
        "Available sections:\n" + _section_catalog_text() + "\n\n"
        "Respond with ONLY strict JSON, no prose, in exactly this shape:\n"
        '{"sections": [{"slug": "hero_webgl", "content": {"headline": "...", "subhead": "...", "cta_label": "..."}}, ...]}\n'
        "Rules:\n"
        "- Use every slug from the available sections list above exactly once, in a sensible page order (hero first, closing call-to-action last).\n"
        "- Fill in every listed content field for each section with short, concrete copy for this specific project — no placeholders.\n"
        "- Do not invent slugs or content fields beyond what was listed."
    )


def select_website_sections(brief: ProjectBrief, handoff: AgentHandoff, ai_ask: Callable[[str], str]) -> tuple[SelectedSection, ...]:
    raw_response = ai_ask(_selection_prompt(brief, handoff))
    payload = _parse_json_object(raw_response)
    raw_sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(raw_sections, list):
        raise ValueError("ai_response_missing_sections_list")

    selected: list[SelectedSection] = []
    for entry in raw_sections:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str):
            continue
        section = get_section(slug)
        if section is None:
            continue
        raw_content = entry.get("content")
        if not isinstance(raw_content, dict):
            continue
        declared_fields = set(section.content_fields())
        content = {
            key: str(value).strip()[:_MAX_CONTENT_FIELD_LENGTH]
            for key, value in raw_content.items()
            if key in declared_fields and isinstance(value, str) and str(value).strip()
        }
        selected.append(SelectedSection(slug=slug, content=content))

    if not selected:
        raise ValueError("ai_returned_no_valid_sections")
    return tuple(selected)


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


def _build_website_prompt(brief: ProjectBrief, handoff: AgentHandoff, selected: tuple[SelectedSection, ...], qa_commands: tuple[str, ...]) -> str:
    section_lines = []
    for item in selected:
        section = get_section(item.slug)
        if section is not None:
            section_lines.append(f"- {section.display_name} (frontend/src/{section.entry_relative_path}.jsx)")
    lines = [
        "Wire together a pre-built cinematic website. The sections listed below are already "
        "implemented, working React/Three.js/GSAP components with their copy already filled in, "
        "and are already imported and rendered in order by frontend/src/App.jsx.",
        "",
        "Sections already materialized, in render order:",
        *section_lines,
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "Acceptance criteria:",
        *[f"- {item}" for item in handoff.acceptance_criteria],
        "",
        "QA commands:",
        *[f"- {item}" for item in qa_commands],
        "",
        "Your job is ONLY to make the project build and run:",
        "- Run npm install and npm run build inside frontend/ and fix any build errors.",
        "- Do NOT rewrite, simplify, or remove the existing animation, WebGL, or GSAP code in the section files.",
        "- Do NOT add new npm dependencies beyond what is already declared in frontend/package.json.",
        "- Do not expose secrets in logs, reports, or generated files.",
        "After the project builds successfully, stop and exit. Do not keep rewriting files.",
    ]
    return "\n".join(lines)


def build_website_execution_plan(
    *,
    execution_id: str,
    brief: ProjectBrief,
    handoff: AgentHandoff,
    workspace: ProjectWorkspace,
    provider_name: str,
    model_name: str,
    qa_commands: tuple[str, ...],
    ai_ask: Callable[[str], str],
) -> ProductionExecutionPackage:
    selected = select_website_sections(brief, handoff, ai_ask)
    # Separate, narrow AI call from section selection — one AI call, one job — so
    # each can be validated and tested independently. A broken/garbage response
    # here never fails the build: generate_design_tokens falls back per-field.
    tokens = generate_design_tokens(f"{brief.goal}\n\n{handoff.context_summary}", ai_ask)
    materialize_site(
        [(item.slug, item.content) for item in selected],
        workspace.project_path,
        project_name=workspace.project_reference,
        project_title=brief.goal[:80] or "AI Freelance Studio",
        tokens_css=render_tokens_css(tokens),
    )
    return ProductionExecutionPackage(
        execution_id=execution_id,
        order_id=brief.order_id,
        brief_id=brief.id,
        handoff_id=handoff.id,
        provider_name=provider_name,
        model_name=model_name,
        workspace_root=workspace.root_reference,
        project_path=workspace.project_reference,
        qa_commands=qa_commands,
        prompt=_build_website_prompt(brief, handoff, selected, qa_commands),
    )
