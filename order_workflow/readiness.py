from __future__ import annotations

from .models import ExecutionBlocker, StrictDomainModel


class ReadinessResult(StrictDomainModel):
    ready: bool
    blockers: tuple[ExecutionBlocker, ...] = ()

    @classmethod
    def ready_result(cls) -> "ReadinessResult":
        return cls(ready=True)

    @classmethod
    def blocked(cls, *blockers: ExecutionBlocker) -> "ReadinessResult":
        return cls(ready=False, blockers=tuple(blockers))


def readiness_blocker(code: str, message: str, action: str = "Open Settings") -> ExecutionBlocker:
    return ExecutionBlocker(code=code, message=message, action=action, action_required=True)


PRODUCTION_NOT_CONFIGURED = readiness_blocker(
    "execution_provider_not_configured",
    "Select and configure a coding provider before starting execution.",
)

OPENCODE_UNAVAILABLE = readiness_blocker(
    "opencode_unavailable",
    "OpenCode is not available.",
)

WORKSPACE_UNAVAILABLE = readiness_blocker(
    "workspace_unavailable",
    "A writable project workspace is not available.",
)

UNSUPPORTED_PRODUCT_TYPE = readiness_blocker(
    "unsupported_product_type",
    "This MVP currently supports only small browser-based web applications.",
    "Revise Brief",
)

BRIEF_NOT_APPROVED = readiness_blocker(
    "brief_not_approved",
    "Approve the current project brief before starting execution.",
    "Review Brief",
)

UNRESOLVED_QUESTIONS = readiness_blocker(
    "unresolved_questions",
    "Resolve open questions or convert them into visible assumptions before execution.",
    "Answer Questions",
)

ELENA_CHOICE_REQUIRED = readiness_blocker(
    "elena_choice_required",
    "Choose whether Elena should prepare a design concept before implementation.",
    "Review Brief",
)
