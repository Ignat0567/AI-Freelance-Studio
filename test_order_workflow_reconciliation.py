"""The description and the brief disagreeing, and nobody saying so.

Every case below is taken from one live run on 2026-08-18 that produced two of them at once
and cost $12.84 building the wrong shape of thing. The point of these tests is not that the
brief wins -- it should, it is what every later stage reads -- but that the client is told,
on the screen where they approve it, while changing their mind is still free.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow.models import ElenaDesignChoice, ProductType, ProjectBrief, RecommendedStack, UserOrder
from order_workflow.reconciliation import reconcile_description_with_brief


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

PAGE_DESCRIPTION = (
    "A scrolling presentation page for AI Freelance Studio. A single self-contained page, "
    "no login, no password field, the only input is a newsletter email."
)


def _order(description: str, product_type: str = "web_app", title: str = "AI Freelance Studio") -> UserOrder:
    return UserOrder(
        id="order_recon",
        title=title,
        description=description,
        product_type=product_type,
        created_at=NOW,
        updated_at=NOW,
    )


def _brief(product_type: ProductType = ProductType.WEB_APP, target_users=("Single local user",)) -> ProjectBrief:
    return ProjectBrief(
        id="brief_recon",
        order_id="order_recon",
        product_type=product_type,
        goal="A scrolling presentation page.",
        target_users=tuple(target_users),
        core_features=("Show the pipeline",),
        acceptance_criteria=("The page renders",),
        recommended_stack=RecommendedStack(),
        elena_design_choice=ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT,
        created_at=NOW,
        updated_at=NOW,
    )


# --- the product-type mismatch that built an app instead of a page --------------------


def test_a_page_description_under_a_web_app_type_is_reported():
    notes = reconcile_description_with_brief(_order(PAGE_DESCRIPTION), _brief())

    assert len(notes) == 1
    assert "single self-contained page" in notes[0]
    assert "small web application" in notes[0]


def test_the_note_says_which_side_will_be_followed():
    """A warning that does not say what happens next is just anxiety. The brief wins, and the
    note has to say so plainly -- and say where to change it."""
    notes = reconcile_description_with_brief(_order(PAGE_DESCRIPTION), _brief())

    assert "follows the product type" in notes[0]
    assert "change it" in notes[0]


def test_the_reverse_mismatch_is_reported_too():
    order = _order("An app with several screens and navigation between them.", product_type="static_page")
    notes = reconcile_description_with_brief(order, _brief(product_type=ProductType.STATIC_PAGE))

    assert len(notes) == 1
    assert "several screens" in notes[0]
    assert "single interactive page" in notes[0]


def test_a_matching_order_produces_no_note_at_all():
    """A note on every order teaches people to skip the section it appears in."""
    order = _order("A single self-contained page.", product_type="static_page")

    assert reconcile_description_with_brief(order, _brief(product_type=ProductType.STATIC_PAGE)) == ()


def test_an_ordinary_app_order_is_left_alone():
    order = _order("A reading journal with three shelves and a page for each book.")

    assert reconcile_description_with_brief(order, _brief()) == ()


# --- the audience mismatch that built a server for a static page ----------------------


def test_no_sign_in_against_a_shared_audience_is_reported():
    """The same run also grew a FastAPI backend and a database, because a shared audience
    makes decide_backend_need conclude server-side state is required -- for a page whose
    description said nobody signs in."""
    notes = reconcile_description_with_brief(
        _order(PAGE_DESCRIPTION, product_type="static_page"),
        _brief(product_type=ProductType.STATIC_PAGE, target_users=("Public users",)),
    )

    assert len(notes) == 1
    assert "nobody signs in" in notes[0]
    assert "server and a database" in notes[0]


def test_a_private_description_with_a_single_user_audience_is_fine():
    notes = reconcile_description_with_brief(
        _order(PAGE_DESCRIPTION, product_type="static_page"),
        _brief(product_type=ProductType.STATIC_PAGE, target_users=("Single local user",)),
    )

    assert notes == ()


def test_both_contradictions_are_reported_together():
    notes = reconcile_description_with_brief(
        _order(PAGE_DESCRIPTION),
        _brief(target_users=("A small internal team",)),
    )

    assert len(notes) == 2


# --- shape ---------------------------------------------------------------------------


def test_every_note_fits_the_assumptions_field():
    """assumptions is ShortText. A note that fails validation would take the whole brief with
    it -- turning a helpful warning into a failed order.

    The worst case is a maximum-length audience, not an arbitrary one: target_users is itself
    ShortText, so the model refuses anything longer and the situation cannot arise."""
    longest_audience = ("Public users " + "x" * 227)[:240]
    notes = reconcile_description_with_brief(
        _order(PAGE_DESCRIPTION),
        _brief(target_users=(longest_audience,)),
    )

    assert len(notes) == 2, "both the product-type and the audience note should fire here"
    assert all(len(note) <= 240 for note in notes)


def test_the_title_counts_as_part_of_the_description():
    order = _order("Build it nicely.", title="Landing page for my studio")

    assert reconcile_description_with_brief(order, _brief())


# --- it has to reach the screen the client approves on --------------------------------


def test_the_note_lands_in_the_brief_the_client_approves():
    """The check is worthless unless it reaches the approval screen. assumptions is already
    rendered there (ProjectBriefPanel's "Assumptions" section), which is why the note goes
    there rather than into a new field nothing displays."""
    from order_workflow.clarification import AlexClarificationService
    from order_workflow.brief_service import ProjectBriefService

    order = UserOrder(
        id="order_recon_live",
        title="AI Freelance Studio",
        description=(
            "A single self-contained page presenting the studio. It shows what the product does, "
            "how it works in five steps, why it can be trusted, and a newsletter email field. "
            "No login, no password, no accounts anywhere on the page."
        ),
        product_type="web_app",
        created_at=NOW,
        updated_at=NOW,
    )
    from order_workflow.models import ClarificationAnswer

    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    # core-features is the one question with no recommended answer, so a client has to type
    # it -- use_recommended_defaults cannot close the session on its own. Answering it here
    # is what the real flow does, not a workaround.
    answered = clarification.apply_answers(
        started.order,
        started.session,
        (ClarificationAnswer(question_id="core-features", value="Read what the product does; scroll five steps; subscribe by email"),),
    )
    resolved = clarification.use_recommended_defaults(answered.order, answered.session)
    briefs = ProjectBriefService(clock=lambda: NOW)

    brief = briefs.generate(resolved.order, resolved.session)

    assert any("single self-contained page" in item for item in brief.assumptions)
    assert any("small web application" in item for item in brief.assumptions)


def test_an_agreeing_order_gains_no_extra_assumptions():
    from order_workflow.clarification import AlexClarificationService
    from order_workflow.brief_service import ProjectBriefService

    order = UserOrder(
        id="order_recon_ok",
        title="Reading Journal",
        description=(
            "A personal reading journal that lives entirely in one browser on my own laptop. "
            "I add a book by typing its title, author and page count, and it goes onto one of "
            "three shelves. Opening a book shows notes I write as I read."
        ),
        product_type="web_app",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    resolved = clarification.use_recommended_defaults(started.order, started.session)

    brief = ProjectBriefService(clock=lambda: NOW).generate(resolved.order, resolved.session)

    assert not any("product type" in item for item in brief.assumptions)
