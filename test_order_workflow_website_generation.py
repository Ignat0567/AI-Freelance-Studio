from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from order_workflow.models import AgentHandoff, ElenaDesignChoice, EventKind, EventLevel, ProjectBrief
from order_workflow.website_generation import (
    build_website_execution_plan,
    detect_cinematic_website_intent,
    propose_custom_sections,
    select_website_sections,
)
from order_workflow.workspace import ProjectWorkspace
from website_sections import SECTION_LIBRARY

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _brief(goal: str, **overrides) -> ProjectBrief:
    fields = dict(
        id="brief_test",
        order_id="order_test",
        goal=goal,
        target_users=("Visitors",),
        core_features=("Core feature",),
        acceptance_criteria=("It works",),
        elena_design_choice=ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT,
        created_at=NOW,
        updated_at=NOW,
    )
    fields.update(overrides)
    return ProjectBrief(**fields)


def _handoff(brief: ProjectBrief, **overrides) -> AgentHandoff:
    fields = dict(
        id="handoff_test",
        order_id=brief.order_id,
        brief_id=brief.id,
        source_agent="Alex",
        target_agent="Codex",
        goal=brief.goal,
        context_summary="Context for the handoff.",
        requirements=("Do the thing",),
        acceptance_criteria=("It works",),
        created_at=NOW,
    )
    fields.update(overrides)
    return AgentHandoff(**fields)


def _full_ai_response() -> str:
    return json.dumps(
        {
            "sections": [
                {
                    "slug": section.slug,
                    "content": {field_name: f"copy for {field_name}" for field_name, _description in section.content_schema},
                }
                for section in SECTION_LIBRARY
            ]
        }
    )


def test_detect_cinematic_website_intent_positive():
    brief = _brief("Build a cinematic WebGL showcase website with scroll storytelling for a design agency.")
    assert detect_cinematic_website_intent(brief) is True


def test_detect_cinematic_website_intent_negative():
    brief = _brief("A CRM for managing clients, leads, pipeline and deals with task activity tracking.")
    assert detect_cinematic_website_intent(brief) is False


def test_select_website_sections_returns_all_valid_sections():
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)

    selected = select_website_sections(brief, handoff, ai_ask=lambda _prompt: _full_ai_response())

    assert {item.slug for item in selected} == {section.slug for section in SECTION_LIBRARY}
    for item in selected:
        for field_name, _description in next(s for s in SECTION_LIBRARY if s.slug == item.slug).content_schema:
            assert item.content[field_name] == f"copy for {field_name}"


def test_select_website_sections_drops_unknown_slug_and_undeclared_field():
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)
    known_slug = SECTION_LIBRARY[0].slug
    known_fields = {field_name: f"copy for {field_name}" for field_name, _description in SECTION_LIBRARY[0].content_schema}
    response = json.dumps(
        {
            "sections": [
                {"slug": "totally_made_up_section", "content": {"x": "y"}},
                {"slug": known_slug, "content": {**known_fields, "not_a_real_field": "dropped"}},
            ]
        }
    )

    selected = select_website_sections(brief, handoff, ai_ask=lambda _prompt: response)

    assert [item.slug for item in selected] == [known_slug]
    assert "not_a_real_field" not in selected[0].content


def test_select_website_sections_rejects_invalid_json():
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)

    with pytest.raises(ValueError, match="ai_response_not_valid_json"):
        select_website_sections(brief, handoff, ai_ask=lambda _prompt: "not json at all")


def test_select_website_sections_rejects_when_nothing_survives_validation():
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)
    response = json.dumps({"sections": [{"slug": "does_not_exist", "content": {}}]})

    with pytest.raises(ValueError, match="ai_returned_no_valid_sections"):
        select_website_sections(brief, handoff, ai_ask=lambda _prompt: response)


def test_select_website_sections_accepts_fenced_json():
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)
    fenced = f"```json\n{_full_ai_response()}\n```"

    selected = select_website_sections(brief, handoff, ai_ask=lambda _prompt: fenced)

    assert len(selected) == len(SECTION_LIBRARY)


