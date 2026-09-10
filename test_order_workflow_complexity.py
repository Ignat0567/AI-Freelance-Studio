from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow.complexity import (
    classify_phase_complexity,
    describe_phase_complexity,
    model_for_complexity,
    substantive_technical_constraints,
)
from order_workflow.models import ElenaDesignChoice, ProjectBrief, RecommendedStack

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


# --- the default-stack constraints must not count as complexity signal ----------------
# brief_service injects exactly these three on every generic web_app brief. Counting them
# made the >=3 threshold fire on every web app ever generated -- including a single static
# page -- so the classifier always answered "complex" and never routed to the cheap tier.

BOILERPLATE_STACK_CONSTRAINTS = (
    "React + Vite frontend",
    "FastAPI backend where required",
    "SQLite local storage",
)


def test_the_injected_default_stack_constraints_are_not_substantive():
    brief = _brief(technical_constraints=BOILERPLATE_STACK_CONSTRAINTS, recommended_stack=RecommendedStack())

    assert substantive_technical_constraints(brief) == ()


def test_a_trivial_web_app_brief_routes_to_the_cheap_model():
    # The regression this guards: "a single page showing a name and a photo" was routed to
    # the expensive model purely because of the three scaffolding constraints above.
    brief = _brief(
        goal="A single page showing my name and a photo.",
        core_features=("Show a name and a photo.",),
        technical_constraints=BOILERPLATE_STACK_CONSTRAINTS,
        recommended_stack=RecommendedStack(),
    )

    complexity = classify_phase_complexity(brief, focus_text=f"{brief.core_features[0]} {brief.goal}")

    assert complexity == "routine"
    assert model_for_complexity(complexity) == "sonnet"


def test_project_specific_constraints_still_count_alongside_the_stack_boilerplate():
    brief = _brief(
        technical_constraints=(
            *BOILERPLATE_STACK_CONSTRAINTS,
            "Local PDF parsing, chunking, and vector indexing",
            "Provider abstraction for grounded LLM answers",
            "Only retrieved document fragments may leave the machine",
        ),
        recommended_stack=RecommendedStack(),
    )

    assert len(substantive_technical_constraints(brief)) == 3
    assert classify_phase_complexity(brief, focus_text="Answer questions about a document.") == "complex"


def test_describe_phase_complexity_explains_a_keyword_match():
    brief = _brief(technical_constraints=BOILERPLATE_STACK_CONSTRAINTS, recommended_stack=RecommendedStack())

    complexity, reason = describe_phase_complexity(brief, focus_text="Send a desktop notification.")

    assert complexity == "complex"
    assert "notification" in reason


def test_describe_phase_complexity_explains_a_routine_decision():
    brief = _brief(technical_constraints=BOILERPLATE_STACK_CONSTRAINTS, recommended_stack=RecommendedStack())

    complexity, reason = describe_phase_complexity(brief, focus_text="Show a list of books.")

    assert complexity == "routine"
    assert reason


# --- a keyword that is being excluded is not evidence of difficulty -------------------
# Found live on 2026-09-10: an order whose description ended "No backend, no database, no
# authentication, no user accounts." routed its ui_shell phase to the expensive model with
# the reason `matched 'authentication'` -- charged the hard-work rate for saying it did not
# want the hard work.


@pytest.mark.parametrize(
    "focus_text",
    [
        "No backend, no database, no authentication, no user accounts.",
        "The app has no authentication.",
        "Runs without authentication of any kind.",
        "There is never any payment involved.",
        "It doesn't need authentication.",
        "Excluding authentication, this is a plain list.",
    ],
)
def test_a_negated_keyword_does_not_route_to_complex(focus_text):
    brief = _brief()

    assert classify_phase_complexity(brief, focus_text=focus_text) == "routine"


@pytest.mark.parametrize(
    "focus_text",
    [
        "The user signs in with authentication.",
        # The negation belongs to the previous clause, not to this keyword.
        "There is no backend, but authentication is required.",
        # ...nor does one from the previous sentence carry over.
        "There is no backend. Authentication uses OAuth.",
        # Far enough away to be about something else entirely.
        "No decorative animation, no icons, no illustrations, and authentication throughout.",
        # Negated once, meant once.
        "No authentication yet, though authentication is planned.",
    ],
)
def test_an_affirmative_keyword_still_routes_to_complex(focus_text):
    brief = _brief()

    assert classify_phase_complexity(brief, focus_text=focus_text) == "complex"


def test_the_reason_names_only_the_keywords_that_actually_counted():
    brief = _brief()

    complexity, reason = describe_phase_complexity(
        brief, focus_text="No authentication and no payment, but it does use websocket streaming."
    )

    assert complexity == "complex"
    assert "websocket" in reason
    assert "authentication" not in reason
    assert "payment" not in reason


def test_negation_scan_does_not_disturb_an_ordinary_keyword_brief():
    # The pre-existing behaviour this must not regress: a plain affirmative mention.
    brief = _brief()

    complexity, reason = describe_phase_complexity(brief, focus_text="Sync data in real-time across devices.")

    assert complexity == "complex"
    assert "real-time" in reason
