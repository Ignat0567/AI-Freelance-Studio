from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow.complexity import classify_phase_complexity, model_for_complexity
from order_workflow.models import ElenaDesignChoice, ProjectBrief

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_complexity",
        "order_id": "order_complexity",
        "goal": "Build a small internal tool.",
        "target_users": ("Small internal team",),
        "core_features": ("Track work items",),
        "acceptance_criteria": ("A user can complete the primary workflow.",),
        "elena_design_choice": ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProjectBrief(**values)


def test_plain_brief_and_focus_text_is_routine():
    brief = _brief()

    assert classify_phase_complexity(brief, focus_text="Track work items. Build a small internal tool.") == "routine"


@pytest.mark.parametrize(
    "keyword",
    ["real-time", "payment", "authentication", "third-party integration", "websocket", "oauth"],
)
def test_a_complexity_keyword_in_focus_text_routes_to_complex(keyword):
    brief = _brief()

    assert classify_phase_complexity(brief, focus_text=f"Build a feature involving {keyword}.") == "complex"


def test_many_technical_constraints_routes_to_complex_even_without_keywords():
    brief = _brief(technical_constraints=("Must run offline-first.", "Must support 10k concurrent rows.", "Must use existing auth provider."))

    assert classify_phase_complexity(brief, focus_text="Track work items.") == "complex"


def test_many_core_features_routes_to_complex_even_without_keywords():
    brief = _brief(core_features=("A", "B", "C", "D", "E"))

    assert classify_phase_complexity(brief, focus_text="Track work items.") == "complex"


def test_model_for_complexity_mapping():
    assert model_for_complexity("routine") == "sonnet"
    assert model_for_complexity("complex") == "opus"
