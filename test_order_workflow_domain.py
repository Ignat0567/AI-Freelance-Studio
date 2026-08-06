from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from order_workflow import (
    AgentHandoff,
    ArtifactKind,
    ClarificationAnswer,
    ClarificationQuestion,
    ElenaDesignChoice,
    EventKind,
    ExecutionEvent,
    ExecutionArtifact,
    ExecutionResult,
    ExecutionStage,
    ExecutionStatus,
    InvalidWorkflowTransition,
    ProjectBrief,
    ProjectExecution,
    QuestionType,
    RecommendedStack,
    UserOrder,
    UserOrderStatus,
    append_bounded_event,
    new_public_id,
    utc_now,
    validate_execution_stage_transition,
    validate_execution_transition,
    validate_order_transition,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _question() -> ClarificationQuestion:
    return ClarificationQuestion(
        id="target-users",
        text="Who will use this application?",
        type=QuestionType.SINGLE_SELECT,
        options=("Only me", "My customers"),
        required=True,
        reason="This determines access requirements.",
        recommended_answer="Only me",
    )


def _order(**changes) -> UserOrder:
    values = {
        "id": "order_fixed",
        "title": "PDF Voice Assistant",
        "description": "Create a browser-based voice assistant for questions about uploaded PDF documents.",
        "product_type": "web_app",
        "preferred_language": "en",
        "questions": (_question(),),
        "answers": (ClarificationAnswer(question_id="target-users", value="Only me"),),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return UserOrder(**values)


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_fixed",
        "order_id": "order_fixed",
        "goal": "Provide conversational search across uploaded PDF documents.",
        "target_users": ("Single local user",),
        "core_features": ("PDF upload", "Document-grounded answers with citations"),
        "acceptance_criteria": ("A user can upload a text-based PDF.",),
        "recommended_stack": RecommendedStack(),
        "elena_design_choice": ElenaDesignChoice.PROCEED_DIRECTLY,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProjectBrief(**values)


def test_user_order_is_strict_serializable_and_copy_safe():
    order = _order(constraints=("No OCR in the first version",))

    restored = UserOrder.from_json(order.to_json())

    assert restored == order
    assert restored.to_dict()["product_type"] == "web_app"
    assert restored.to_json() == order.to_json()
    with pytest.raises(ValidationError):
        UserOrder(**{**order.to_dict(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        order.title = "Changed"


def test_only_supported_product_type_is_accepted():
    with pytest.raises(ValidationError):
        _order(product_type="desktop_app")


def test_order_timestamps_require_timezone_and_valid_order():
    with pytest.raises(ValidationError):
        _order(created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _order(updated_at=NOW - timedelta(seconds=1))


def test_answer_must_match_exactly_one_question():
    with pytest.raises(ValidationError):
        _order(answers=(ClarificationAnswer(question_id="unknown", value=True),))
    duplicate = ClarificationAnswer(question_id="target-users", value="Only me")
    with pytest.raises(ValidationError):
        _order(answers=(duplicate, duplicate))
    with pytest.raises(ValidationError):
        _order(answers=(ClarificationAnswer(question_id="target-users", value="Unknown audience"),))


def test_question_contract_validates_options_and_recommendation():
    with pytest.raises(ValidationError):
        ClarificationQuestion(
            id="invalid",
            text="Select a mode",
            type="single_select",
            options=("Only one",),
            reason="The mode changes implementation.",
        )
    with pytest.raises(ValidationError):
        ClarificationQuestion(
            id="invalid",
            text="Enable storage?",
            type="boolean",
            options=(),
            reason="Storage changes implementation.",
            recommended_answer="yes",
        )


def test_brief_requires_explicit_consistent_elena_choice():
    assert _brief().elena_design_choice is ElenaDesignChoice.PROCEED_DIRECTLY
    with pytest.raises(ValidationError):
        _brief(elena_design_choice="show_concept")


def test_handoff_is_compact_and_rejects_raw_chat_history():
    handoff = AgentHandoff(
        id="handoff_fixed",
        order_id="order_fixed",
        brief_id="brief_fixed",
        source_agent="alex",
        target_agent="codex",
        goal="Implement the approved PDF assistant brief.",
        context_summary="Single-user web app with grounded PDF question answering.",
        requirements=("Show document and page citations",),
        acceptance_criteria=("A typed question returns a grounded answer.",),
        created_at=NOW,
    )

    assert "chat" not in handoff.to_json().lower()
    with pytest.raises(ValidationError):
        AgentHandoff(**{**handoff.to_dict(), "chat_history": ["secret transcript"]})


def test_public_ids_support_deterministic_injected_factory():
    assert new_public_id("order", lambda: "fixture-001") == "order_fixture-001"
    with pytest.raises(ValueError):
        new_public_id("Invalid Prefix", lambda: "fixture")


def test_clock_is_injectable_and_rejects_naive_time():
    assert utc_now(lambda: NOW) == NOW
    with pytest.raises(ValueError):
        utc_now(lambda: NOW.replace(tzinfo=None))


def test_order_state_transitions_are_explicit_and_terminal_states_absorb():
    validate_order_transition(UserOrderStatus.DRAFT, UserOrderStatus.CLARIFICATION_REQUIRED)
    validate_order_transition(UserOrderStatus.AWAITING_APPROVAL, UserOrderStatus.APPROVED)
    validate_order_transition(UserOrderStatus.RUNNING, UserOrderStatus.SUCCEEDED)

    with pytest.raises(InvalidWorkflowTransition) as error:
        validate_order_transition(UserOrderStatus.DRAFT, UserOrderStatus.RUNNING)
    assert error.value.code == "invalid_workflow_transition"
    with pytest.raises(InvalidWorkflowTransition):
        validate_order_transition(UserOrderStatus.SUCCEEDED, UserOrderStatus.RUNNING)
    with pytest.raises(InvalidWorkflowTransition):
        validate_order_transition("draft", "brief_ready")


def test_execution_state_and_stage_transitions_include_repair_loop():
    validate_execution_transition(ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
    validate_execution_transition(ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED)
    validate_execution_stage_transition(ExecutionStage.VERIFICATION, ExecutionStage.REPAIR)
    validate_execution_stage_transition(ExecutionStage.REPAIR, ExecutionStage.VERIFICATION)

    with pytest.raises(InvalidWorkflowTransition):
        validate_execution_transition(ExecutionStatus.SUCCEEDED, ExecutionStatus.RUNNING)
    with pytest.raises(InvalidWorkflowTransition):
        validate_execution_stage_transition(ExecutionStage.PLANNING, ExecutionStage.COMPLETED)


def test_phased_pipeline_stage_transitions():
    validate_execution_stage_transition(ExecutionStage.PLANNING, ExecutionStage.UI_SHELL)
    validate_execution_stage_transition(ExecutionStage.UI_SHELL, ExecutionStage.CORE_FEATURE)
    validate_execution_stage_transition(ExecutionStage.CORE_FEATURE, ExecutionStage.BACKEND_DECISION)
    validate_execution_stage_transition(ExecutionStage.BACKEND_DECISION, ExecutionStage.COMPLETED)
    validate_execution_stage_transition(ExecutionStage.BACKEND_DECISION, ExecutionStage.IMPLEMENTATION)

    with pytest.raises(InvalidWorkflowTransition):
        validate_execution_stage_transition(ExecutionStage.UI_SHELL, ExecutionStage.BACKEND_DECISION)
    with pytest.raises(InvalidWorkflowTransition):
        validate_execution_stage_transition(ExecutionStage.CORE_FEATURE, ExecutionStage.COMPLETED)


def test_bounded_events_keep_newest_immutable_records():
    execution_id = "execution_fixed"
    events = ()
    for index in range(5):
        events = append_bounded_event(
            events,
            ExecutionEvent(
                id=f"event_fixed_{index}",
                execution_id=execution_id,
                kind=EventKind.ACTIVITY,
                message=f"Activity {index}",
                stage=ExecutionStage.PLANNING,
                agent="alex",
                progress=index * 10,
                created_at=NOW + timedelta(seconds=index),
            ),
            limit=3,
        )

    assert tuple(event.message for event in events) == ("Activity 2", "Activity 3", "Activity 4")
    with pytest.raises(ValueError):
        append_bounded_event(events, events[-1], limit=0)


def test_event_retention_rejects_cross_execution_data():
    first = ExecutionEvent(
        id="event_first",
        execution_id="execution_first",
        kind="status",
        message="Queued",
        created_at=NOW,
    )
    second = ExecutionEvent(
        id="event_second",
        execution_id="execution_second",
        kind="status",
        message="Running",
        created_at=NOW,
    )
    with pytest.raises(ValueError):
        append_bounded_event((first,), second)


def test_project_execution_round_trip_requires_consistent_terminal_result():
    artifact = ExecutionArtifact(
        id="artifact_summary",
        execution_id="execution_fixed",
        kind=ArtifactKind.PROJECT_SUMMARY,
        name="Generated project summary",
        summary="Simulated project output for UI development.",
        reference="artifact-summary",
        simulated=True,
        created_at=NOW,
    )
    result = ExecutionResult(
        success=True,
        summary="Fake execution completed successfully.",
        artifact_ids=(artifact.id,),
        completed_at=NOW + timedelta(minutes=1),
    )
    execution = ProjectExecution(
        id="execution_fixed",
        order_id="order_fixed",
        brief_id="brief_fixed",
        handoff_id="handoff_fixed",
        mode="fake",
        status="succeeded",
        stage="completed",
        progress=100,
        current_activity="Execution completed",
        artifacts=(artifact,),
        result=result,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=1),
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=1),
    )

    assert ProjectExecution.from_json(execution.to_json()) == execution
    with pytest.raises(ValidationError):
        execution.model_copy(update={"progress": 0})
    with pytest.raises(ValidationError):
        ProjectExecution(**{**execution.to_dict(), "status": "failed"})
    with pytest.raises(ValidationError):
        ExecutionArtifact(**{**artifact.to_dict(), "reference": "..\\secrets.env"})


@pytest.mark.parametrize("terminal", [UserOrderStatus.SUCCEEDED, UserOrderStatus.FAILED, UserOrderStatus.CANCELLED])
def test_every_order_terminal_state_is_absorbing(terminal):
    with pytest.raises(InvalidWorkflowTransition):
        validate_order_transition(terminal, UserOrderStatus.RUNNING)


@pytest.mark.parametrize("terminal", [ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED])
def test_every_execution_terminal_state_is_absorbing(terminal):
    with pytest.raises(InvalidWorkflowTransition):
        validate_execution_transition(terminal, ExecutionStatus.RUNNING)
