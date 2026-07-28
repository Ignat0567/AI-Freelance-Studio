from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Callable, Mapping, Protocol

from .adapter import (
    SandboxAvailability,
    SandboxCheckCode,
    SandboxDiagnosticCode,
    SandboxProfile,
    SandboxProgress,
    SandboxReadiness,
    SandboxStatus,
    SandboxTestLabAdapter,
    SandboxTestLabResult,
    ValidatedSandboxCheck,
)
from .job_service import JsonSandboxJobStateStore, SandboxTestLabJobService
from .models import RunStatus, SandboxRunResult
from .production_runner import ProductionSelfTestRunner
from .production_self_test import (
    ProductionSelfTestRequest,
    ensure_no_active_windows_sandbox_session,
    validate_trusted_production_artifact,
)
from .screenshot_runner import ScreenshotSelfTestRunner
from .screenshot_self_test import ScreenshotSelfTestRequest


PRODUCTION_UI_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_TEST_LAB_UI"
PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS = 70.0


class _BlockingRunner(Protocol):
    def run(
        self,
        request: ProductionSelfTestRequest,
        cancellation: Event | None = None,
    ) -> SandboxRunResult: ...


@dataclass(slots=True)
class _PreparedRun:
    profile: SandboxProfile
    request: ProductionSelfTestRequest
    runner: _BlockingRunner
    cancellation: Event
    snapshot: SandboxTestLabResult
    worker: Thread | None = None


