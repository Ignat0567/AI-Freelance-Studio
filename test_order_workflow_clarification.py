from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    BriefRevisionKind,
    BriefRevisionOperation,
    BriefRevisionRequest,
    BriefServiceError,
    ClarificationAnswer,
    ClarificationError,
    ElenaDesignChoice,
    ProjectBrief,
    ProjectBriefService,
    UserOrder,
    UserOrderStatus,
    detect_requirement_gaps,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)
PDF_REQUEST = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions about their contents by voice or text, receive answers grounded in the "
    "documents with page references, and hear the answers spoken aloud."
)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"fixed-{self.index:03d}"


def _order(description: str = PDF_REQUEST, **changes) -> UserOrder:
    values = {
        "id": "order_pdf_voice",
        "title": "PDF Voice Assistant",
        "description": description,
        "product_type": "web_app",
        "preferred_language": "ru",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return UserOrder(**values)


def _services():
    ids = SequenceIds()
    clarification = AlexClarificationService(clock=lambda: NOW)
    briefs = ProjectBriefService(id_factory=ids, clock=lambda: NOW + timedelta(minutes=1))
    handoffs = AgentHandoffService(id_factory=ids, clock=lambda: NOW + timedelta(minutes=2))
    return clarification, briefs, handoffs


def _resolved_reference(*, defaults: bool = True):
    clarification, briefs, handoffs = _services()
    started = clarification.begin(_order())
    if defaults:
        completed = clarification.use_recommended_defaults(started.order, started.session)
    else:
        completed = clarification.apply_answers(
            started.order,
            started.session,
            tuple(
                ClarificationAnswer(question_id=question.id, value=question.recommended_answer)
                for question in started.order.questions
            ),
        )
    brief = briefs.generate(completed.order, completed.session)
    return clarification, briefs, handoffs, completed, brief


def test_sparse_description_creates_essential_questions_in_stable_order():
    unsafe_sparse_order = _order("", title="Small app")

    first = AlexClarificationService(clock=lambda: NOW).begin(unsafe_sparse_order)
    second = AlexClarificationService(clock=lambda: NOW).begin(unsafe_sparse_order)

    assert tuple(item.id for item in first.order.questions) == (
        "target-users",
        "core-features",
        "elena-design",
    )
    assert first.order.questions == second.order.questions


def test_bot_order_never_asks_the_elena_design_question():
    # A Telegram bot has no browser UI for Elena's visual design step to describe.
    sparse_bot = _order("", title="Small bot", product_type="bot")

    started = AlexClarificationService(clock=lambda: NOW).begin(sparse_bot)

    question_ids = tuple(item.id for item in started.order.questions)
    assert "elena-design" not in question_ids
    assert "target-users" in question_ids
    assert "core-features" in question_ids


def test_bot_order_brief_has_bot_product_type_and_no_elena_concept():
    clarification, briefs, _handoffs = _services()
    order = _order(
        "Build a Telegram bot that lets users track daily habits: add a habit, mark it done "
        "today, and see their current streak.",
        title="Habit Bot",
        product_type="bot",
    )
    started = clarification.begin(order)
    completed = clarification.use_recommended_defaults(started.order, started.session)

    brief = briefs.generate(completed.order, completed.session)

    assert brief.product_type.value == "bot"
    assert brief.elena_design_concept is None
    assert brief.ui_requirements == ()
    assert "telegram bot" in brief.goal.casefold()
    assert "telegram" in brief.recommended_stack.backend.casefold()
    assert brief.recommended_stack.frontend != "React + Vite"


def test_static_page_brief_includes_elena_living_plate_concept():
    clarification, briefs, _handoffs = _services()
    order = _order(
        "A single HTML page showing the name Mini Card and a button labelled Email.",
        title="Mini Card",
        product_type="static_page",
        id="order_mini_card",
    )
    started = clarification.begin(order)
    current_order, session = started.order, started.session
    if any(question.id == "core-features" for question in current_order.questions):
        answered = clarification.apply_answers(
            current_order,
            session,
            (ClarificationAnswer(question_id="core-features", value="View Mini Card, read Local Studio test, click Email"),),
        )
        current_order, session = answered.order, answered.session
    completed = clarification.use_recommended_defaults(current_order, session)
    brief = briefs.generate(completed.order, completed.session)

    assert brief.product_type.value == "static_page"
    assert brief.elena_design_choice is ElenaDesignChoice.SHOW_ELENA_CONCEPT
    assert brief.elena_design_concept is not None
    assert "living client plate" in brief.elena_design_concept.visual_direction.casefold()


def test_complete_web_description_creates_fewer_questions():
    sparse = AlexClarificationService(clock=lambda: NOW).begin(_order("Build an app.", title="Small app"))
    complete = AlexClarificationService(clock=lambda: NOW).begin(
        _order(
            "Build a browser dashboard for only me to create, edit, search, and delete local notes "
            "without sign-in; data persists after restart.",
            title="Local notes",
        )
    )

    assert len(complete.order.questions) < len(sparse.order.questions)
    assert tuple(item.id for item in complete.order.questions) == ("elena-design",)


def test_unsupported_product_type_is_rejected_even_for_unvalidated_input():
    base = _order()
    unsupported = UserOrder.model_construct(**{**base.model_dump(mode="python"), "product_type": "desktop_app"})
    with pytest.raises(ClarificationError) as error:
        detect_requirement_gaps(unsupported)
    assert error.value.code == "unsupported_product_type"


def test_pdf_request_infers_supplied_requirements_without_redundant_questions():
    result = AlexClarificationService(clock=lambda: NOW).begin(_order())
    question_ids = tuple(item.id for item in result.order.questions)

    assert question_ids == (
        "target-users",
        "document-ocr",
        "speech-languages",
        "document-persistence",
        "document-processing",
        "elena-design",
    )
    assert "core-features" not in question_ids
    assert not any("voice or text" in item.text.casefold() for item in result.order.questions)
    assert not any("page reference" in item.text.casefold() for item in result.order.questions)
    assert result.order.questions[1].recommended_answer is False


def test_answered_dimensions_are_not_repeated_in_later_rounds():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order())
    answered = service.apply_answers(
        started.order,
        started.session,
        (ClarificationAnswer(question_id="target-users", value="Only me"),),
    )
    advanced = service.advance_round(answered.order, answered.session)

    assert tuple(item.id for item in advanced.order.questions).count("target-users") == 1
    assert "target-users" in advanced.session.answered_question_ids