def test_build_website_execution_plan_materializes_files_and_builds_narrow_prompt(tmp_path):
    brief = _brief("Cinematic WebGL showcase site for a design studio.")
    handoff = _handoff(brief)
    workspace = ProjectWorkspace(root=tmp_path, project_path=tmp_path / "proj")

    package = build_website_execution_plan(
        execution_id="execution_test",
        brief=brief,
        handoff=handoff,
        workspace=workspace,
        provider_name="OpenCode",
        model_name="local-codex",
        qa_commands=("npm run build",),
        ai_ask=lambda _prompt: _full_ai_response(),
    )

    assert package.mode == "dry_run"
    assert "Do NOT rewrite, simplify, or remove the existing animation" in package.prompt
    assert "Implement the approved AI Freelancer Studio project brief" not in package.prompt
    assert (workspace.project_path / "frontend/package.json").is_file()
    assert (workspace.project_path / "frontend/src/App.jsx").is_file()
    for section in SECTION_LIBRARY:
        for relative_path in section.files:
            assert (workspace.project_path / "frontend/src" / relative_path).is_file()


def _tokens_response(accent: str) -> str:
    return json.dumps(
        {
            "light": {"primary": accent, "secondary": "#333333", "accent": accent, "background": "#ffffff", "surface": "#eeeeee", "text": "#111111", "muted": "#999999"},
            "dark": {"primary": accent, "secondary": "#cccccc", "accent": accent, "background": "#000000", "surface": "#111111", "text": "#eeeeee", "muted": "#666666"},
            "font_pairing": "editorial-serif",
        }
    )


def _ai_ask_for(accent: str):
    def _ask(prompt: str) -> str:
        if "font_pairing" in prompt:
            return _tokens_response(accent)
        return _full_ai_response()

    return _ask


def test_two_different_briefs_produce_two_different_tokens_css(tmp_path):
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)

    first_workspace = ProjectWorkspace(root=tmp_path, project_path=tmp_path / "first")
    build_website_execution_plan(
        execution_id="execution_first",
        brief=brief,
        handoff=handoff,
        workspace=first_workspace,
        provider_name="OpenCode",
        model_name="local-codex",
        qa_commands=(),
        ai_ask=_ai_ask_for("#ff0000"),
    )

    second_workspace = ProjectWorkspace(root=tmp_path, project_path=tmp_path / "second")
    build_website_execution_plan(
        execution_id="execution_second",
        brief=brief,
        handoff=handoff,
        workspace=second_workspace,
        provider_name="OpenCode",
        model_name="local-codex",
        qa_commands=(),
        ai_ask=_ai_ask_for("#00ff00"),
    )

    first_css = (first_workspace.project_path / "frontend/src/tokens.css").read_text(encoding="utf-8")
    second_css = (second_workspace.project_path / "frontend/src/tokens.css").read_text(encoding="utf-8")

    assert first_css != second_css
    assert "#ff0000" in first_css
    assert "#00ff00" in second_css


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, *, stage, agent, progress, message, level=EventLevel.INFO, details=(), kind=EventKind.ACTIVITY):
        self.events.append({"stage": stage, "agent": agent, "progress": progress, "message": message})

    def artifact(self, *, kind, name, summary, reference):
        raise AssertionError("not expected to be called by website_generation")


def _custom_sections_response() -> str:
    return json.dumps(
        {
            "sections": [
                {"component_name": "PopularGamesGrid", "brief": "A grid of the platform's most popular titles."},
                {"component_name": "MostAnticipated", "brief": "A single highlighted card for an upcoming title."},
            ]
        }
    )


def _ai_ask_with_custom_sections(accent: str = "#ff0000"):
    def _ask(prompt: str) -> str:
        if "font_pairing" in prompt:
            return _tokens_response(accent)
        if "ADDITIONAL, bespoke sections" in prompt:
            return _custom_sections_response()
        return _full_ai_response()

    return _ask


