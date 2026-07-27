from __future__ import annotations

from .models import ExecutionStage, ExecutionStatus, UserOrderStatus


class InvalidWorkflowTransition(ValueError):
    """Stable domain error for invalid public workflow transitions."""

    code = "invalid_workflow_transition"


_ORDER_TRANSITIONS: dict[UserOrderStatus, frozenset[UserOrderStatus]] = {
    UserOrderStatus.DRAFT: frozenset({UserOrderStatus.CLARIFICATION_REQUIRED, UserOrderStatus.BRIEF_READY}),
    UserOrderStatus.CLARIFICATION_REQUIRED: frozenset({UserOrderStatus.CLARIFICATION_REQUIRED, UserOrderStatus.BRIEF_READY}),
    UserOrderStatus.BRIEF_READY: frozenset({UserOrderStatus.AWAITING_APPROVAL, UserOrderStatus.CLARIFICATION_REQUIRED}),
    UserOrderStatus.AWAITING_APPROVAL: frozenset({UserOrderStatus.APPROVED, UserOrderStatus.CLARIFICATION_REQUIRED}),
    UserOrderStatus.APPROVED: frozenset({UserOrderStatus.QUEUED, UserOrderStatus.AWAITING_USER, UserOrderStatus.CANCELLED}),
    UserOrderStatus.QUEUED: frozenset({UserOrderStatus.RUNNING, UserOrderStatus.AWAITING_USER, UserOrderStatus.FAILED, UserOrderStatus.CANCELLED}),
    UserOrderStatus.RUNNING: frozenset({UserOrderStatus.AWAITING_USER, UserOrderStatus.SUCCEEDED, UserOrderStatus.FAILED, UserOrderStatus.CANCELLED}),
    UserOrderStatus.AWAITING_USER: frozenset({UserOrderStatus.APPROVED, UserOrderStatus.QUEUED, UserOrderStatus.RUNNING, UserOrderStatus.FAILED, UserOrderStatus.CANCELLED}),
}

_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.QUEUED: frozenset({ExecutionStatus.RUNNING, ExecutionStatus.AWAITING_USER, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}),
    ExecutionStatus.RUNNING: frozenset({ExecutionStatus.AWAITING_USER, ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}),
    ExecutionStatus.AWAITING_USER: frozenset({ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}),
}

_STAGE_TRANSITIONS: dict[ExecutionStage, frozenset[ExecutionStage]] = {
    ExecutionStage.REQUIREMENTS: frozenset({ExecutionStage.DESIGN, ExecutionStage.PLANNING}),
    ExecutionStage.DESIGN: frozenset({ExecutionStage.PLANNING}),
    ExecutionStage.PLANNING: frozenset({ExecutionStage.IMPLEMENTATION}),
    ExecutionStage.IMPLEMENTATION: frozenset({ExecutionStage.VERIFICATION}),
    ExecutionStage.VERIFICATION: frozenset({ExecutionStage.REPAIR, ExecutionStage.PACKAGING, ExecutionStage.COMPLETED}),
    ExecutionStage.REPAIR: frozenset({ExecutionStage.VERIFICATION, ExecutionStage.PACKAGING}),
    ExecutionStage.PACKAGING: frozenset({ExecutionStage.COMPLETED}),
}


def _validate_transition(current, target, allowed, enum_type, label: str) -> None:
    if type(current) is not enum_type or type(target) is not enum_type:
        raise InvalidWorkflowTransition(f"Invalid {label} transition value")
    if current == target:
        return
    if target not in allowed.get(current, frozenset()):
        raise InvalidWorkflowTransition(f"Invalid {label} transition: {current.value} -> {target.value}")


def validate_order_transition(current: UserOrderStatus, target: UserOrderStatus) -> None:
    _validate_transition(current, target, _ORDER_TRANSITIONS, UserOrderStatus, "order")


def validate_execution_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    _validate_transition(current, target, _EXECUTION_TRANSITIONS, ExecutionStatus, "execution")


def validate_execution_stage_transition(current: ExecutionStage, target: ExecutionStage) -> None:
    _validate_transition(current, target, _STAGE_TRANSITIONS, ExecutionStage, "execution stage")