def test_recommended_defaults_complete_pdf_flow_and_are_visible():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order())
    completed = service.use_recommended_defaults(started.order, started.session)

    assert completed.order.status is UserOrderStatus.BRIEF_READY
    assert completed.session.remaining_dimensions == ()
    assert completed.session.elena_choice is ElenaDesignChoice.SHOW_ELENA_CONCEPT
    assert set(completed.session.recommended_defaults_used) == {item.id for item in started.order.questions}
    assumptions = " ".join(completed.session.assumptions).casefold()
    assert "single-user local" in assumptions
    assert "does not include ocr" in assumptions
    assert "russian and german" in assumptions
    assert "push-to-talk" in assumptions
    assert "retrieved fragments" in assumptions
    assert "persist locally" in assumptions


def test_unknown_invalid_and_conflicting_duplicate_answers_are_stable():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order())
    with pytest.raises(ClarificationError) as unknown:
        service.apply_answers(
            started.order,
            started.session,
            (ClarificationAnswer(question_id="unknown", value="value"),),
        )
    assert unknown.value.code == "unknown_question_id"
    with pytest.raises(ClarificationError) as invalid:
        service.apply_answers(
            started.order,
            started.session,
            (ClarificationAnswer(question_id="target-users", value="Robots"),),
        )
    assert invalid.value.code == "invalid_answer"

    once = service.apply_answers(
        started.order,
        started.session,
        (ClarificationAnswer(question_id="target-users", value="Only me"),),
    )
    idempotent = service.apply_answers(
        once.order,
        once.session,
        (ClarificationAnswer(question_id="target-users", value="Only me"),),
    )
    assert idempotent == once
    with pytest.raises(ClarificationError) as conflict:
        service.apply_answers(
            once.order,
            once.session,
            (ClarificationAnswer(question_id="target-users", value="Public users"),),
        )
    assert conflict.value.code == "duplicate_answer_conflict"