def test_propose_custom_sections_parses_valid_response():
    brief = _brief("PS5 concept landing page with a games showcase.")
    handoff = _handoff(brief)

    proposed = propose_custom_sections(brief, handoff, ai_ask=lambda _prompt: _custom_sections_response())

    assert [cs.component_name for cs in proposed] == ["PopularGamesGrid", "MostAnticipated"]
    assert "popular titles" in proposed[0].brief


def test_propose_custom_sections_degrades_to_empty_on_garbage_response():
    brief = _brief("PS5 concept landing page.")
    handoff = _handoff(brief)

    assert propose_custom_sections(brief, handoff, ai_ask=lambda _prompt: "not json at all") == ()


def test_propose_custom_sections_degrades_to_empty_when_ai_ask_raises():
    brief = _brief("PS5 concept landing page.")
    handoff = _handoff(brief)

    def _boom(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    assert propose_custom_sections(brief, handoff, ai_ask=_boom) == ()


def test_propose_custom_sections_drops_invalid_names_and_dedupes_and_caps():
    brief = _brief("PS5 concept landing page.")
    handoff = _handoff(brief)
    response = json.dumps(
        {
            "sections": [
                {"component_name": "not_pascal_case", "brief": "dropped: invalid identifier"},
                {"component_name": "Games", "brief": "first"},
                {"component_name": "Games", "brief": "duplicate slug, dropped"},
                {"component_name": "Specs", "brief": "b"},
                {"component_name": "Reviews", "brief": "c"},
                {"component_name": "History", "brief": "d"},
                {"component_name": "Accessories", "brief": "over the cap, dropped"},
            ]
        }
    )

    proposed = propose_custom_sections(brief, handoff, ai_ask=lambda _prompt: response)

    assert [cs.component_name for cs in proposed] == ["Games", "Specs", "Reviews", "History"]


def test_build_website_execution_plan_includes_custom_sections_in_prompt(tmp_path):
    brief = _brief("PS5 concept landing page with a games showcase and a most-anticipated title card.")
    handoff = _handoff(brief)
    workspace = ProjectWorkspace(root=tmp_path, project_path=tmp_path / "proj")
    sink = _RecordingSink()

    package = build_website_execution_plan(
        execution_id="execution_test",
        brief=brief,
        handoff=handoff,
        workspace=workspace,
        provider_name="OpenCode",
        model_name="local-codex",
        qa_commands=("npm run build",),
        ai_ask=_ai_ask_with_custom_sections(),
        event_sink=sink,
    )

    assert "PopularGamesGrid" in package.prompt
    assert "MostAnticipated" in package.prompt
    assert "frontend/src/sections/PopularGamesGrid.jsx" in package.prompt
    assert "Do not use" in package.prompt and "trademarked artwork" in package.prompt
    assert "between <ScrollRevealSection /> and <ClosingCTA />" in package.prompt
    # The curated files are still the only ones materialized on disk up front --
    # the coding agent is responsible for writing the custom section files itself.
    assert not (workspace.project_path / "frontend/src/sections/PopularGamesGrid.jsx").exists()
    assert any("Drafting custom sections" in event["message"] for event in sink.events)


def test_build_website_execution_plan_reports_when_no_custom_sections_proposed(tmp_path):
    brief = _brief("Cinematic WebGL showcase site.")
    handoff = _handoff(brief)
    workspace = ProjectWorkspace(root=tmp_path, project_path=tmp_path / "proj")
    sink = _RecordingSink()

    package = build_website_execution_plan(
        execution_id="execution_test",
        brief=brief,
        handoff=handoff,
        workspace=workspace,
        provider_name="OpenCode",
        model_name="local-codex",
        qa_commands=("npm run build",),
        ai_ask=lambda _prompt: _full_ai_response(),
        event_sink=sink,
    )

    assert "ADDITIONAL, bespoke sections" not in package.prompt
    assert any("shipping the curated sections only" in event["message"] for event in sink.events)
