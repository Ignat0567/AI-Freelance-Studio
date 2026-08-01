from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Callable, Sequence

from .capability import detect_sandbox_capability
from .fixture_installation import ensure_no_active_windows_sandbox_session
from .interactive_session import (
    InteractiveSessionRequest,
    interactive_session_run_root,
    write_interactive_session_wsb,
)
from .models import RunStatus, SandboxCapability, SandboxRunResult, validate_transition
from .runner import (
    GUEST_SANDBOX_SHUTDOWN_GRACE_SECONDS,
    ProcessHandle,
    _stop_owned_process,
    archive_run_diagnostics,
    launch_sandbox,
    utc_now,
)
from .sandbox_session import OwnedSandboxSession, capture_owned_sandbox_session
from .workspace import WorkspaceError, atomic_write_json


MAX_LAUNCH_ATTEMPTS = 3
LAUNCH_RETRY_DELAY_SECONDS = 2.0
# The only launch failure retried automatically: session discovery timed out without ever
# finding a child process at all. Confirmed empirically 2026-07-31 as the dominant transient
# failure mode of this dev machine's Windows Sandbox stack -- other WorkspaceErrors (identity
# mismatches, an already-active conflicting session, etc.) indicate a real problem and must
# fail loud, not be silently retried.
_RETRYABLE_DISCOVERY_ERROR = "owned_sandbox_client_discovery_failed"