def test_round_limit_converts_optional_uncertainty_but_keeps_required_blockers():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order())
    at_two = service.advance_round(started.order, started.session)
    at_three = service.advance_round(at_two.order, at_two.session)
    limited = service.advance_round(at_three.order, at_three.session)

    assert limited.session.round_number == 3
    assert limited.order.status is UserOrderStatus.CLARIFICATION_REQUIRED
    assert "target_users" in {item.value for item in limited.session.remaining_dimensions}
    assert any("does not include OCR" in item for item in limited.session.assumptions)


def test_round_limit_allows_required_answers_and_turns_optional_items_into_assumptions():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order())
    required_answers = tuple(
        ClarificationAnswer(question_id=item.id, value=item.recommended_answer)
        for item in started.order.questions
        if item.required
    )
    partial = service.apply_answers(started.order, started.session, required_answers)
    at_two = service.advance_round(partial.order, partial.session)
    at_three = service.advance_round(at_two.order, at_two.session)
    limited = service.advance_round(at_three.order, at_three.session)

    assert limited.order.status is UserOrderStatus.BRIEF_READY
    assert limited.session.remaining_dimensions == ()
    assert any("persist locally" in item for item in limited.session.assumptions)


def test_pdf_brief_contains_expected_features_non_goals_and_verifiable_acceptance():
    _, _, _, completed, brief = _resolved_reference()
    features = " | ".join(brief.core_features).casefold()
    non_goals = " | ".join(brief.non_goals).casefold()
    criteria = " | ".join(brief.acceptance_criteria).casefold()

    assert brief.order_id == completed.order.id
    assert brief.goal == "A browser application for conversational search across uploaded PDF documents using text and voice."
    for expected in (
        "pdf upload",
        "processing status",
        "document list",
        "document removal",
        "text extraction",
        "chunking",
        "grounded answers",
        "page citations",
        "typed questions",
        "push-to-talk",
        "speech output",
        "stop speech",
        "honest no-answer",
    ):
        assert expected in features
    for expected in ("ocr", "handwritten", "word and excel", "continuous listening", "wake words", "voice cloning", "multi-user", "cloud synchronization", "mobile", "desktop packaging"):
        assert expected in non_goals
    for expected in ("upload a text-based pdf", "processing status", "typed question", "grounded only", "page reference", "do not contain an answer", "push-to-talk", "recognized text", "spoken aloud", "stop speech", "actionable blockers", "stack traces"):
        assert expected in criteria
    assert not any("works well" in item.casefold() for item in brief.acceptance_criteria)
    assert brief.recommended_stack.frontend == "React + Vite"
    assert brief.recommended_stack.backend == "FastAPI"
    assert brief.elena_design_choice is ElenaDesignChoice.SHOW_ELENA_CONCEPT
    assert brief.elena_design_concept is not None
    assert brief.elena_design_concept.visual_direction == "Liquid Glass"


def test_explicit_pdf_answers_override_default_technical_assumptions():
    clarification, briefs, _ = _services()
    started = clarification.begin(_order())
    values = {
        "target-users": "Only me",
        "document-ocr": True,
        "speech-languages": ("English",),
        "document-persistence": False,
        "document-processing": "Cloud-assisted processing",
        "elena-design": "Proceed directly to implementation",
    }
    completed = clarification.apply_answers(
        started.order,
        started.session,
        tuple(ClarificationAnswer(question_id=item.id, value=values[item.id]) for item in started.order.questions),
    )
    brief = briefs.generate(completed.order, completed.session)
    features = " ".join(brief.core_features).casefold()
    non_goals = " ".join(brief.non_goals).casefold()
    technical = " ".join(brief.technical_constraints).casefold()

    assert "ocr for scanned" in features
    assert "persistent local document library" not in features
    assert "ocr and scanned" not in non_goals
    assert "cloud-assisted" in technical
    assert "speech recognition and synthesis locales: english" in technical
    assert brief.elena_design_choice is ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT
    assert brief.elena_design_concept is None


