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
