from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .models import ProjectBrief

# Deterministic hour multipliers, driven entirely by real counts already on a real
# ProjectBrief -- not an AI guess -- so the estimate can never drift out of sync with
# what's actually being asked for. Each constant is a rough, transparent rule of
# thumb, easy to tune later; PROJECT_SETUP_HOURS is a fixed floor every project pays
# (scaffolding, deploy, QA wiring) regardless of feature count.
PROJECT_SETUP_HOURS = 8.0
HOURS_PER_FEATURE = 6.0
HOURS_PER_ACCEPTANCE_CRITERION = 1.5
HOURS_PER_UI_REQUIREMENT = 2.0
HOURS_PER_TECHNICAL_CONSTRAINT = 1.0

DEFAULT_HOURLY_RATE = 45.0
DEFAULT_CONTINGENCY = 0.25

_MAX_VALUE_PROPOSITION_LENGTH = 500


@dataclass(frozen=True, slots=True)
class CostEstimate:
    min_hours: float
    max_hours: float
    hourly_rate: float
    min_cost: float
    max_cost: float
    currency: str = "USD"


def estimate_project_cost(brief: ProjectBrief, hourly_rate: float = DEFAULT_HOURLY_RATE, contingency: float = DEFAULT_CONTINGENCY) -> CostEstimate:
    base_hours = (
        PROJECT_SETUP_HOURS
        + len(brief.core_features) * HOURS_PER_FEATURE
        + len(brief.acceptance_criteria) * HOURS_PER_ACCEPTANCE_CRITERION
        + len(brief.ui_requirements) * HOURS_PER_UI_REQUIREMENT
        + len(brief.technical_constraints) * HOURS_PER_TECHNICAL_CONSTRAINT
    )
    min_hours = round(base_hours, 1)
    max_hours = round(base_hours * (1 + contingency), 1)
    return CostEstimate(
        min_hours=min_hours,
        max_hours=max_hours,
        hourly_rate=hourly_rate,
        min_cost=round(min_hours * hourly_rate, 2),
        max_cost=round(max_hours * hourly_rate, 2),
    )


def _fallback_value_proposition(goal: str) -> str:
    trimmed = goal.strip().rstrip(".")
    if not trimmed:
        return "This project will be built to your exact requirements by AI Freelance Studio."
    return f"This project delivers on: {trimmed}."


def generate_value_proposition(brief: ProjectBrief, ai_ask: Callable[[str], str]) -> str:
    """One narrow AI call for a short "why this matters" paragraph. Never raises --
    an unusable AI response degrades to a plain deterministic sentence built from
    `brief.goal`, same fallback discipline as `project_docs.generate_overview_paragraph`."""
    prompt = (
        "Write a single short paragraph (2-3 sentences, no more than 500 characters) for a client-facing "
        "commercial proposal, explaining why this project matters and what value it delivers. Be concrete "
        "and specific to this project, not generic marketing language.\n\n"
        f"Project goal: {brief.goal}\n"
        "Key features:\n" + "\n".join(f"- {feature}" for feature in brief.core_features) + "\n\n"
        "Respond with only the paragraph text, no heading, no quotes."
    )
    try:
        raw = ai_ask(prompt).strip()
    except Exception:
        raw = ""
    if not raw:
        return _fallback_value_proposition(brief.goal)
    return raw[:_MAX_VALUE_PROPOSITION_LENGTH]


def build_proposal(brief: ProjectBrief, estimate: CostEstimate, value_proposition: str) -> str:
    tech_stack = (
        f"- Frontend: {brief.recommended_stack.frontend}\n"
        f"- Backend: {brief.recommended_stack.backend}\n"
        f"- Storage: {brief.recommended_stack.storage}"
    )
    lines = [
        "# Project Proposal",
        "",
        "## Understanding Your Need",
        "",
        brief.goal,
        "",
        "## Value Proposition",
        "",
        value_proposition,
        "",
        "## Proposed Scope",
        "",
        *(f"- {feature}" for feature in brief.core_features),
        "",
        "## Timeline & Investment",
        "",
        f"Estimated effort: {estimate.min_hours:g}–{estimate.max_hours:g} hours at {estimate.hourly_rate:g} {estimate.currency}/hour.",
        f"Estimated cost: {estimate.min_cost:,.2f}–{estimate.max_cost:,.2f} {estimate.currency}.",
        "",
        "*This is an automated estimate based on the scoped requirements, not a fixed quote. "
        "The final price is confirmed after review.*",
        "",
        "## Tech Stack",
        "",
        tech_stack,
        "",
        "## Next Steps",
        "",
        "Reply to this proposal to confirm scope and kick off the project, or let us know what needs adjusting.",
    ]
    return "\n".join(lines).rstrip() + "\n"