def test_brief_revision_is_new_immutable_version_and_approval_is_version_bound():
    _, briefs, _, _, original = _resolved_reference()
    old_binding = briefs.prepare_approval(original)
    revised, history = briefs.revise(
        original,
        BriefRevisionRequest(
            operations=(
                BriefRevisionOperation(kind=BriefRevisionKind.ADD_REQUIREMENT, value="Keyboard shortcut for push-to-talk"),
                BriefRevisionOperation(
                    kind=BriefRevisionKind.CHANGE_ELENA_CHOICE,
                    elena_choice=ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT,
                ),
            )
        ),
    )

    assert original.revision == 1
    assert "Keyboard shortcut for push-to-talk" not in original.core_features
    assert revised.revision == 2
    assert revised.id != original.id
    assert revised.approved_at is None
    assert history.previous_brief_id == original.id
    assert history.revised_brief_id == revised.id
    with pytest.raises(BriefServiceError) as stale:
        briefs.approve(revised, old_binding)
    assert stale.value.code == "approval_binding_stale"
    approved = briefs.approve(revised, briefs.prepare_approval(revised))
    assert approved.approved_at == NOW + timedelta(minutes=1)

    removed, _ = briefs.revise(
        revised,
        BriefRevisionRequest(
            operations=(
                BriefRevisionOperation(
                    kind=BriefRevisionKind.REMOVE_REQUIREMENT,
                    previous_value="Keyboard shortcut for push-to-talk",
                ),
            )
        ),
    )
    assert "Keyboard shortcut for push-to-talk" not in removed.core_features
    assert not any("Keyboard shortcut for push-to-talk" in item for item in removed.acceptance_criteria)


def test_unsupported_or_secret_revision_is_rejected():
    _, briefs, _, _, brief = _resolved_reference()
    with pytest.raises(ValidationError):
        BriefRevisionRequest.model_validate({"operations": [{"kind": "replace_structure", "value": "anything"}]})
    with pytest.raises(BriefServiceError) as secret:
        briefs.revise(
            brief,
            BriefRevisionRequest(
                operations=(BriefRevisionOperation(kind="add_requirement", value="API_KEY=sk-abcdefghijklmnop"),)
            ),
        )
    assert secret.value.code == "secret_like_content_rejected"
    with pytest.raises(BriefServiceError):
        briefs.revise(
            brief,
            BriefRevisionRequest(
                operations=(BriefRevisionOperation(kind="add_requirement", value="Authorization: Bearer abcdefghijklmnop"),)
            ),
        )


def test_ui_and_non_ui_projects_receive_correct_elena_contract():
    service = AlexClarificationService(clock=lambda: NOW)
    ui = service.begin(_order())
    assert "elena-design" in {item.id for item in ui.order.questions}

    no_ui_order = _order(
        "Create an API-only backend for only me to create, list, update, and delete local records with no user interface."
    )
    no_ui = service.begin(no_ui_order)
    assert no_ui.session.elena_choice is ElenaDesignChoice.NOT_APPLICABLE
    assert "elena-design" not in {item.id for item in no_ui.order.questions}


def test_undecided_elena_and_open_questions_block_approval_or_handoff():
    _, briefs, handoffs, _, brief = _resolved_reference()
    undecided = brief.model_copy(
        update={"elena_design_choice": ElenaDesignChoice.UNDECIDED, "elena_design_concept": None}
    )
    with pytest.raises(BriefServiceError) as decision:
        briefs.prepare_approval(undecided)
    assert decision.value.code == "elena_choice_required"

    open_brief = brief.model_copy(update={"open_questions": ("Choose a deployment target",)})
    with pytest.raises(BriefServiceError) as open_error:
        briefs.prepare_approval(open_brief)
    assert open_error.value.code == "brief_has_open_questions"
    with pytest.raises(BriefServiceError):
        handoffs.create_implementation_handoff(brief)


def test_approved_handoff_is_compact_deterministic_and_preserves_contract():
    _, briefs, handoffs, _, brief = _resolved_reference()
    approved = briefs.approve(brief, briefs.prepare_approval(brief))
    from order_workflow import DesignPreviewService

    design_service = DesignPreviewService(id_factory=lambda: "clarification-preview", clock=lambda: NOW)
    preview = design_service.approve(design_service.generate(approved), approved)
    handoff = handoffs.create_implementation_handoff(approved, preview)
    serialized = handoff.to_json()

    assert handoff.source_agent == "alex"
    assert handoff.target_agent == "codex"
    assert handoff.requested_action == "implement"
    assert handoff.requirements == approved.core_features
    assert handoff.acceptance_criteria == approved.acceptance_criteria
    assert handoff.open_questions == ()
    assert len(handoff.context_summary) < 1_000
    assert "chat_history" not in serialized
    assert "api_key" not in serialized.casefold()
    assert handoff.to_json() == handoff.to_json()

    forged = approved.model_copy(update={"approval_fingerprint": "0" * 64})
    with pytest.raises(BriefServiceError) as forged_error:
        handoffs.create_implementation_handoff(forged)
    assert forged_error.value.code == "brief_not_approved"


