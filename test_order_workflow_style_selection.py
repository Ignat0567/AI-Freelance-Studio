"""Elena picking a look, and what happens when the client already described one.

The case these tests exist for: on 2026-08-18 an order asking for "a deep near-black ground"
with "frosted glass" panels was given the white SaaS pack, because the description also
contained the word "product". The Liquid Glass pack -- frosted glass over an ambient
gradient, exactly what was asked for -- scored zero, because its keywords were all about
audiences and none about appearance.

That palette then became binding rather than advisory: it entered the prompt as "paint the
page ground exactly #eef4fb", was written into the workspace as CSS variables, and the visual
gate measured adherence to it and passed the page. One wrong word at the start, reinforced by
every step after it. The tests below pin the rule that prevents it: a stated look decides,
and the domain only answers when nobody stated one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow.design_preview import _select_style
from order_workflow.models import ElenaDesignChoice, ProductType, ProjectBrief, RecommendedStack
from order_workflow.style_library import STYLE_LIBRARY


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

THE_ORDER_THAT_FAILED = (
    "A scrolling presentation page for a product called AI Freelance Studio. A deep "
    "near-black ground with a slow aurora of teal and violet light drifting behind "
    "everything, and faint stars. Every panel is frosted glass, translucent and softly "
    "blurred, with a thin bright edge catching the light."
)


def _brief(goal: str) -> ProjectBrief:
    return ProjectBrief(
        id="brief_style",
        order_id="order_style",
        product_type=ProductType.STATIC_PAGE,
        goal=goal,
        target_users=("Public users",),
        core_features=("Show the pipeline",),
        acceptance_criteria=("The page renders",),
        recommended_stack=RecommendedStack(),
        elena_design_choice=ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT,
        created_at=NOW,
        updated_at=NOW,
    )


def test_the_order_that_got_the_wrong_palette_now_gets_the_right_one():
    assert _select_style(_brief(THE_ORDER_THAT_FAILED)).slug == "liquid_glass"


def test_one_domain_word_no_longer_outranks_a_described_look():
    """"product" is why the SaaS pack won. It is still in the description here."""
    assert "product" in THE_ORDER_THAT_FAILED
    assert _select_style(_brief(THE_ORDER_THAT_FAILED)).slug != "corporate_gradient_mesh"


def test_the_domain_still_decides_when_no_look_is_described():
    """The fallback has to keep working: most orders say what they are for and nothing about
    how they should look."""
    assert _select_style(_brief("A SaaS product dashboard for software startups.")).slug == "corporate_gradient_mesh"


def test_a_description_with_neither_look_nor_domain_gets_a_default():
    style = _select_style(_brief("Something useful, please."))

    assert style is STYLE_LIBRARY[0]


@pytest.mark.parametrize(
    ("described", "expected"),
    [
        ("Monospace type on a green on black terminal aesthetic with a blinking cursor.", "retro_terminal"),
        ("Pure black and white, oversized type, no colour at all.", "minimal_mono"),
        ("Near-black backgrounds with saturated neon glow and scanlines.", "cyberpunk_neon"),
        ("Thick black borders with hard offset shadows and flat colour blocks.", "neubrutalism"),
        ("Earthy palette with hand-drawn organic shapes.", "organic_wellness"),
    ],
)
def test_each_described_look_reaches_its_own_pack(described, expected):
    assert _select_style(_brief(described)).slug == expected


def test_every_pack_can_be_chosen_by_describing_it():
    """A pack with no visual vocabulary can only ever be reached by naming an industry, which
    is how Liquid Glass came to be unreachable for someone describing frosted glass."""
    assert all(style.visual_cues for style in STYLE_LIBRARY)


def test_the_two_vocabularies_stay_separate():
    """Merging them would restore the original bug: the whole point is that a stated look and
    an inferred domain are not equally strong evidence."""
    for style in STYLE_LIBRARY:
        assert not set(style.visual_cues) & set(style.when_to_use)


# --- the palette the build is actually bound to ---------------------------------------


def _concept(description: str):
    from order_workflow.brief_service import ProjectBriefService
    from order_workflow.clarification import AlexClarificationService
    from order_workflow.models import ClarificationAnswer, UserOrder

    order = UserOrder(
        id="order_palette",
        title="AI Freelance Studio",
        description=description,
        product_type="web_app",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    current_order, session = started.order, started.session
    if any(question.id == "core-features" for question in current_order.questions):
        answered = clarification.apply_answers(
            current_order, session,
            (ClarificationAnswer(question_id="core-features", value="Read it; scroll the steps; subscribe by email"),),
        )
        current_order, session = answered.order, answered.session
    resolved = clarification.use_recommended_defaults(current_order, session)
    return ProjectBriefService(clock=lambda: NOW).generate(resolved.order, resolved.session).elena_design_concept


DARK_ORDER = (
    "A presentation page with a deep near-black ground, a slow aurora of light and frosted "
    "glass panels. A visitor reads what it does, scrolls five steps, and subscribes by email."
)


def test_the_concept_palette_follows_the_described_style():
    """Fixing style selection alone did not reach this. The pack decided the prose in the
    prompt while ElenaDesignConcept kept a hardcoded pale blue -- and the concept, not the
    prose, is what design_tokens.py writes into the workspace and what the visual gate
    measures adherence to."""
    concept = _concept(DARK_ORDER)

    assert concept.visual_direction == "Liquid Glass"
    assert concept.light_theme.background != "#eef4fb"


def test_a_design_described_as_dark_is_dark_by_default():
    """light_theme is the slot that becomes `:root`. Leaving the near-black in the dark slot
    would serve a near-white page to every visitor whose system is not already in dark mode --
    which is not what "a deep near-black ground" asks for."""
    concept = _concept(DARK_ORDER)

    assert concept.light_theme.background == "#08080b"
    assert concept.dark_theme.background == "#08080b"


def test_an_order_that_describes_no_look_keeps_a_light_default():
    concept = _concept("A clean SaaS product dashboard for software startups with charts and a settings page.")

    assert concept.visual_direction == "Corporate Gradient Mesh"
    assert concept.light_theme.background == "#eef4fb"
    assert concept.dark_theme.background == "#101725"


def test_every_pack_palette_clears_AA_in_both_themes():
    """These palettes are handed to the coding CLI as binding variables and then measured by
    the contrast gate. A pack that cannot pass its own gate would fail every order that chose
    it, through no fault of the generated code."""
    from order_workflow.design_tokens import contrast_ratio

    for style in STYLE_LIBRARY:
        for theme in (style.light_theme, style.dark_theme):
            assert contrast_ratio(theme.text, theme.background) >= 4.5, f"{style.slug} text on background"
            assert contrast_ratio(theme.text, theme.surface) >= 4.5, f"{style.slug} text on surface"


def test_a_keyword_inside_a_longer_word_does_not_count():
    """"hear the answers spoken aloud" scored a point for the loud, playful pack, because
    "loud" sits inside "aloud" -- so a PDF voice assistant was styled with thick borders and
    hard offset shadows. The bug predates the two vocabularies; it was invisible while the
    concept ignored style selection entirely."""
    from order_workflow.style_library import select_style_pack

    spoken = "Create a browser voice assistant that reads answers back to you, spoken aloud."

    assert select_style_pack(spoken).slug != "neubrutalism"


def test_a_keyword_that_matches_almost_every_order_carries_no_signal():
    """"app" sat in a pack's domain list and matched nearly anything anyone would order --
    the same weakness as "product", which is what started all of this."""
    from order_workflow.style_library import STYLE_LIBRARY

    for style in STYLE_LIBRARY:
        assert "app" not in style.when_to_use
