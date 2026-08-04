"""Independent Windows Sandbox test harness for AI Freelancer Studio."""

from importlib import import_module
from typing import Any


_LAZY_EXPORTS = {
    "ApplicationTestRequest": (".installer", "ApplicationTestRequest"),
    "GuestOutcome": (".installer", "GuestOutcome"),
    "GuestPhase": (".installer", "GuestPhase"),
    "InstallationPlan": (".installer", "InstallationPlan"),
    "InstallationRecipe": (".installer", "InstallationRecipe"),
    "InstallerKind": (".installer", "InstallerKind"),
    "RunStatus": (".models", "RunStatus"),
    "SandboxAvailability": (".adapter", "SandboxAvailability"),
    "SandboxCapability": (".models", "SandboxCapability"),
    "SandboxCheckCode": (".adapter", "SandboxCheckCode"),
    "SandboxDiagnosticCode": (".adapter", "SandboxDiagnosticCode"),
    "SandboxProfile": (".adapter", "SandboxProfile"),
    "SandboxProgress": (".adapter", "SandboxProgress"),
    "SandboxReadiness": (".adapter", "SandboxReadiness"),
    "SandboxRepairHandoff": (".adapter", "SandboxRepairHandoff"),
    "SandboxRunPaths": (".models", "SandboxRunPaths"),
    "SandboxRunRequest": (".models", "SandboxRunRequest"),
    "SandboxRunResult": (".models", "SandboxRunResult"),
    "SandboxRunner": (".runner", "SandboxRunner"),
    "SandboxStatus": (".adapter", "SandboxStatus"),
    "SandboxTestLabAdapter": (".adapter", "SandboxTestLabAdapter"),
    "SandboxTestLabDisabledError": (".adapter", "SandboxTestLabDisabledError"),
    "SandboxTestLabResult": (".adapter", "SandboxTestLabResult"),
    "SandboxTestLabRunner": (".adapter", "SandboxTestLabRunner"),
    "SandboxTestLabUnavailableError": (".adapter", "SandboxTestLabUnavailableError"),
    "ValidatedSandboxCheck": (".adapter", "ValidatedSandboxCheck"),
    "detect_sandbox_capability": (".capability", "detect_sandbox_capability"),
    "plan_installation": (".installer", "plan_installation"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