class ProductionSandboxTestLabRunner:
    """Asynchronous adapter over the two exact production Sandbox runners."""

    def __init__(
        self,
        *,
        opt_in_enabled: Callable[[], bool] | None = None,
        self_test_runner_factory: Callable[[], _BlockingRunner] | None = None,
        screenshot_runner_factory: Callable[[], _BlockingRunner] | None = None,
        self_test_request_factory: Callable[[], ProductionSelfTestRequest] = ProductionSelfTestRequest,
        screenshot_request_factory: Callable[[], ProductionSelfTestRequest] = ScreenshotSelfTestRequest,
        thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        self._opt_in_enabled = opt_in_enabled or (
            lambda: os.environ.get(PRODUCTION_UI_EXTERNAL_OPT_IN) == "1"
        )
        self._runner_factories = {
            SandboxProfile.PRODUCTION_SELF_TEST: self_test_runner_factory
            or (lambda: ProductionSelfTestRunner(launch_guard=self._guard_external_launch)),
            SandboxProfile.PRODUCTION_SCREENSHOT: screenshot_runner_factory
            or (lambda: ScreenshotSelfTestRunner(launch_guard=self._guard_external_launch)),
        }
        self._request_factories = {
            SandboxProfile.PRODUCTION_SELF_TEST: self_test_request_factory,
            SandboxProfile.PRODUCTION_SCREENSHOT: screenshot_request_factory,
        }
        self._thread_factory = thread_factory
        self._run: _PreparedRun | None = None
        self._lock = RLock()

    def prepare(self, profile: SandboxProfile) -> SandboxTestLabResult:
        self._require_opt_in()
        if type(profile) is not SandboxProfile:
            raise ValueError("profile is invalid")
        with self._lock:
            if self._run is not None:
                raise RuntimeError("production_runner_already_prepared")
            request = self._request_factories[profile]()
            snapshot = SandboxTestLabResult(
                request.run_id,
                profile,
                SandboxStatus.PREPARED,
                SandboxReadiness.READY,
                progress=SandboxProgress.PREPARING,
            )
            self._run = _PreparedRun(
                profile,
                request,
                self._runner_factories[profile](),
                Event(),
                snapshot,
            )
            return snapshot

    def launch(self, run_id: str) -> SandboxTestLabResult:
        self._require_opt_in()
        with self._lock:
            run = self._owned_run(run_id)
            if run.worker is not None:
                raise RuntimeError("production_runner_already_launched")
            launching = self._snapshot(run, SandboxStatus.LAUNCHING, SandboxProgress.LAUNCHING)
            worker = self._thread_factory(
                target=self._execute,
                args=(run,),
                name=f"production-sandbox-runner-{run_id}",
                daemon=True,
            )
            run.worker = worker
            worker.start()
            return launching

    def status(self, run_id: str) -> SandboxTestLabResult:
        with self._lock:
            return self._owned_run(run_id).snapshot

    def evidence(self, run_id: str) -> SandboxTestLabResult:
        return self.status(run_id)

    def cancel(self, run_id: str) -> SandboxTestLabResult:
        with self._lock:
            run = self._owned_run(run_id)
            run.cancellation.set()
            if run.worker is None:
                return self._snapshot(run, SandboxStatus.CANCELLED, SandboxProgress.COMPLETE)
            if run.snapshot.status in {
                SandboxStatus.PASSED,
                SandboxStatus.FAILED,
                SandboxStatus.TIMED_OUT,
                SandboxStatus.CANCELLED,
                SandboxStatus.INFRASTRUCTURE_ERROR,
            }:
                if run.snapshot.status is SandboxStatus.PASSED:
                    run.snapshot = self._cancelled_snapshot(run.snapshot)
                    return run.snapshot
                return SandboxTestLabResult(
                    run.request.run_id,
                    run.profile,
                    SandboxStatus.CANCELLING,
                    SandboxReadiness.READY,
                    progress=SandboxProgress.CANCELLING,
                    manual_close_required=run.snapshot.manual_close_required,
                )
            return self._snapshot(run, SandboxStatus.CANCELLING, SandboxProgress.CANCELLING)

    def _execute(self, run: _PreparedRun) -> None:
        with self._lock:
            if run.cancellation.is_set():
                self._snapshot(run, SandboxStatus.CANCELLED, SandboxProgress.COMPLETE)
                return
            self._snapshot(run, SandboxStatus.RUNNING, SandboxProgress.RUNNING_CHECKS)
        try:
            result = run.runner.run(run.request, run.cancellation)
            snapshot = self._translate(run.profile, result)
        except SystemExit:
            snapshot = SandboxTestLabResult(
                run.request.run_id,
                run.profile,
                SandboxStatus.INFRASTRUCTURE_ERROR,
                SandboxReadiness.READY,
                progress=SandboxProgress.COMPLETE,
            )
        except KeyboardInterrupt:
            snapshot = SandboxTestLabResult(
                run.request.run_id,
                run.profile,
                SandboxStatus.INFRASTRUCTURE_ERROR,
                SandboxReadiness.READY,
                progress=SandboxProgress.COMPLETE,
            )
            with self._lock:
                if run.cancellation.is_set():
                    snapshot = self._cancelled_snapshot(snapshot)
                run.snapshot = snapshot
            raise
        except BaseException:
            snapshot = SandboxTestLabResult(
                run.request.run_id,
                run.profile,
                SandboxStatus.INFRASTRUCTURE_ERROR,
                SandboxReadiness.READY,
                progress=SandboxProgress.COMPLETE,
            )
        with self._lock:
            if run.cancellation.is_set() and snapshot.status is SandboxStatus.PASSED:
                snapshot = self._cancelled_snapshot(snapshot)
            run.snapshot = snapshot

    @staticmethod
    def _translate(profile: SandboxProfile, result: SandboxRunResult) -> SandboxTestLabResult:
        status_map = {
            RunStatus.PASSED: SandboxStatus.PASSED,
            RunStatus.FAILED: SandboxStatus.FAILED,
            RunStatus.TIMED_OUT: SandboxStatus.TIMED_OUT,
            RunStatus.CANCELLED: SandboxStatus.CANCELLED,
            RunStatus.INFRASTRUCTURE_ERROR: SandboxStatus.INFRASTRUCTURE_ERROR,
            RunStatus.UNAVAILABLE: SandboxStatus.INFRASTRUCTURE_ERROR,
        }
        status = status_map.get(result.status, SandboxStatus.INFRASTRUCTURE_ERROR)
        passed = status is SandboxStatus.PASSED
        evidence_validated = result.evidence_path is not None
        cleanup_failed = any(
            error.startswith("owned_sandbox_") for error in result.errors
        )
        diagnostics: list[SandboxDiagnosticCode] = []
        if result.status is RunStatus.UNAVAILABLE:
            diagnostics.append(SandboxDiagnosticCode.SANDBOX_UNAVAILABLE)
        if cleanup_failed and evidence_validated:
            diagnostics.append(SandboxDiagnosticCode.SANDBOX_GUEST_SUCCEEDED_OWNERSHIP_FAILED)
        return SandboxTestLabResult(
            result.run_id,
            profile,
            status,
            SandboxReadiness.READY,
            progress=SandboxProgress.COMPLETE,
            validated_checks=(ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, passed),)
            if evidence_validated
            else (),
            evidence_references=(result.run_id,) if evidence_validated else (),
            errors=tuple(diagnostics),
            manual_close_required=cleanup_failed,
            evidence_validated=evidence_validated,
        )

    def _owned_run(self, run_id: str) -> _PreparedRun:
        if self._run is None or self._run.request.run_id != run_id:
            raise RuntimeError("production_run_not_prepared")
        return self._run

    @staticmethod
    def _cancelled_snapshot(snapshot: SandboxTestLabResult) -> SandboxTestLabResult:
        return SandboxTestLabResult(
            snapshot.run_id,
            snapshot.profile,
            SandboxStatus.CANCELLED,
            snapshot.readiness,
            progress=SandboxProgress.COMPLETE,
            validated_checks=snapshot.validated_checks,
            evidence_references=snapshot.evidence_references,
            errors=snapshot.errors,
            warnings=snapshot.warnings,
            manual_close_required=snapshot.manual_close_required,
            evidence_validated=snapshot.evidence_validated,
        )

    @staticmethod
    def _snapshot(
        run: _PreparedRun,
        status: SandboxStatus,
        progress: SandboxProgress,
    ) -> SandboxTestLabResult:
        run.snapshot = SandboxTestLabResult(
            run.request.run_id,
            run.profile,
            status,
            SandboxReadiness.READY,
            progress=progress,
            manual_close_required=run.snapshot.manual_close_required,
        )
        return run.snapshot

    def _require_opt_in(self) -> None:
        if self._opt_in_enabled() is not True:
            raise RuntimeError("production_ui_external_opt_in_required")

    def _guard_external_launch(self) -> None:
        self._require_opt_in()
        ensure_no_active_windows_sandbox_session()