def test_small_question_limit_does_not_discard_unasked_mandatory_dimensions():
    service = AlexClarificationService(clock=lambda: NOW, max_questions_per_round=1)
    started = service.begin(_order("", title="Small app"))
    defaulted = service.use_recommended_defaults(started.order, started.session)

    assert defaulted.order.status is UserOrderStatus.CLARIFICATION_REQUIRED
    assert "core_features" in {item.value for item in defaulted.session.remaining_dimensions}


def test_empty_order_defaults_do_not_invent_missing_mandatory_features():
    service = AlexClarificationService(clock=lambda: NOW)
    started = service.begin(_order("", title="Small app"))
    defaulted = service.use_recommended_defaults(started.order, started.session)

    assert defaulted.order.status is UserOrderStatus.CLARIFICATION_REQUIRED
    assert "core_features" in {item.value for item in defaulted.session.remaining_dimensions}


def test_reference_title_is_not_hard_coded_in_workflow_services():
    root = Path(__file__).parent / "order_workflow"
    source = "\n".join((root / name).read_text(encoding="utf-8") for name in ("clarification.py", "brief_service.py", "handoffs.py"))
    assert "PDF Voice Assistant" not in source


# --- an answer listing several capabilities becomes several features --------------------


def test_a_semicolon_separated_answer_becomes_one_feature_each():
    from order_workflow.brief_service import _split_capabilities

    # The real answer that shipped a four-in-one "feature": the core-feature prompt says
    # "wire in exactly ONE central feature" and was handed all four at once.
    answer = (
        "Start, pause, and reset the 25-minute timer; show a live countdown ring; "
        "fire a desktop notification on completion; and log today's completed sessions."
    )

    features = _split_capabilities(answer)

    assert len(features) == 4
    assert features[0] == "Start, pause, and reset the 25-minute timer"
    assert features[3].startswith("log today")  # the joining "and" is dropped


def test_commas_and_and_are_not_treated_as_separators():
    from order_workflow.brief_service import _split_capabilities

    # "start, pause, and reset the timer" is one capability with three verbs. Splitting it
    # would invent features nobody asked for.
    assert _split_capabilities("Start, pause, and reset the timer") == ("Start, pause, and reset the timer",)


def test_a_bulleted_answer_is_split_on_the_bullets():
    from order_workflow.brief_service import _split_capabilities

    features = _split_capabilities("- Add a book\n- Mark it finished\n- See the count")

    assert features == ("Add a book", "Mark it finished", "See the count")


def test_a_capability_too_long_for_its_field_is_cut_at_a_word(  ):
    """The reading-journal order's only feature ended "...three shelves: Want to re" -- a
    mid-word stump that goes into the delivery report's "What was built", into the build
    prompt as a requirement, and into the acceptance criteria written from it."""
    from order_workflow.brief_service import _MAX_FEATURE_CHARS, _split_capabilities

    prose = "A personal reading journal that lives in one browser. " + "I add a book by typing its title and page count and it goes onto a shelf. " * 6

    feature = _split_capabilities(prose)[0]

    assert len(feature) <= _MAX_FEATURE_CHARS
    assert feature.endswith("…")
    assert not feature[:-1].endswith(" ")
    # The cut lands between words: the last word before it is whole.
    assert prose.startswith(feature[:-1])


def test_a_capability_that_fits_is_left_exactly_as_it_is():
    from order_workflow.brief_service import _split_capabilities

    assert _split_capabilities("Add a book to a shelf") == ("Add a book to a shelf",)


def test_punctuation_noise_is_not_mistaken_for_a_feature():
    from order_workflow.brief_service import _split_capabilities

    assert _split_capabilities("Add a book;;; ; mark it read") == ("Add a book", "mark it read")


def test_each_capability_gets_its_own_acceptance_criterion():
    from order_workflow.brief_service import _generic_acceptance, _split_capabilities

    features = _split_capabilities("Add a book; mark it finished; see the monthly count")

    assert len(_generic_acceptance(features)) == 3
