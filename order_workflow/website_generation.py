from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re

from design_system import generate_design_tokens, render_tokens_css
from website_sections import SECTION_LIBRARY, get_section, materialize_site

from .execution_plan import ProductionExecutionPackage
from .executors import ExecutionEventSink
from .models import AgentHandoff, ExecutionStage, ProjectBrief
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
_MAX_CUSTOM_SECTIONS = 4
_MAX_CUSTOM_SECTION_BRIEF_LENGTH = 600
_CUSTOM_COMPONENT_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")


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


@dataclass(frozen=True, slots=True)
class CustomSection:
    """A bespoke section proposed for one specific project, not drawn from the curated library.

    Unlike `SelectedSection`, there is no pre-written component behind this — `brief`
    is handed to the coding agent as the spec for a new file it must write from scratch.
    """

    component_name: str
    brief: str


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


def _custom_section_prompt(brief: ProjectBrief, handoff: AgentHandoff) -> str:
    return (
        "This project already has a working WebGL hero section, a scroll-reveal highlights "
        "section, and a closing call-to-action/newsletter section handled separately — do not "
        "propose any of those again. Your only job is to propose the ADDITIONAL, bespoke sections "
        "this specific project needs that those generic sections do not cover (for example: a "
        "product or feature grid, a comparison or specs table, a showcase gallery, pricing tiers, "
        "or whatever this project's actual requirements call for). If the generic sections already "
        "cover everything this project needs, propose zero sections.\n\n"
        f"Project goal: {brief.goal}\n"
        f"Context: {handoff.context_summary}\n"
        "Requirements:\n" + "\n".join(f"- {item}" for item in handoff.requirements) + "\n\n"
        "Respond with ONLY strict JSON, no prose, in exactly this shape:\n"
        '{"sections": [{"component_name": "PopularGamesGrid", "brief": "..."}]}\n'
        "Rules:\n"
        f"- Propose between 0 and {_MAX_CUSTOM_SECTIONS} sections, ordered as they should appear on the page.\n"
        "- component_name must be a PascalCase, valid JavaScript identifier (e.g. PopularGamesGrid), unique across the list.\n"
        "- brief must be a concrete, specific paragraph describing exactly what content, layout, and any "
        "interaction this section needs, grounded in this project's actual requirements above — no vague filler."
    )


def propose_custom_sections(brief: ProjectBrief, handoff: AgentHandoff, ai_ask: Callable[[str], str]) -> tuple[CustomSection, ...]:
    """Propose bespoke, non-curated sections for this project's specific requirements.

    This call is intentionally allowed to fail soft: an unavailable/garbage AI response
    degrades to zero custom sections (the site still ships with the three reliable curated
    sections) rather than failing the whole build the way `select_website_sections` does --
    the curated sections are the guaranteed-working core, this is a best-effort addition.
    """
    try:
        raw_response = ai_ask(_custom_section_prompt(brief, handoff))
        payload = _parse_json_object(raw_response)
    except Exception:
        return ()
    raw_sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(raw_sections, list):
        return ()

    proposed: list[CustomSection] = []
    seen_names: set[str] = set()
    for entry in raw_sections:
        if not isinstance(entry, dict):
            continue
        name = entry.get("component_name")
        section_brief = entry.get("brief")
        if not isinstance(name, str) or not isinstance(section_brief, str):
            continue
        name = name.strip()
        section_brief = section_brief.strip()
        if not section_brief or not _CUSTOM_COMPONENT_NAME.fullmatch(name) or name in seen_names:
            continue
        seen_names.add(name)
        proposed.append(CustomSection(component_name=name, brief=section_brief[:_MAX_CUSTOM_SECTION_BRIEF_LENGTH]))
        if len(proposed) >= _MAX_CUSTOM_SECTIONS:
            break
    return tuple(proposed)


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


def _build_website_prompt(
    brief: ProjectBrief,
    handoff: AgentHandoff,
    selected: tuple[SelectedSection, ...],
    qa_commands: tuple[str, ...],
    custom_sections: tuple[CustomSection, ...] = (),
) -> str:
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
    ]
    if custom_sections:
        lines += [
            "In addition, write NEW section components tailored to this specific project's "
            "requirements (the curated sections above are generic and do not cover this content). "
            "Create each one, in this order, between the ScrollRevealSection and ClosingCTA sections:",
            *[
                f"- {cs.component_name} (new file: frontend/src/sections/{cs.component_name}.jsx): {cs.brief}"
                for cs in custom_sections
            ],
            "",
            "Rules for these new sections:",
            "- Plain React + CSS only (a matching frontend/src/sections/<Name>.css per component). Do not use "
            "Three.js, GSAP, or any package not already declared in frontend/package.json.",
            "- Match the existing dark, token-driven visual style: use the CSS custom properties already "
            "defined in frontend/src/tokens.css (colors, fonts) rather than hardcoding new ones.",
            "- Do not use real third-party trademarked artwork, logos, or copyrighted screenshots for any "
            "images this section needs -- use original CSS/SVG graphics, gradients, or abstract placeholder art.",
            "- Edit frontend/src/App.jsx to import each new component and render it in the exact order given "
            "above, positioned between <ScrollRevealSection /> and <ClosingCTA />.",
            "",
        ]
    lines += [
        "Your job:",
        "- Run npm install and npm run build inside frontend/ and fix any build errors.",
        "- Do NOT rewrite, simplify, or remove the existing animation, WebGL, or GSAP code in the curated section files listed above.",
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
    event_sink: ExecutionEventSink | None = None,
) -> ProductionExecutionPackage:
    selected = select_website_sections(brief, handoff, ai_ask)
    # Separate, narrow AI call from section selection — one AI call, one job — so
    # each can be validated and tested independently. A broken/garbage response
    # here never fails the build: generate_design_tokens falls back per-field,
    # and propose_custom_sections degrades to zero bespoke sections.
    custom_sections = propose_custom_sections(brief, handoff, ai_ask)
    if custom_sections:
        names = ", ".join(cs.component_name for cs in custom_sections)
        if event_sink is not None:
            event_sink.emit(stage=ExecutionStage.PLANNING, agent="Elena", progress=27, message=f"Drafting custom sections: {names}")
    elif event_sink is not None:
        event_sink.emit(
            stage=ExecutionStage.PLANNING,
            agent="Elena",
            progress=27,
            message="No project-specific sections proposed; shipping the curated sections only.",
        )
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
        prompt=_build_website_prompt(brief, handoff, selected, qa_commands, custom_sections),
    )