class InteractiveSessionRunner:
    """Keeps a Windows Sandbox window open for direct human control instead of running any
    automated check. The guest desktop is already fully mouse/keyboard interactive with no
    guest script at all -- confirmed empirically 2026-07-31, see docs/interactive-sandbox-
    test-lab-phase-5-design.md. This runner only owns the session lifecycle: launch, watch
    for the host cancelling it or the user closing the guest window themselves, and enforce
    a hard maximum duration so a forgotten session can never run unattended forever.
    """

    def __init__(
        self,
        *,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 1.0,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        session_guard: Callable[[], None] = ensure_no_active_windows_sandbox_session,
        session_factory: Callable[[int, datetime], OwnedSandboxSession] = capture_owned_sandbox_session,
        launch_guard: Callable[[], None] = lambda: None,
        runtime_root: Path | None = None,
        diagnostics_root: Path | None = None,
    ):
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval
        self.utc_clock = utc_clock
        self.session_guard = session_guard
        self.session_factory = session_factory
        self.launch_guard = launch_guard
        self.runtime_root = runtime_root
        self.diagnostics_root = diagnostics_root
        self._live_lock = threading.Lock()
        self._live_run_id: str | None = None
        self._live_session: OwnedSandboxSession | None = None

    def run(self, request: InteractiveSessionRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        host_started_utc = self.utc_clock().astimezone(timezone.utc)
        started_at = host_started_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        status = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        process: ProcessHandle | None = None
        owned_session: OwnedSandboxSession | None = None
        run_root: Path | None = None

        def transition(target: RunStatus) -> None:
            nonlocal status
            validate_transition(status, target)
            status = target

        def finish(reason: str) -> SandboxRunResult:
            with self._live_lock:
                if self._live_run_id == request.run_id:
                    self._live_run_id = None
                    self._live_session = None
            result = SandboxRunResult(
                run_id=request.run_id, status=status, started_at=started_at, finished_at=utc_now(),
                duration_seconds=round(max(0.0, self.monotonic() - started_monotonic), 3), exit_reason=reason,
                evidence_path=None, errors=tuple(errors), warnings=tuple(dict.fromkeys(warnings)),
            )
            if run_root is not None:
                atomic_write_json(run_root / "host-result.json", result.to_dict())
                (run_root / "host.log").write_text(
                    f"run_id={request.run_id}\nstatus={status.value}\nexit_reason={reason}\n", encoding="utf-8",
                )
                archive_run_diagnostics(
                    self.diagnostics_root, request.run_id,
                    SimpleNamespace(run_root=run_root, logs_directory=run_root),
                )
            return result

        try:
            capability = self.capability_detector()
            warnings.extend(capability.warnings)
            if not capability.available:
                transition(RunStatus.UNAVAILABLE)
                errors.extend(capability.blockers)
                return finish("sandbox_unavailable")
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return finish("cancelled_before_launch")
            if self.monotonic() >= deadline:
                transition(RunStatus.TIMED_OUT)
                return finish("timeout_before_launch")
            if not capability.executable_path:
                raise RuntimeError("capability did not provide WindowsSandbox.exe path")

            run_root = interactive_session_run_root(request.run_id, self.runtime_root)
            config_file = write_interactive_session_wsb(run_root)

            transition(RunStatus.LAUNCHING)
            for attempt in range(1, MAX_LAUNCH_ATTEMPTS + 1):
                # Re-checked on every attempt, not just the first: a prior failed attempt
                # can leave an orphaned Sandbox process behind, and that must fail loud
                # here rather than let a retry silently race against it.
                self.session_guard()
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    return finish("cancelled_before_launch")
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    return finish("timeout_before_launch")

                launcher_started_utc = self.utc_clock().astimezone(timezone.utc)
                self.launch_guard()
                process = self.launcher([capability.executable_path, str(config_file)])
                try:
                    owned_session = self.session_factory(process.pid, launcher_started_utc)
                    break
                except WorkspaceError as exc:
                    _stop_owned_process(process)
                    process = None
                    if str(exc) != _RETRYABLE_DISCOVERY_ERROR or attempt == MAX_LAUNCH_ATTEMPTS:
                        raise
                    errors.append(f"launch_attempt_{attempt}_failed_{exc}")
                    self.sleeper(LAUNCH_RETRY_DELAY_SECONDS)

            with self._live_lock:
                self._live_run_id = request.run_id
                self._live_session = owned_session
            transition(RunStatus.RUNNING)

            while True:
                if cancellation is not None and cancellation.is_set():
                    self._close_session(owned_session, errors)
                    transition(RunStatus.CANCELLED)
                    return finish("cancelled_by_host")
                if self.monotonic() >= deadline:
                    self._close_session(owned_session, errors)
                    transition(RunStatus.TIMED_OUT)
                    return finish("interactive_session_max_duration_exceeded")
                if not owned_session.is_running():
                    transition(RunStatus.CANCELLED)
                    return finish("guest_closed_session")
                self.sleeper(self.poll_interval)
        except (WorkspaceError, OSError, RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            if owned_session is not None:
                self._close_session(owned_session, errors)
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            return finish("infrastructure_error")
        finally:
            if process is not None:
                _stop_owned_process(process)

    def capture_frame(self, run_id: str) -> bytes | None:
        """Best-effort live thumbnail of the running session's Sandbox window, or None if
        there is no live session for this run_id right now. Never raises. The actual
        capture happens outside the lock -- it shells out to PowerShell and can be slow,
        and must never block the run() loop's own polling."""
        with self._live_lock:
            if self._live_run_id != run_id or self._live_session is None:
                return None
            session = self._live_session
        return session.capture_window_png()

    def _close_session(self, session: OwnedSandboxSession, errors: list[str]) -> bool:
        """Best-effort graceful close, escalating to a verified kill and, as a last resort
        for the remote_session model, to killing its server process directly (the server
        does not always exit promptly once its client closes -- confirmed empirically
        2026-07-31). Never declares success without actually re-checking that every owned
        process is gone; a soft CloseMainWindow can return without the process exiting."""
        try:
            session.request_close()
        except WorkspaceError as exc:
            errors.append(f"owned_sandbox_request_close_failed_{type(exc).__name__}")
        if self._wait_until_closed(session, GUEST_SANDBOX_SHUTDOWN_GRACE_SECONDS):
            return True

        try:
            session.terminate()
        except WorkspaceError as exc:
            errors.append(f"owned_sandbox_terminate_failed_{type(exc).__name__}")
        else:
            if self._wait_until_closed(session, 5.0):
                return True

        if session.model == "remote_session" and session.server_pid is not None:
            try:
                session.terminate_server()
            except WorkspaceError as exc:
                errors.append(f"owned_sandbox_terminate_server_failed_{type(exc).__name__}")
            else:
                if self._wait_until_closed(session, 5.0):
                    return True

        errors.append("owned_sandbox_session_survived_terminate")
        return False

    def _wait_until_closed(self, session: OwnedSandboxSession, timeout_seconds: float) -> bool:
        deadline = self.monotonic() + timeout_seconds
        while self.monotonic() < deadline:
            if not session.is_running():
                return True
            self.sleeper(min(0.5, max(0.0, deadline - self.monotonic())))
        return not session.is_running()
