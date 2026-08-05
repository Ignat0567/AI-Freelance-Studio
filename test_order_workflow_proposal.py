from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow import AlexClarificationService, DesignPreviewService, ProjectBriefService, UserOrder
from order_workflow.proposal import (
    DEFAULT_CONTINGENCY,
    DEFAULT_HOURLY_RATE,
    build_proposal,
    estimate_project_cost,
    generate_value_proposition,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
PDF = "Create a browser PDF voice assistant with upload, voice and text chat, grounded answers, page citations and speech playback."


def _clock() -> datetime:
    return NOW


def _real_brief(description: str = PDF):
    order = UserOrder(id="order_proposal", title="Proposal Test", description=description, product_type="web_app", created_at=NOW, updated_at=NOW)
    clarification = AlexClarificationService(clock=_clock)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=lambda: "proposal-brief", clock=_clock)
    brief = briefs.generate(result.order, result.session)
    return briefs.approve(brief, briefs.prepare_approval(brief))


def test_estimate_project_cost_scales_with_more_features():
    brief = _real_brief()
    small_estimate = estimate_project_cost(brief)
    padded_brief = brief.model_copy(update={"core_features": (*brief.core_features, "An extra feature")})
    larger_estimate = estimate_project_cost(padded_brief)

    assert larger_estimate.min_hours > small_estimate.min_hours
    assert larger_estimate.min_cost > small_estimate.min_cost


def test_estimate_project_cost_min_is_never_above_max():
    brief = _real_brief()
    estimate = estimate_project_cost(brief)

    assert estimate.min_hours <= estimate.max_hours
    assert estimate.min_cost <= estimate.max_cost
    assert estimate.hourly_rate == DEFAULT_HOURLY_RATE


def test_estimate_project_cost_respects_custom_rate_and_contingency():
    brief = _real_brief()
    default_estimate = estimate_project_cost(brief)
    custom_estimate = estimate_project_cost(brief, hourly_rate=100.0, contingency=0.5)

    assert custom_estimate.hourly_rate == 100.0
    assert custom_estimate.max_hours > default_estimate.max_hours
    assert custom_estimate.min_cost == pytest.approx(default_estimate.min_hours * 100.0)


def test_estimate_project_cost_has_a_positive_floor_even_with_minimal_brief():
    brief = _real_brief()
    stripped = brief.model_copy(update={"ui_requirements": (), "technical_constraints": ()})

    estimate = estimate_project_cost(stripped)

    assert estimate.min_hours > 0
    assert estimate.min_cost > 0


def test_generate_value_proposition_uses_ai_response_when_available():
    brief = _real_brief()

    result = generate_value_proposition(brief, lambda _prompt: "A real, concrete value proposition.")

    assert result == "A real, concrete value proposition."


def test_generate_value_proposition_falls_back_when_ai_raises():
    brief = _real_brief()

    def _raising_ai_ask(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    result = generate_value_proposition(brief, _raising_ai_ask)

    assert result != ""
    assert "This project" in result


def test_generate_value_proposition_falls_back_when_ai_returns_empty():
    brief = _real_brief()

    result = generate_value_proposition(brief, lambda _prompt: "   ")

    assert result != ""


def test_build_proposal_contains_every_real_substituted_value():
    brief = _real_brief()
    estimate = estimate_project_cost(brief)
    value_proposition = "A concrete, specific value proposition sentence."

    proposal = build_proposal(brief, estimate, value_proposition)

    assert "# Project Proposal" in proposal
    assert brief.goal in proposal
    assert value_proposition in proposal
    for feature in brief.core_features:
        assert f"- {feature}" in proposal
    assert f"{estimate.min_hours:g}" in proposal
    assert f"{estimate.max_hours:g}" in proposal
    assert "automated estimate" in proposal
    assert brief.recommended_stack.backend in proposal
    assert "Next Steps" in proposal
