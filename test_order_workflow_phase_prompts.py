from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow import AgentHandoff, ElenaDesignChoice, ProjectBrief, RecommendedStack
from order_workflow.models import ElenaDesignConcept, ThemePalette
from order_workflow.phase_context import PhaseContext
from order_workflow.phase_prompts import (
    build_backend_bridge_prompt,
    build_core_feature_prompt,
    build_ui_shell_prompt,
    decide_backend_need,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_fixed",
        "order_id": "order_fixed",
        "goal": "Provide conversational search across uploaded PDF documents.",
        "target_users": ("Single local user",),
        "core_features": ("Document-grounded answers with citations", "PDF upload"),
        "acceptance_criteria": ("A user can upload a text-based PDF.",),
        "recommended_stack": RecommendedStack(),
        "elena_design_choice": ElenaDesignChoice.PROCEED_DIRECTLY,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProjectBrief(**values)


def _handoff(**changes) -> AgentHandoff:
    values = {
        "id": "handoff_fixed",
        "order_id": "order_fixed",
        "brief_id": "brief_fixed",
        "source_agent": "alex",
        "target_agent": "codex",
        "goal": "Implement the approved PDF assistant brief.",
        "context_summary": "Single-user web app with grounded PDF question answering.",
        "requirements": ("Show document and page citations", "Upload a PDF"),
        "acceptance_criteria": ("A typed question returns a grounded answer.",),
        "created_at": NOW,
    }
    values.update(changes)
    return AgentHandoff(**values)


def _phase_context(phase: str) -> PhaseContext:
    return PhaseContext(phase=phase, summary=f"{phase} completed.", files=("src/App.jsx",), qa_status="QA passed.")


def test_ui_shell_prompt_forbids_backend_auth_and_external_calls_and_requires_skeletons():
    prompt = build_ui_shell_prompt(_brief(), _handoff())

    lowered = prompt.lower()
    assert "do not connect a database" in lowered
    assert "do not implement authentication" in lowered
    assert "do not call any external api" in lowered
    assert "loading or skeleton state" in lowered
    assert "provide conversational search across uploaded pdf documents" in lowered
    assert "`preview` script" in prompt
    assert "4173" in prompt


def test_ui_shell_prompt_states_the_visual_gate_s_own_thresholds():
    # The gate measures these numbers on the built page. Leaving them out of the prompt that
    # produces the page made every one of them a discovery at Playwright speed: one live run
    # spent both repair attempts on a 4.05:1 label and a layout 236px too wide for a phone.
    prompt = build_ui_shell_prompt(_brief(), _handoff())

    assert "4.5:1" in prompt
    assert "3.0:1" in prompt
    assert "375x812" in prompt
    assert "24x24px" in prompt
    assert "overlap" in prompt.lower()


def test_ui_shell_prompt_names_the_approved_palette_when_there_is_one():
    concept = ElenaDesignConcept(
        visual_direction="Calm editorial",
        layout="Two-column shell with a persistent sidebar.",
        screens=("Library",),
        components=("Card",),
        light_theme=ThemePalette(background="#ffffff", surface="#f4f7fa", text="#172033", accent="#356cf6"),
        dark_theme=ThemePalette(background="#0f1420", surface="#1b2231", text="#e8ecf4", accent="#6d9bff"),
    )

    brief = _brief(
        # The model rejects a concept without the matching choice, so both move together.
        elena_design_choice=ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        elena_design_concept=concept,
    )

    prompt = build_ui_shell_prompt(brief, _handoff())

    for value in ("#ffffff", "#f4f7fa", "#172033", "#356cf6", "#0f1420", "#6d9bff"):
        assert value in prompt
    assert "prefers-color-scheme: dark" in prompt


def test_ui_shell_prompt_asserts_no_palette_when_none_was_approved():
    # The gate skips itself when there is no approved palette; demanding one here would
    # invent a standard the design stage never set.
    prompt = build_ui_shell_prompt(_brief(), _handoff())

    assert "approved background" not in prompt
    assert "prefers-color-scheme" not in prompt


def test_core_feature_prompt_names_exactly_one_feature_and_requires_a_test():
    brief = _brief()
    ui_context = _phase_context("ui_shell")

    prompt = build_core_feature_prompt(brief, _handoff(), ui_context)

    assert brief.core_features[0] in prompt
    assert brief.core_features[1] not in prompt
    assert "one automated test" in prompt.lower()
    assert ui_context.summary in prompt


def test_core_feature_prompt_states_what_the_state_continuity_gate_measures():
    # Measured: this gate failed on both CRUD runs of the repeatability batch and was
    # repaired both times, at ~3.5 minutes a run. The gate catches component-local state
    # that dies on unmount, and the prompt never mentioned it.
    prompt = build_core_feature_prompt(_brief(), _handoff(), _phase_context("ui_shell"))

    lowered = prompt.lower()
    assert "survive navigation" in lowered
    assert "usestate" in lowered
    assert "localstorage" in lowered


def test_core_feature_prompt_does_not_repeat_the_full_ui_shell_instructions():
    prompt = build_core_feature_prompt(_brief(), _handoff(), _phase_context("ui_shell"))

    assert "do not connect a database" not in prompt.lower()


def test_backend_bridge_prompt_states_phases_are_already_implemented():
    prompt = build_backend_bridge_prompt(
        _brief(), _handoff(), _phase_context("ui_shell"), _phase_context("core_feature"),
        "Multi-user sync required.", ("npm test",),
    )

    assert "already implemented" in prompt.lower()
    assert "must not be" in prompt.lower() or "not be rewritten" in prompt.lower()
    assert "Multi-user sync required." in prompt
    assert "Implement the approved AI Freelancer Studio project brief." in prompt


def test_single_local_user_with_no_sharing_needs_no_backend():
    decision = decide_backend_need(_brief(), _handoff())

    assert decision.needs_backend is False
    assert decision.confident is True


@pytest.mark.parametrize(
    "audience",
    ["A small internal team", "Customers using the application", "Public users"],
)
def test_shared_audiences_need_a_backend(audience):
    decision = decide_backend_need(_brief(target_users=(audience,)), _handoff())

    assert decision.needs_backend is True
    assert decision.confident is True
    assert audience.casefold() in decision.reasoning.casefold()


@pytest.mark.parametrize(
    "requirement",
    [
        "Synchronize saved answers between users",
        "Users sign in before uploading",
        "Support multi-user workspaces",
        "Keep documents available across devices",
    ],
)
def test_sharing_requirements_override_a_single_user_audience(requirement):
    # "Only me, but synced across my laptop and phone" is still a single-user audience
    # that needs somewhere to sync through.
    decision = decide_backend_need(_brief(), _handoff(requirements=(requirement,)))

    assert decision.needs_backend is True
    assert decision.confident is True


def test_unrecognised_audience_fails_closed_to_needing_a_backend():
    decision = decide_backend_need(_brief(target_users=("Whoever my cousin invites",)), _handoff())

    assert decision.needs_backend is True
    assert decision.confident is False