@dataclass(frozen=True, slots=True)
class ProductionSandboxRuntime:
    service: SandboxTestLabJobService
    availability_provider: Callable[[], SandboxAvailability]


def create_production_sandbox_runtime(
    runtime_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> ProductionSandboxRuntime:
    source = environ if environ is not None else os.environ
    opt_in_enabled = lambda: source.get(PRODUCTION_UI_EXTERNAL_OPT_IN) == "1"
    if not opt_in_enabled():
        return ProductionSandboxRuntime(
            SandboxTestLabJobService(enabled=False),
            SandboxAvailability.disabled,
        )

    try:
        validate_trusted_production_artifact()
    except (OSError, RuntimeError, ValueError):
        unavailable = SandboxAvailability(
            False,
            SandboxReadiness.UNAVAILABLE,
            errors=(SandboxDiagnosticCode.SANDBOX_UNAVAILABLE,),
        )
        return ProductionSandboxRuntime(
            SandboxTestLabJobService(enabled=False),
            lambda: unavailable,
        )

    def adapter_factory() -> SandboxTestLabAdapter:
        return SandboxTestLabAdapter(
            enabled=True,
            runner=ProductionSandboxTestLabRunner(opt_in_enabled=opt_in_enabled),
        )

    availability_adapter = adapter_factory()

    def availability_provider() -> SandboxAvailability:
        if not opt_in_enabled():
            return SandboxAvailability.disabled()
        return availability_adapter.availability()

    service = SandboxTestLabJobService(
        enabled=True,
        adapter_factory=adapter_factory,
        state_store=JsonSandboxJobStateStore(Path(runtime_dir) / "sandbox-test-lab-jobs.json"),
    )
    return ProductionSandboxRuntime(service, availability_provider)
