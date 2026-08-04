from __future__ import annotations

from typing import Literal

from .brief_service import sanitize_public_text
from .models import ExecutionBlocker, ExecutionMode, StrictDomainModel


ReadinessStatus = Literal["ready", "blocked", "missing", "unavailable"]


class ExecutionReadinessCheck(StrictDomainModel):
    code: str
    label: str
    status: ReadinessStatus
    message: str
    action: str | None = None


class ExecutionReadinessBlocker(StrictDomainModel):
    code: str
    message: str
    action: str
    severity: Literal["blocking"] = "blocking"


class ExecutionReadinessView(StrictDomainModel):
    ready: bool
    mode: ExecutionMode
    simulation_ready: bool
    production_dry_run_ready: bool
    production_live_ready: bool
    can_run_simulation: bool
    can_prepare_dry_run: bool
    can_run_live: bool
    blockers: tuple[ExecutionReadinessBlocker, ...]
    checks: tuple[ExecutionReadinessCheck, ...]


def readiness_blocker_view(blocker: ExecutionBlocker, *, code: str | None = None) -> ExecutionReadinessBlocker:
    return ExecutionReadinessBlocker(
        code=code or blocker.code,
        message=sanitize_public_text(blocker.message),
        action=sanitize_public_text(blocker.action),
    )


def readiness_check(code: str, label: str, status: ReadinessStatus, message: str, action: str | None = None) -> ExecutionReadinessCheck:
    return ExecutionReadinessCheck(
        code=code,
        label=label,
        status=status,
        message=sanitize_public_text(message),
        action=sanitize_public_text(action) if action else None,
    )
