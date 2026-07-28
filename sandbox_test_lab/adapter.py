from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable, Protocol

from .capability import detect_sandbox_capability
from .models import SandboxCapability, validate_run_id


class SandboxTestLabError(RuntimeError):
    """Base error for the portable Test Lab boundary."""


class SandboxTestLabDisabledError(SandboxTestLabError):
    pass


class SandboxTestLabUnavailableError(SandboxTestLabError):
    pass


class SandboxProfile(str, Enum):
    PRODUCTION_SELF_TEST = "production_self_test"
    PRODUCTION_SCREENSHOT = "production_screenshot"


class SandboxStatus(str, Enum):
    PREPARED = "prepared"
    LAUNCHING = "launching"
    RUNNING = "running"
    CANCELLING = "cancelling"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class SandboxReadiness(str, Enum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    NOT_READY = "not_ready"
    READY = "ready"


class SandboxProgress(str, Enum):
    NOT_STARTED = "not_started"
    PREPARING = "preparing"
    LAUNCHING = "launching"
    RUNNING_CHECKS = "running_checks"
    COLLECTING_EVIDENCE = "collecting_evidence"
    CANCELLING = "cancelling"
    COMPLETE = "complete"


class SandboxDiagnosticCode(str, Enum):
    SANDBOX_TEST_LAB_DISABLED = "sandbox_test_lab_disabled"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    SANDBOX_CAPABILITY_WARNING = "sandbox_capability_warning"
    SANDBOX_RUNNER_NOT_CONFIGURED = "sandbox_runner_not_configured"
    MANUAL_CLOSE_PENDING = "manual_close_pending"
    SANDBOX_OWNERSHIP_FAILED = "sandbox_ownership_failed"
    SANDBOX_GUEST_SUCCEEDED_OWNERSHIP_FAILED = "sandbox_guest_succeeded_ownership_failed"


class SandboxCheckCode(str, Enum):
    APPLICATION_READY = "application_ready"


_TERMINAL_STATUSES = frozenset(
    {
        SandboxStatus.PASSED,
        SandboxStatus.FAILED,
        SandboxStatus.TIMED_OUT,
        SandboxStatus.CANCELLED,
        SandboxStatus.INFRASTRUCTURE_ERROR,
    }
)

_ALLOWED_STATUS_TRANSITIONS: dict[SandboxStatus, frozenset[SandboxStatus]] = {
    SandboxStatus.PREPARED: frozenset(
        {
            SandboxStatus.PREPARED,
            SandboxStatus.LAUNCHING,
            SandboxStatus.RUNNING,
            SandboxStatus.FAILED,
            SandboxStatus.TIMED_OUT,
            SandboxStatus.CANCELLED,
            SandboxStatus.INFRASTRUCTURE_ERROR,
        }
    ),
    SandboxStatus.LAUNCHING: frozenset(
        {
            SandboxStatus.LAUNCHING,
            SandboxStatus.RUNNING,
            SandboxStatus.CANCELLING,
            SandboxStatus.PASSED,
            SandboxStatus.FAILED,
            SandboxStatus.TIMED_OUT,
            SandboxStatus.CANCELLED,
            SandboxStatus.INFRASTRUCTURE_ERROR,
        }
    ),
    SandboxStatus.RUNNING: frozenset(
        {
            SandboxStatus.RUNNING,
            SandboxStatus.CANCELLING,
            SandboxStatus.PASSED,
            SandboxStatus.FAILED,
            SandboxStatus.TIMED_OUT,
            SandboxStatus.CANCELLED,
            SandboxStatus.INFRASTRUCTURE_ERROR,
        }
    ),
    SandboxStatus.CANCELLING: frozenset(
        {
            SandboxStatus.CANCELLING,
            SandboxStatus.FAILED,
            SandboxStatus.TIMED_OUT,
            SandboxStatus.CANCELLED,
            SandboxStatus.INFRASTRUCTURE_ERROR,
        }
    ),
    **{status: frozenset() for status in _TERMINAL_STATUSES},
}

_ALLOWED_BACKEND_OPERATIONS: dict[SandboxStatus, frozenset[str]] = {
    SandboxStatus.PREPARED: frozenset({"launch", "status", "cancel"}),
    SandboxStatus.LAUNCHING: frozenset({"status", "evidence", "cancel"}),
    SandboxStatus.RUNNING: frozenset({"status", "evidence", "cancel"}),
    SandboxStatus.CANCELLING: frozenset({"status", "evidence"}),
    **{status: frozenset() for status in _TERMINAL_STATUSES},
}


def _validate_diagnostic_codes(values: tuple[SandboxDiagnosticCode, ...]) -> None:
    if type(values) is not tuple or any(type(value) is not SandboxDiagnosticCode for value in values):
        raise ValueError("diagnostic codes are invalid")


def _validate_exact_run_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("run_id must be an exact string")
    return validate_run_id(value)


def _validate_evidence_references(values: tuple[str, ...]) -> None:
    if type(values) is not tuple:
        raise ValueError("evidence references are invalid")
    try:
        for value in values:
            _validate_exact_run_id(value)
    except ValueError:
        raise ValueError("evidence references are invalid") from None


@dataclass(frozen=True, slots=True)
class ValidatedSandboxCheck:
    check_id: SandboxCheckCode
    passed: bool

    def __post_init__(self) -> None:
        if type(self.check_id) is not SandboxCheckCode:
            raise ValueError("check code is invalid")
        if type(self.passed) is not bool:
            raise ValueError("passed must be boolean")


@dataclass(frozen=True, slots=True)
class SandboxAvailability:
    available: bool
    readiness: SandboxReadiness
    errors: tuple[SandboxDiagnosticCode, ...] = ()
    warnings: tuple[SandboxDiagnosticCode, ...] = ()

    def __post_init__(self) -> None:
        if type(self.available) is not bool or type(self.readiness) is not SandboxReadiness:
            raise ValueError("availability fields are invalid")
        _validate_diagnostic_codes(self.errors)
        _validate_diagnostic_codes(self.warnings)

    @classmethod
    def disabled(cls) -> "SandboxAvailability":
        return cls(
            False,
            SandboxReadiness.DISABLED,
            errors=(SandboxDiagnosticCode.SANDBOX_TEST_LAB_DISABLED,),
        )


@dataclass(frozen=True, slots=True)
class SandboxTestLabResult:
    run_id: str
    profile: SandboxProfile
    status: SandboxStatus
    readiness: SandboxReadiness
    progress: SandboxProgress = SandboxProgress.NOT_STARTED
    validated_checks: tuple[ValidatedSandboxCheck, ...] = ()
    evidence_references: tuple[str, ...] = ()
    errors: tuple[SandboxDiagnosticCode, ...] = ()
    warnings: tuple[SandboxDiagnosticCode, ...] = ()
    manual_close_required: bool = False
    evidence_validated: bool = False

    def __post_init__(self) -> None:
        _validate_exact_run_id(self.run_id)
        if type(self.profile) is not SandboxProfile:
            raise ValueError("profile must be an allowed SandboxProfile")
        if type(self.status) is not SandboxStatus:
            raise ValueError("status must be a normalized SandboxStatus")
        if type(self.readiness) is not SandboxReadiness:
            raise ValueError("readiness must be a normalized SandboxReadiness")
        if type(self.progress) is not SandboxProgress:
            raise ValueError("progress must be a normalized SandboxProgress")
        if type(self.validated_checks) is not tuple or any(
            type(check) is not ValidatedSandboxCheck
            or type(check.check_id) is not SandboxCheckCode
            or type(check.passed) is not bool
            for check in self.validated_checks
        ):
            raise ValueError("validated_checks must contain ValidatedSandboxCheck values")
        _validate_evidence_references(self.evidence_references)
        _validate_diagnostic_codes(self.errors)
        _validate_diagnostic_codes(self.warnings)
        if type(self.manual_close_required) is not bool:
            raise ValueError("manual_close_required must be boolean")
        if type(self.evidence_validated) is not bool:
            raise ValueError("evidence_validated must be boolean")


@dataclass(frozen=True, slots=True)
class SandboxRepairHandoff:
    run_id: str
    profile: SandboxProfile
    status: SandboxStatus
    readiness: SandboxReadiness
    progress: SandboxProgress
    validated_checks: tuple[ValidatedSandboxCheck, ...]
    evidence_references: tuple[str, ...]
    errors: tuple[SandboxDiagnosticCode, ...]
    warnings: tuple[SandboxDiagnosticCode, ...]
    manual_close_required: bool
    evidence_validated: bool
    automatic_repair_allowed: bool = False

    def __post_init__(self) -> None:
        _validate_exact_run_id(self.run_id)
        if type(self.profile) is not SandboxProfile:
            raise ValueError("profile is invalid")
        if type(self.status) is not SandboxStatus:
            raise ValueError("status is invalid")
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("terminal status is required")
        if type(self.readiness) is not SandboxReadiness:
            raise ValueError("readiness is invalid")
        if type(self.progress) is not SandboxProgress:
            raise ValueError("progress is invalid")
        if type(self.validated_checks) is not tuple or any(
            type(check) is not ValidatedSandboxCheck
            or type(check.check_id) is not SandboxCheckCode
            or type(check.passed) is not bool
            for check in self.validated_checks
        ):
            raise ValueError("validated checks are invalid")
        _validate_evidence_references(self.evidence_references)
        _validate_diagnostic_codes(self.errors)
        _validate_diagnostic_codes(self.warnings)
        if type(self.manual_close_required) is not bool:
            raise ValueError("manual close state is invalid")
        if self.evidence_validated is not True:
            raise ValueError("validated evidence is required")
        if self.automatic_repair_allowed is not False:
            raise ValueError("automatic repair is not allowed")

    @classmethod
    def from_result(cls, result: SandboxTestLabResult) -> "SandboxRepairHandoff":
        if type(result) is not SandboxTestLabResult:
            raise ValueError("result type is invalid")
        result.__post_init__()
        return cls(
            run_id=result.run_id,
            profile=result.profile,
            status=result.status,
            readiness=result.readiness,
            progress=result.progress,
            validated_checks=tuple(
                ValidatedSandboxCheck(check.check_id, check.passed)
                for check in result.validated_checks
            ),
            evidence_references=tuple(reference for reference in result.evidence_references),
            errors=tuple(code for code in result.errors),
            warnings=tuple(code for code in result.warnings),
            manual_close_required=result.manual_close_required,
            evidence_validated=result.evidence_validated,
        )


class SandboxTestLabRunner(Protocol):
    def prepare(self, profile: SandboxProfile) -> SandboxTestLabResult: ...

    def launch(self, run_id: str) -> SandboxTestLabResult: ...

    def status(self, run_id: str) -> SandboxTestLabResult: ...

    def evidence(self, run_id: str) -> SandboxTestLabResult: ...

    def cancel(self, run_id: str) -> SandboxTestLabResult: ...


@dataclass(frozen=True, slots=True)
class _OwnedRun:
    profile: SandboxProfile
    runner: SandboxTestLabRunner
    snapshot: SandboxTestLabResult


class SandboxTestLabAdapter:
    """Portable, normalized boundary between QA and a Sandbox lifecycle runner."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        runner: SandboxTestLabRunner | None = None,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
    ) -> None:
        self._enabled = bool(enabled)
        self._runner = runner
        self._capability_detector = capability_detector
        self._runs: dict[str, _OwnedRun] = {}
        self._registry_lock = RLock()

    def availability(self) -> SandboxAvailability:
        if not self._enabled:
            return SandboxAvailability.disabled()
        try:
            capability = self._capability_detector()
        except Exception:
            return SandboxAvailability(
                False,
                SandboxReadiness.UNAVAILABLE,
                errors=(SandboxDiagnosticCode.SANDBOX_UNAVAILABLE,),
            )
        errors = (
            (SandboxDiagnosticCode.SANDBOX_UNAVAILABLE,)
            if capability.blockers
            else ()
        )
        warnings = (
            (SandboxDiagnosticCode.SANDBOX_CAPABILITY_WARNING,)
            if capability.warnings
            else ()
        )
        if not capability.available:
            return SandboxAvailability(False, SandboxReadiness.UNAVAILABLE, errors, warnings)
        if self._runner is None:
            return SandboxAvailability(
                True,
                SandboxReadiness.NOT_READY,
                warnings=warnings + (SandboxDiagnosticCode.SANDBOX_RUNNER_NOT_CONFIGURED,),
            )
        return SandboxAvailability(True, SandboxReadiness.READY, warnings=warnings)

    def prepare(self, profile: SandboxProfile | str) -> SandboxTestLabResult:
        if type(profile) is SandboxProfile:
            normalized_profile = profile
        elif type(profile) is str:
            try:
                normalized_profile = SandboxProfile(profile)
            except ValueError:
                raise ValueError("profile is invalid") from None
        else:
            raise ValueError("profile is invalid")
        runner = self._ready_runner()
        try:
            result = self._trusted_snapshot(runner.prepare(normalized_profile))
        except SandboxTestLabError:
            raise
        except Exception:
            raise SandboxTestLabError("sandbox_runner_prepare_failed") from None
        if result.profile is not normalized_profile:
            raise SandboxTestLabError("sandbox_runner_profile_mismatch")
        if result.status is not SandboxStatus.PREPARED:
            raise SandboxTestLabError("sandbox_runner_prepare_status_invalid")
        with self._registry_lock:
            if result.run_id in self._runs:
                raise SandboxTestLabError("sandbox_run_id_conflict")
            registry_snapshot = self._trusted_snapshot(result)
            self._runs[result.run_id] = _OwnedRun(result.profile, runner, registry_snapshot)
        return self._trusted_snapshot(result)

    def launch(self, run_id: str) -> SandboxTestLabResult:
        return self._run_operation("launch", run_id)

    def status(self, run_id: str) -> SandboxTestLabResult:
        return self._run_operation("status", run_id)

    def evidence(self, run_id: str) -> SandboxTestLabResult:
        return self._run_operation("evidence", run_id)

    def cancel(self, run_id: str) -> SandboxTestLabResult:
        return self._run_operation("cancel", run_id)

    def repair_handoff(self, run_id: str) -> SandboxRepairHandoff:
        normalized_run_id = _validate_exact_run_id(run_id)
        with self._registry_lock:
            owned_run = self._runs.get(normalized_run_id)
            if owned_run is None:
                raise SandboxTestLabError("sandbox_run_not_prepared")
            snapshot = self._trusted_snapshot(owned_run.snapshot)
            if snapshot.status not in _TERMINAL_STATUSES:
                raise SandboxTestLabError("sandbox_repair_handoff_not_terminal")
            if not snapshot.evidence_validated:
                raise SandboxTestLabError("sandbox_repair_handoff_evidence_invalid")
            if snapshot.run_id != normalized_run_id or snapshot.profile is not owned_run.profile:
                raise SandboxTestLabError("sandbox_repair_handoff_identity_invalid")
            return SandboxRepairHandoff.from_result(snapshot)

    def _ready_runner(self) -> SandboxTestLabRunner:
        availability = self.availability()
        if availability.readiness is SandboxReadiness.DISABLED:
            raise SandboxTestLabDisabledError("sandbox_test_lab_disabled")
        if availability.readiness is not SandboxReadiness.READY or self._runner is None:
            raise SandboxTestLabUnavailableError("sandbox_test_lab_not_ready")
        return self._runner

    def _run_operation(self, operation: str, run_id: str) -> SandboxTestLabResult:
        normalized_run_id = _validate_exact_run_id(run_id)
        with self._registry_lock:
            owned_run = self._runs.get(normalized_run_id)
            if owned_run is None:
                raise SandboxTestLabError("sandbox_run_not_prepared")
            current_status = owned_run.snapshot.status
            if current_status in _TERMINAL_STATUSES:
                if operation in {"status", "evidence"}:
                    return self._trusted_snapshot(owned_run.snapshot)
                raise SandboxTestLabError("sandbox_run_terminal")
            allowed_operations = _ALLOWED_BACKEND_OPERATIONS.get(current_status)
            if allowed_operations is None or operation not in allowed_operations:
                raise SandboxTestLabError("sandbox_operation_not_allowed")
            try:
                result = self._trusted_snapshot(
                    getattr(owned_run.runner, operation)(normalized_run_id)
                )
            except SandboxTestLabError:
                raise
            except Exception:
                raise SandboxTestLabError("sandbox_runner_operation_failed") from None
            if result.run_id != normalized_run_id:
                raise SandboxTestLabError("sandbox_runner_run_id_mismatch")
            if result.profile is not owned_run.profile:
                raise SandboxTestLabError("sandbox_runner_profile_mismatch")
            allowed_targets = _ALLOWED_STATUS_TRANSITIONS.get(current_status)
            if allowed_targets is None or result.status not in allowed_targets:
                raise SandboxTestLabError("sandbox_runner_status_transition_invalid")
            if operation == "cancel" and result.status not in {
                SandboxStatus.CANCELLING,
                SandboxStatus.CANCELLED,
            }:
                raise SandboxTestLabError("sandbox_runner_cancel_status_invalid")
            registry_snapshot = self._trusted_snapshot(result)
            self._runs[normalized_run_id] = _OwnedRun(
                owned_run.profile,
                owned_run.runner,
                registry_snapshot,
            )
            return self._trusted_snapshot(result)

    @staticmethod
    def _trusted_snapshot(result: object) -> SandboxTestLabResult:
        if type(result) is not SandboxTestLabResult:
            raise SandboxTestLabError("sandbox_runner_result_invalid")
        try:
            if (
                type(result.run_id) is not str
                or type(result.profile) is not SandboxProfile
                or type(result.status) is not SandboxStatus
                or type(result.readiness) is not SandboxReadiness
                or type(result.progress) is not SandboxProgress
                or type(result.manual_close_required) is not bool
                or type(result.evidence_validated) is not bool
            ):
                raise ValueError("invalid scalar fields")
            if any(
                type(values) is not tuple
                for values in (
                    result.validated_checks,
                    result.evidence_references,
                    result.errors,
                    result.warnings,
                )
            ):
                raise ValueError("invalid collections")
            if any(
                type(check) is not ValidatedSandboxCheck
                or type(check.check_id) is not SandboxCheckCode
                or type(check.passed) is not bool
                for check in result.validated_checks
            ):
                raise ValueError("invalid checks")
            if any(type(reference) is not str for reference in result.evidence_references):
                raise ValueError("invalid evidence references")
            if any(type(code) is not SandboxDiagnosticCode for code in result.errors):
                raise ValueError("invalid errors")
            if any(type(code) is not SandboxDiagnosticCode for code in result.warnings):
                raise ValueError("invalid warnings")
            checks = tuple(
                ValidatedSandboxCheck(check.check_id, check.passed)
                for check in result.validated_checks
            )
            return SandboxTestLabResult(
                run_id=result.run_id,
                profile=result.profile,
                status=result.status,
                readiness=result.readiness,
                progress=result.progress,
                validated_checks=checks,
                evidence_references=tuple(reference for reference in result.evidence_references),
                errors=tuple(code for code in result.errors),
                warnings=tuple(code for code in result.warnings),
                manual_close_required=result.manual_close_required,
                evidence_validated=result.evidence_validated,
            )
        except (AttributeError, TypeError, ValueError):
            raise SandboxTestLabError("sandbox_runner_result_invalid") from None
