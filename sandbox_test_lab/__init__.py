"""Independent Windows Sandbox test harness for AI Freelance Studio."""

from .capability import detect_sandbox_capability
from .installer import (
    ApplicationTestRequest,
    GuestOutcome,
    GuestPhase,
    InstallationPlan,
    InstallationRecipe,
    InstallerKind,
    plan_installation,
)
from .models import (
    RunStatus,
    SandboxCapability,
    SandboxRunPaths,
    SandboxRunRequest,
    SandboxRunResult,
)
from .runner import SandboxRunner

__all__ = [
    "RunStatus",
    "ApplicationTestRequest",
    "GuestOutcome",
    "GuestPhase",
    "InstallationPlan",
    "InstallationRecipe",
    "InstallerKind",
    "SandboxCapability",
    "SandboxRunPaths",
    "SandboxRunRequest",
    "SandboxRunResult",
    "SandboxRunner",
    "detect_sandbox_capability",
    "plan_installation",
]
