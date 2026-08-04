from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class WorkflowState(StrEnum):
    DRAFT = "draft"
    REQUIREMENTS = "requirements"
    SPECIFICATION = "specification"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    PREFLIGHT = "preflight"
    READY = "ready"
    EXECUTING = "executing"
    TESTING = "testing"
    REPAIRING = "repairing"
    RETESTING = "retesting"
    BROWSER_VALIDATION = "browser_validation"
    PRODUCT_REVIEW = "product_review"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.DRAFT: {WorkflowState.REQUIREMENTS, WorkflowState.CANCELLED},
    WorkflowState.REQUIREMENTS: {WorkflowState.SPECIFICATION, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.SPECIFICATION: {WorkflowState.PLANNING, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.PLANNING: {WorkflowState.AWAITING_PLAN_APPROVAL, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.AWAITING_PLAN_APPROVAL: {WorkflowState.PREFLIGHT, WorkflowState.PLANNING, WorkflowState.CANCELLED},
    WorkflowState.PREFLIGHT: {WorkflowState.READY, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.READY: {WorkflowState.EXECUTING, WorkflowState.CANCELLED},
    WorkflowState.EXECUTING: {WorkflowState.TESTING, WorkflowState.BLOCKED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.TESTING: {WorkflowState.REPAIRING, WorkflowState.BROWSER_VALIDATION, WorkflowState.PRODUCT_REVIEW, WorkflowState.FAILED, WorkflowState.PARTIALLY_COMPLETED, WorkflowState.CANCELLED},
    WorkflowState.REPAIRING: {WorkflowState.RETESTING, WorkflowState.FAILED, WorkflowState.PARTIALLY_COMPLETED, WorkflowState.CANCELLED},
    WorkflowState.RETESTING: {WorkflowState.BROWSER_VALIDATION, WorkflowState.PRODUCT_REVIEW, WorkflowState.REPAIRING, WorkflowState.FAILED, WorkflowState.PARTIALLY_COMPLETED, WorkflowState.CANCELLED},
    WorkflowState.BROWSER_VALIDATION: {WorkflowState.PRODUCT_REVIEW, WorkflowState.REPAIRING, WorkflowState.FAILED, WorkflowState.PARTIALLY_COMPLETED, WorkflowState.CANCELLED},
    WorkflowState.PRODUCT_REVIEW: {WorkflowState.COMPLETED, WorkflowState.REPAIRING, WorkflowState.FAILED, WorkflowState.PARTIALLY_COMPLETED, WorkflowState.CANCELLED},
    WorkflowState.BLOCKED: {WorkflowState.PREFLIGHT, WorkflowState.CANCELLED},
    WorkflowState.FAILED: {WorkflowState.PREFLIGHT, WorkflowState.CANCELLED},
    WorkflowState.PARTIALLY_COMPLETED: {WorkflowState.PREFLIGHT, WorkflowState.CANCELLED},
    WorkflowState.COMPLETED: set(),
    WorkflowState.CANCELLED: set(),
}


def validate_transition(current: str | WorkflowState, target: str | WorkflowState) -> bool:
    current_state = WorkflowState(current)
    target_state = WorkflowState(target)
    return target_state in ALLOWED_TRANSITIONS[current_state]


@dataclass
class ExecutionBrief:
    project_id: str
    task_id: str
    title: str
    objective: str
    project_root: str
    requirements: list[str]
    constraints: list[str]
    acceptance_criteria: list[str]
    allowed_paths: list[str]
    forbidden_paths: list[str]
    implementation_steps: list[str]
    test_commands: list[str]
    validation_commands: list[str]
    requires_browser_validation: bool
    requires_security_review: bool
    approval_policy: str
    sandbox_policy: str
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    def validate(self) -> list[str]:
        errors = []
        for field_name in ("project_id", "task_id", "title", "objective", "project_root"):
            if not str(getattr(self, field_name) or "").strip():
                errors.append(f"{field_name}_required")
        if not self.acceptance_criteria:
            errors.append("acceptance_criteria_required")
        if not self.test_commands:
            errors.append("test_commands_required")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DevelopmentTask:
    id: str
    title: str
    description: str
    dependencies: list[str]
    acceptance_criteria: list[str]
    expected_files: list[str]
    test_commands: list[str]
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
