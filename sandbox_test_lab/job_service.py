from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable, Protocol
from uuid import uuid4

from .adapter import (
    SandboxProfile,
    SandboxProgress,
    SandboxStatus,
    SandboxTestLabAdapter,
    SandboxTestLabResult,
)
from .models import validate_run_id


class SandboxJobError(RuntimeError):
    pass


class SandboxJobDisabledError(SandboxJobError):
    pass


class SandboxJobStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    CANCELLING = "cancelling"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


_TERMINAL_JOB_STATUSES = frozenset(
    {
        SandboxJobStatus.PASSED,
        SandboxJobStatus.FAILED,
        SandboxJobStatus.TIMED_OUT,
        SandboxJobStatus.CANCELLED,
        SandboxJobStatus.INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class SandboxJobSnapshot:
    run_id: str
    profile: SandboxProfile
    status: SandboxJobStatus
    progress: SandboxProgress
    manual_close_required: bool = False

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if type(self.profile) is not SandboxProfile:
            raise ValueError("profile must be an allowed SandboxProfile")
        if type(self.status) is not SandboxJobStatus:
            raise ValueError("status must be a normalized SandboxJobStatus")
        if type(self.progress) is not SandboxProgress:
            raise ValueError("progress must be a normalized SandboxProgress")
        if type(self.manual_close_required) is not bool:
            raise ValueError("manual_close_required must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "profile": self.profile.value,
            "status": self.status.value,
            "progress": self.progress.value,
            "manual_close_required": self.manual_close_required,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SandboxJobSnapshot":
        if type(value) is not dict or set(value) != {
            "run_id",
            "profile",
            "status",
            "progress",
            "manual_close_required",
        }:
            raise ValueError("job snapshot is invalid")
        try:
            return cls(
                run_id=value["run_id"],
                profile=SandboxProfile(value["profile"]),
                status=SandboxJobStatus(value["status"]),
                progress=SandboxProgress(value["progress"]),
                manual_close_required=value["manual_close_required"],
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("job snapshot is invalid") from None


class SandboxJobStateStore(Protocol):
    def load(self) -> tuple[SandboxJobSnapshot, ...]: ...

    def save(self, snapshot: SandboxJobSnapshot) -> None: ...


class InMemorySandboxJobStateStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, SandboxJobSnapshot] = {}
        self._lock = RLock()

    def load(self) -> tuple[SandboxJobSnapshot, ...]:
        with self._lock:
            return tuple(self._snapshots.values())

    def save(self, snapshot: SandboxJobSnapshot) -> None:
        if type(snapshot) is not SandboxJobSnapshot:
            raise ValueError("job snapshot is invalid")
        with self._lock:
            self._snapshots[snapshot.run_id] = snapshot


class JsonSandboxJobStateStore:
    """Durable store containing only the public, normalized job snapshots."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def load(self) -> tuple[SandboxJobSnapshot, ...]:
        with self._lock:
            if not self._path.is_file():
                return ()
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                if type(payload) is not dict or set(payload) != {"schema_version", "jobs"}:
                    raise ValueError
                if payload["schema_version"] != 1 or type(payload["jobs"]) is not list:
                    raise ValueError
                snapshots = tuple(SandboxJobSnapshot.from_dict(item) for item in payload["jobs"])
                if len({item.run_id for item in snapshots}) != len(snapshots):
                    raise ValueError
                return snapshots
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                raise SandboxJobError("sandbox_job_state_invalid") from None

    def save(self, snapshot: SandboxJobSnapshot) -> None:
        if type(snapshot) is not SandboxJobSnapshot:
            raise ValueError("job snapshot is invalid")
        with self._lock:
            snapshots = {item.run_id: item for item in self.load()}
            snapshots[snapshot.run_id] = snapshot
            payload = {
                "schema_version": 1,
                "jobs": [item.to_dict() for item in snapshots.values()],
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self._path)
            except OSError:
                raise SandboxJobError("sandbox_job_state_write_failed") from None


@dataclass(slots=True)
class _JobRecord:
    snapshot: SandboxJobSnapshot
    cancellation: Event
    adapter: SandboxTestLabAdapter | None = None
    backend_run_id: str | None = None
    cancel_sent: bool = False
    worker: Thread | None = None


class SandboxTestLabJobService:
    """Single-active-run, in-process orchestration above the Phase 4A adapter."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        adapter_factory: Callable[[], SandboxTestLabAdapter] | None = None,
        state_store: SandboxJobStateStore | None = None,
        poll_interval: float = 0.1,
        thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._enabled = bool(enabled)
        self._adapter_factory = adapter_factory
        self._state_store = state_store or InMemorySandboxJobStateStore()
        self._poll_interval = float(poll_interval)
        self._thread_factory = thread_factory
        self._records: dict[str, _JobRecord] = {}
        self._active_run_id: str | None = None
        self._accepting = True
        self._lock = RLock()
        self._restore()

    def start(self, profile: SandboxProfile | str) -> SandboxJobSnapshot:
        normalized_profile = self._normalize_profile(profile)
        if not self._enabled:
            raise SandboxJobDisabledError("sandbox_test_lab_disabled")
        if self._adapter_factory is None:
            raise SandboxJobError("sandbox_job_backend_not_configured")
        with self._lock:
            if not self._accepting:
                raise SandboxJobError("sandbox_job_service_stopping")
            if self._active_run_id is not None:
                raise SandboxJobError("sandbox_job_already_active")
            run_id = str(uuid4())
            while run_id in self._records:
                run_id = str(uuid4())
            snapshot = SandboxJobSnapshot(
                run_id,
                normalized_profile,
                SandboxJobStatus.QUEUED,
                SandboxProgress.NOT_STARTED,
            )
            record = _JobRecord(snapshot=snapshot, cancellation=Event())
            try:
                worker = self._thread_factory(
                    target=self._run_job,
                    args=(run_id,),
                    name=f"sandbox-test-lab-{run_id}",
                    daemon=True,
                )
            except Exception:
                raise SandboxJobError("sandbox_job_worker_start_failed") from None
            record.worker = worker
            self._save(snapshot)
            self._records[run_id] = record
            self._active_run_id = run_id
            try:
                worker.start()
            except Exception:
                self._set_status_locked(record, SandboxJobStatus.FAILED, SandboxProgress.COMPLETE)
                raise SandboxJobError("sandbox_job_worker_start_failed") from None
            return self._copy_snapshot(snapshot)

    def snapshot(self, run_id: str) -> SandboxJobSnapshot:
        normalized_run_id = self._exact_run_id(run_id)
        with self._lock:
            record = self._records.get(normalized_run_id)
            if record is None:
                raise SandboxJobError("sandbox_job_not_found")
            return self._copy_snapshot(record.snapshot)

    def cancel(self, run_id: str) -> SandboxJobSnapshot:
        normalized_run_id = self._exact_run_id(run_id)
        with self._lock:
            record = self._records.get(normalized_run_id)
            if record is None:
                raise SandboxJobError("sandbox_job_not_found")
            if record.snapshot.status in _TERMINAL_JOB_STATUSES:
                raise SandboxJobError("sandbox_job_terminal")
            record.cancellation.set()
            self._set_status_locked(
                record,
                SandboxJobStatus.CANCELLING,
                SandboxProgress.CANCELLING,
                manual_close_required=record.snapshot.manual_close_required,
            )
            should_send = (
                record.adapter is not None
                and record.backend_run_id is not None
                and not record.cancel_sent
            )
            if should_send:
                record.cancel_sent = True
                adapter = record.adapter
                backend_run_id = record.backend_run_id
            else:
                adapter = None
                backend_run_id = None
            current = self._copy_snapshot(record.snapshot)
        if adapter is not None and backend_run_id is not None:
            self._send_cancel(normalized_run_id, adapter, backend_run_id)
            return self.snapshot(normalized_run_id)
        return current

    def wait(self, run_id: str, timeout: float | None = None) -> SandboxJobSnapshot:
        normalized_run_id = self._exact_run_id(run_id)
        with self._lock:
            record = self._records.get(normalized_run_id)
            if record is None:
                raise SandboxJobError("sandbox_job_not_found")
            worker = record.worker
        if worker is not None:
            worker.join(timeout)
        return self.snapshot(normalized_run_id)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop new jobs and request cooperative cancellation without an unbounded wait."""
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._lock:
            self._accepting = False
            record = self._records.get(self._active_run_id) if self._active_run_id else None
            if record is None or record.snapshot.status in _TERMINAL_JOB_STATUSES:
                return
            record.cancellation.set()
            self._set_status_locked(
                record,
                SandboxJobStatus.CANCELLING,
                SandboxProgress.CANCELLING,
                manual_close_required=record.snapshot.manual_close_required,
            )
            worker = record.worker
            should_send = (
                record.adapter is not None
                and record.backend_run_id is not None
                and not record.cancel_sent
            )
            if should_send:
                record.cancel_sent = True
                adapter = record.adapter
                backend_run_id = record.backend_run_id
            else:
                adapter = None
                backend_run_id = None
        deadline = monotonic() + timeout
        cancel_worker = None
        if adapter is not None and backend_run_id is not None:
            try:
                cancel_worker = Thread(
                    target=self._send_cancel,
                    args=(record.snapshot.run_id, adapter, backend_run_id),
                    name=f"sandbox-test-lab-shutdown-{record.snapshot.run_id}",
                    daemon=True,
                )
                cancel_worker.start()
            except Exception:
                with self._lock:
                    if record.snapshot.status not in _TERMINAL_JOB_STATUSES:
                        record.cancel_sent = False
                cancel_worker = None
        for active_worker in (cancel_worker, worker):
            if active_worker is not None:
                active_worker.join(max(0.0, deadline - monotonic()))

    def _restore(self) -> None:
        for snapshot in self._state_store.load():
            if type(snapshot) is not SandboxJobSnapshot:
                raise SandboxJobError("sandbox_job_state_invalid")
            restored = snapshot
            if snapshot.status not in _TERMINAL_JOB_STATUSES:
                restored = SandboxJobSnapshot(
                    snapshot.run_id,
                    snapshot.profile,
                    SandboxJobStatus.INTERRUPTED,
                    SandboxProgress.COMPLETE,
                    snapshot.manual_close_required,
                )
                self._save(restored)
            self._records[restored.run_id] = _JobRecord(restored, Event())

    def _run_job(self, run_id: str) -> None:
        try:
            with self._lock:
                record = self._records[run_id]
                if record.cancellation.is_set():
                    self._set_status_locked(record, SandboxJobStatus.CANCELLED, SandboxProgress.COMPLETE)
                    return
                self._set_status_locked(record, SandboxJobStatus.PREPARING, SandboxProgress.PREPARING)
            adapter = self._adapter_factory()
            with self._lock:
                record.adapter = adapter
            prepared = adapter.prepare(record.snapshot.profile)
            with self._lock:
                record.backend_run_id = prepared.run_id
            self._apply_backend_result(run_id, prepared)
            if record.cancellation.is_set():
                self._claim_and_send_cancel(run_id)
            else:
                launched = adapter.launch(prepared.run_id)
                self._apply_backend_result(run_id, launched)

            while not self._is_terminal(run_id):
                if record.cancellation.is_set():
                    self._claim_and_send_cancel(run_id)
                status = adapter.status(prepared.run_id)
                self._apply_backend_result(run_id, status)
                if self._is_terminal(run_id):
                    break
                evidence = adapter.evidence(prepared.run_id)
                self._apply_backend_result(run_id, evidence)
                if self._is_terminal(run_id):
                    break
                record.cancellation.wait(self._poll_interval)
        except Exception:
            with self._lock:
                record = self._records[run_id]
                if record.snapshot.status not in _TERMINAL_JOB_STATUSES:
                    self._set_status_locked(record, SandboxJobStatus.FAILED, SandboxProgress.COMPLETE)

    def _claim_and_send_cancel(self, run_id: str) -> None:
        with self._lock:
            record = self._records[run_id]
            if record.cancel_sent or record.adapter is None or record.backend_run_id is None:
                return
            record.cancel_sent = True
            adapter = record.adapter
            backend_run_id = record.backend_run_id
        self._send_cancel(run_id, adapter, backend_run_id)

    def _send_cancel(
        self,
        run_id: str,
        adapter: SandboxTestLabAdapter,
        backend_run_id: str,
    ) -> None:
        try:
            result = adapter.cancel(backend_run_id)
            self._apply_backend_result(run_id, result)
        except Exception:
            with self._lock:
                record = self._records[run_id]
                if record.snapshot.status not in _TERMINAL_JOB_STATUSES:
                    self._set_status_locked(record, SandboxJobStatus.FAILED, SandboxProgress.COMPLETE)

    def _apply_backend_result(self, run_id: str, result: SandboxTestLabResult) -> None:
        status, progress = self._normalize_backend_result(result)
        with self._lock:
            record = self._records[run_id]
            current = record.snapshot.status
            if current in _TERMINAL_JOB_STATUSES:
                return
            if current is SandboxJobStatus.CANCELLING and status not in _TERMINAL_JOB_STATUSES:
                status = SandboxJobStatus.CANCELLING
                progress = SandboxProgress.CANCELLING
            self._set_status_locked(
                record,
                status,
                progress,
                manual_close_required=(
                    record.snapshot.manual_close_required or result.manual_close_required
                ),
            )

    @staticmethod
    def _normalize_backend_result(
        result: SandboxTestLabResult,
    ) -> tuple[SandboxJobStatus, SandboxProgress]:
        if type(result) is not SandboxTestLabResult:
            raise SandboxJobError("sandbox_job_backend_result_invalid")
        status_map = {
            SandboxStatus.PREPARED: (SandboxJobStatus.PREPARING, SandboxProgress.PREPARING),
            SandboxStatus.LAUNCHING: (SandboxJobStatus.RUNNING, SandboxProgress.LAUNCHING),
            SandboxStatus.RUNNING: (SandboxJobStatus.RUNNING, SandboxProgress.RUNNING_CHECKS),
            SandboxStatus.CANCELLING: (SandboxJobStatus.CANCELLING, SandboxProgress.CANCELLING),
            SandboxStatus.PASSED: (SandboxJobStatus.PASSED, SandboxProgress.COMPLETE),
            SandboxStatus.FAILED: (SandboxJobStatus.FAILED, SandboxProgress.COMPLETE),
            SandboxStatus.INFRASTRUCTURE_ERROR: (SandboxJobStatus.FAILED, SandboxProgress.COMPLETE),
            SandboxStatus.TIMED_OUT: (SandboxJobStatus.TIMED_OUT, SandboxProgress.COMPLETE),
            SandboxStatus.CANCELLED: (SandboxJobStatus.CANCELLED, SandboxProgress.COMPLETE),
        }
        normalized = status_map.get(result.status)
        if normalized is None:
            raise SandboxJobError("sandbox_job_backend_status_invalid")
        if result.status is SandboxStatus.RUNNING and result.progress is SandboxProgress.COLLECTING_EVIDENCE:
            return SandboxJobStatus.RUNNING, SandboxProgress.COLLECTING_EVIDENCE
        return normalized

    def _set_status_locked(
        self,
        record: _JobRecord,
        status: SandboxJobStatus,
        progress: SandboxProgress,
        *,
        manual_close_required: bool = False,
    ) -> None:
        if record.snapshot.status in _TERMINAL_JOB_STATUSES:
            return
        snapshot = SandboxJobSnapshot(
            record.snapshot.run_id,
            record.snapshot.profile,
            status,
            progress,
            manual_close_required,
        )
        record.snapshot = snapshot
        if status in _TERMINAL_JOB_STATUSES and self._active_run_id == snapshot.run_id:
            self._active_run_id = None
        self._save(snapshot)

    def _is_terminal(self, run_id: str) -> bool:
        with self._lock:
            return self._records[run_id].snapshot.status in _TERMINAL_JOB_STATUSES

    def _save(self, snapshot: SandboxJobSnapshot) -> None:
        self._state_store.save(self._copy_snapshot(snapshot))

    @staticmethod
    def _normalize_profile(profile: SandboxProfile | str) -> SandboxProfile:
        if type(profile) is SandboxProfile:
            return profile
        if type(profile) is str:
            try:
                return SandboxProfile(profile)
            except ValueError:
                pass
        raise ValueError("profile is invalid")

    @staticmethod
    def _exact_run_id(run_id: object) -> str:
        if type(run_id) is not str:
            raise ValueError("run_id must be an exact string")
        return validate_run_id(run_id)

    @staticmethod
    def _copy_snapshot(snapshot: SandboxJobSnapshot) -> SandboxJobSnapshot:
        return SandboxJobSnapshot(
            snapshot.run_id,
            snapshot.profile,
            snapshot.status,
            snapshot.progress,
            snapshot.manual_close_required,
        )
