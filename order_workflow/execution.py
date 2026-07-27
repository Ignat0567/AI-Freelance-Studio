from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re
from threading import RLock, Thread
from time import monotonic
from uuid import uuid4

from .brief_service import BriefServiceError, sanitize_public_text, verify_brief_approval
from .executors import (
    CancellationToken,
    ExecutionEventSink,
    ExecutionRequest,
    FakeProjectExecutionAdapter,
    ProjectExecutionAdapter,
)
from .models import (
    AgentHandoff,
    ArtifactKind,
    ExecutionArtifact,
    ExecutionBlocker,
    ExecutionEvent,
    ExecutionMode,
    ExecutionResult,
    ExecutionStage,
    ExecutionStatus,
    EventKind,
    EventLevel,
    ProjectBrief,
    ProjectExecution,
    TestSummary,
    append_bounded_event,
    new_public_id,
    utc_now,
)
from .persistence import ExecutionStateStore, InMemoryExecutionStateStore
from .readiness import (
    BRIEF_NOT_APPROVED,
    ELENA_CHOICE_REQUIRED,
    PRODUCTION_NOT_CONFIGURED,
    ReadinessResult,
    UNRESOLVED_QUESTIONS,
    UNSUPPORTED_PRODUCT_TYPE,
)


TERMINAL_EXECUTION_STATUSES = frozenset(
    {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
)


class ExecutionServiceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ExecutionRecord:
    def __init__(self, snapshot: ProjectExecution, token: CancellationToken) -> None:
        self.snapshot = snapshot
        self.token = token
        self.worker: Thread | None = None
        self.started_monotonic: float | None = None


class _Sink(ExecutionEventSink):
    def __init__(self, service: "ProjectExecutionService", execution_id: str) -> None:
        self._service = service
        self._execution_id = execution_id

    def emit(
        self,
        *,
        stage: ExecutionStage,
        agent: str,
        progress: int,
        message: str,
        level: EventLevel = EventLevel.INFO,
        details: tuple[str, ...] = (),
    ) -> None:
        self._service._emit(
            self._execution_id,
            kind=EventKind.ACTIVITY,
            stage=stage,
            agent=agent,
            progress=progress,
            message=message,
            level=level,
            details=details,
        )

    def artifact(self, *, kind: ArtifactKind, name: str, summary: str, reference: str) -> ExecutionArtifact:
        return self._service._add_artifact(self._execution_id, kind=kind, name=name, summary=summary, reference=reference)


class ProjectExecutionService:
    """Application-owned in-process execution registry.

    Concurrency policy: multiple different orders may run concurrently, but one approved
    brief fingerprint can be executed only once. Duplicate active starts return the
    existing snapshot; duplicate terminal starts are rejected until a future retry API.
    """

    def __init__(
        self,
        *,
        mode: ExecutionMode = ExecutionMode.FAKE,
        fake_adapter: ProjectExecutionAdapter | None = None,
        production_adapter: ProjectExecutionAdapter | None = None,
        state_store: ExecutionStateStore | None = None,
        id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
        thread_factory: Callable[..., Thread] = Thread,
        event_limit: int = 200,
    ) -> None:
        if event_limit <= 0:
            raise ValueError("event_limit must be positive")
        self._mode = mode
        self._fake_adapter = fake_adapter or FakeProjectExecutionAdapter()
        self._production_adapter = production_adapter
        self._store = state_store or InMemoryExecutionStateStore()
        self._id_factory = id_factory
        self._clock = clock
        self._thread_factory = thread_factory
        self._event_limit = event_limit
        self._lock = RLock()
        self._records: dict[str, _ExecutionRecord] = {}
        self._by_approval: dict[tuple[str, str, str], str] = {}
        self._accepting = True
        self._restore()

    def check_readiness(
        self,
        brief: ProjectBrief,
        handoff: AgentHandoff | None = None,
        *,
        mode: ExecutionMode | None = None,
    ) -> ReadinessResult:
        blockers = list(self._domain_blockers(brief, handoff))
        if blockers:
            return ReadinessResult.blocked(*blockers)
        adapter = self._adapter(mode or self._mode)
        if adapter is None:
            return ReadinessResult.blocked(PRODUCTION_NOT_CONFIGURED)
        return adapter.check_readiness(brief)

    def start(
        self,
        brief: ProjectBrief,
        handoff: AgentHandoff,
        *,
        mode: ExecutionMode | None = None,
    ) -> ProjectExecution:
        active_mode = mode or self._mode
        self._validate_handoff(brief, handoff)
        key = self._approval_key(brief)
        with self._lock:
            if not self._accepting:
                raise ExecutionServiceError("execution_service_stopping")
            existing_id = self._by_approval.get(key)
            if existing_id is not None:
                existing = self._records[existing_id].snapshot
                if existing.status in TERMINAL_EXECUTION_STATUSES or existing.status is ExecutionStatus.AWAITING_USER:
                    raise ExecutionServiceError("execution_already_completed")
                return self._snapshot(existing)
        readiness = self.check_readiness(brief, handoff, mode=active_mode)
        if not readiness.ready:
            return self._create_blocked_execution(brief, handoff, active_mode, readiness.blockers, key)
        adapter = self._adapter(active_mode)
        if adapter is None:
            return self._create_blocked_execution(brief, handoff, active_mode, (PRODUCTION_NOT_CONFIGURED,), key)
        now = utc_now(self._clock)
        execution = ProjectExecution(
            id=new_public_id("execution", self._id_factory),
            order_id=brief.order_id,
            brief_id=brief.id,
            handoff_id=handoff.id,
            approval_fingerprint=brief.approval_fingerprint,
            mode=active_mode,
            status=ExecutionStatus.QUEUED,
            stage=ExecutionStage.REQUIREMENTS,
            progress=0,
            current_activity="Queued for execution",
            created_at=now,
            updated_at=now,
        )
        record = _ExecutionRecord(execution, CancellationToken())
        with self._lock:
            if key in self._by_approval:
                return self.start(brief, handoff, mode=active_mode)
            self._records[execution.id] = record
            self._by_approval[key] = execution.id
            self._save(record.snapshot)
            worker = self._thread_factory(
                target=self._run,
                args=(execution.id, brief, handoff, adapter),
                name=f"order-execution-{execution.id}",
                daemon=True,
            )
            record.worker = worker
            worker.start()
            return self._snapshot(record.snapshot)

    def snapshot(self, execution_id: str) -> ProjectExecution:
        with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError("execution_not_found")
            return self._snapshot(record.snapshot)

    def cancel(self, execution_id: str) -> ProjectExecution:
        with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError("execution_not_found")
            if record.snapshot.status is ExecutionStatus.CANCELLED:
                return self._snapshot(record.snapshot)
            if record.snapshot.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
                return self._snapshot(record.snapshot)
            record.token.cancel()
            if record.snapshot.status in {ExecutionStatus.QUEUED, ExecutionStatus.AWAITING_USER}:
                self._finish_locked(record, self._cancelled_result(), ExecutionStatus.CANCELLED)
                return self._snapshot(record.snapshot)
        self._emit(
            execution_id,
            kind=EventKind.STATUS,
            stage=ExecutionStage.COMPLETED,
            agent="Studio",
            progress=100,
            message="Cancellation requested by user",
            level=EventLevel.WARNING,
        )
        with self._lock:
            record = self._records[execution_id]
            return self._snapshot(record.snapshot)

    def wait(self, execution_id: str, timeout: float | None = None) -> ProjectExecution:
        with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError("execution_not_found")
            worker = record.worker
        if worker is not None:
            worker.join(timeout)
        return self.snapshot(execution_id)

    def shutdown(self, timeout: float = 2.0) -> None:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._lock:
            self._accepting = False
            workers = []
            for record in self._records.values():
                if record.snapshot.status not in TERMINAL_EXECUTION_STATUSES:
                    record.token.cancel()
                    self._finish_locked(record, self._cancelled_result(), ExecutionStatus.CANCELLED)
                    workers.append(record.worker)
        deadline = monotonic() + timeout
        for worker in workers:
            if worker is not None:
                worker.join(max(0.0, deadline - monotonic()))

    def _restore(self) -> None:
        for snapshot in self._store.load():
            record = _ExecutionRecord(snapshot, CancellationToken())
            self._records[snapshot.id] = record
            if snapshot.approval_fingerprint:
                self._by_approval[(snapshot.order_id, snapshot.brief_id, snapshot.approval_fingerprint)] = snapshot.id

    def _run(
        self,
        execution_id: str,
        brief: ProjectBrief,
        handoff: AgentHandoff,
        adapter: ProjectExecutionAdapter,
    ) -> None:
        with self._lock:
            record = self._records[execution_id]
            if record.token.is_cancelled():
                self._finish_locked(record, self._cancelled_result(), ExecutionStatus.CANCELLED)
                return
            record.started_monotonic = monotonic()
            self._set_running_locked(record, ExecutionStage.REQUIREMENTS, "Alex", 5, "Starting execution")
            request = ExecutionRequest(brief=brief, handoff=handoff, execution_id=execution_id)
        try:
            result = adapter.execute(request, _Sink(self, execution_id), record.token)
            status = ExecutionStatus.SUCCEEDED if result.success else ExecutionStatus.FAILED
            if result.outcome == "cancelled" or record.token.is_cancelled():
                status = ExecutionStatus.CANCELLED
            with self._lock:
                self._finish_locked(record, result, status)
        except Exception:
            result = ExecutionResult(
                success=False,
                outcome="failed",
                summary="Execution failed because of an internal error.",
                test_summary=TestSummary(failed=1),
                errors=("execution_internal_error",),
                final_stage=ExecutionStage.COMPLETED,
                completed_at=utc_now(self._clock),
            )
            with self._lock:
                self._finish_locked(self._records[execution_id], result, ExecutionStatus.FAILED)

    def _emit(
        self,
        execution_id: str,
        *,
        kind: EventKind,
        stage: ExecutionStage,
        agent: str,
        progress: int,
        message: str,
        level: EventLevel = EventLevel.INFO,
        details: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            record = self._records.get(execution_id)
            if record is None or record.snapshot.status in TERMINAL_EXECUTION_STATUSES:
                return
            event = ExecutionEvent(
                id=new_public_id("event", self._id_factory),
                execution_id=execution_id,
                kind=kind,
                level=level,
                message=sanitize_public_text(message)[:240] or "Execution activity updated",
                stage=stage,
                agent=sanitize_public_text(agent)[:240] or "Studio",
                progress=progress,
                details=tuple(sanitize_public_text(item)[:240] for item in details),
                created_at=utc_now(self._clock),
            )
            updated = record.snapshot.model_copy(
                update={
                    "status": ExecutionStatus.RUNNING,
                    "stage": stage,
                    "active_agent": event.agent,
                    "progress": progress,
                    "current_activity": event.message,
                    "events": append_bounded_event(record.snapshot.events, event, limit=self._event_limit),
                    "updated_at": event.created_at,
                    "started_at": record.snapshot.started_at or event.created_at,
                }
            )
            record.snapshot = updated
            self._save(updated)

    def _add_artifact(self, execution_id: str, *, kind: ArtifactKind, name: str, summary: str, reference: str) -> ExecutionArtifact:
        with self._lock:
            record = self._records[execution_id]
            if record.snapshot.status in TERMINAL_EXECUTION_STATUSES:
                raise ExecutionServiceError("execution_terminal")
            artifact = ExecutionArtifact(
                id=new_public_id("artifact", self._id_factory),
                execution_id=execution_id,
                kind=kind,
                name=sanitize_public_text(name),
                summary=sanitize_public_text(summary),
                reference=self._safe_reference(reference),
                simulated=True,
                created_at=utc_now(self._clock),
            )
            event = ExecutionEvent(
                id=new_public_id("event", self._id_factory),
                execution_id=execution_id,
                kind=EventKind.ARTIFACT,
                message=f"Artifact recorded: {artifact.name}",
                stage=record.snapshot.stage,
                agent=record.snapshot.active_agent or "Studio",
                progress=record.snapshot.progress,
                created_at=artifact.created_at,
            )
            updated = record.snapshot.model_copy(
                update={
                    "artifacts": (*record.snapshot.artifacts, artifact),
                    "events": append_bounded_event(record.snapshot.events, event, limit=self._event_limit),
                    "updated_at": artifact.created_at,
                }
            )
            record.snapshot = updated
            self._save(updated)
            return artifact

    def _create_blocked_execution(
        self,
        brief: ProjectBrief,
        handoff: AgentHandoff,
        mode: ExecutionMode,
        blockers: tuple[ExecutionBlocker, ...],
        key: tuple[str, str, str],
    ) -> ProjectExecution:
        now = utc_now(self._clock)
        execution_id = new_public_id("execution", self._id_factory)
        execution = ProjectExecution(
            id=execution_id,
            order_id=brief.order_id,
            brief_id=brief.id,
            handoff_id=handoff.id,
            approval_fingerprint=brief.approval_fingerprint,
            mode=mode,
            status=ExecutionStatus.AWAITING_USER,
            stage=ExecutionStage.REQUIREMENTS,
            active_agent="Studio",
            current_activity="Execution is blocked until required setup is complete",
            blockers=blockers,
            events=(
                ExecutionEvent(
                    id=new_public_id("event", self._id_factory),
                    execution_id=execution_id,
                    kind=EventKind.BLOCKER,
                    level=EventLevel.WARNING,
                    message=blockers[0].message,
                    stage=ExecutionStage.REQUIREMENTS,
                    agent="Studio",
                    created_at=now,
                ),
            ),
            created_at=now,
            updated_at=now,
        )
        record = _ExecutionRecord(execution, CancellationToken())
        with self._lock:
            existing_id = self._by_approval.get(key)
            if existing_id is not None:
                return self._snapshot(self._records[existing_id].snapshot)
            self._records[execution.id] = record
            self._by_approval[key] = execution.id
            self._save(execution)
        return self._snapshot(execution)

    def _set_running_locked(
        self,
        record: _ExecutionRecord,
        stage: ExecutionStage,
        agent: str,
        progress: int,
        activity: str,
    ) -> None:
        if record.snapshot.status in TERMINAL_EXECUTION_STATUSES:
            return
        now = utc_now(self._clock)
        record.snapshot = record.snapshot.model_copy(
            update={
                "status": ExecutionStatus.RUNNING,
                "stage": stage,
                "active_agent": agent,
                "progress": progress,
                "current_activity": activity,
                "started_at": record.snapshot.started_at or now,
                "updated_at": now,
            }
        )
        self._save(record.snapshot)

    def _finish_locked(
        self,
        record: _ExecutionRecord,
        result: ExecutionResult,
        status: ExecutionStatus,
    ) -> None:
        if record.snapshot.status in TERMINAL_EXECUTION_STATUSES:
            return
        now = utc_now(self._clock)
        duration = monotonic() - record.started_monotonic if record.started_monotonic is not None else 0.0
        result = self._sanitize_result(result).model_copy(
            update={
                "duration_seconds": result.duration_seconds if result.duration_seconds is not None else duration,
                "completed_at": now,
                "final_stage": result.final_stage or ExecutionStage.COMPLETED,
            }
        )
        event = ExecutionEvent(
            id=new_public_id("event", self._id_factory),
            execution_id=record.snapshot.id,
            kind=EventKind.RESULT,
            level=EventLevel.INFO if status is ExecutionStatus.SUCCEEDED else EventLevel.WARNING,
            message=result.summary[:240],
            stage=ExecutionStage.COMPLETED if status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED} else record.snapshot.stage,
            agent="Product Judge" if status is ExecutionStatus.SUCCEEDED else "Studio",
            progress=100 if status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED} else record.snapshot.progress,
            created_at=now,
        )
        stage = ExecutionStage.COMPLETED if status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED} else record.snapshot.stage
        progress = 100 if status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED} else record.snapshot.progress
        record.snapshot = record.snapshot.model_copy(
            update={
                "status": status,
                "stage": stage,
                "active_agent": event.agent,
                "progress": progress,
                "current_activity": result.summary[:240],
                "events": append_bounded_event(record.snapshot.events, event, limit=self._event_limit),
                "result": result,
                "updated_at": now,
                "finished_at": now,
            }
        )
        self._save(record.snapshot)

    @staticmethod
    def _sanitize_result(result: ExecutionResult) -> ExecutionResult:
        return result.model_copy(
            update={
                "summary": sanitize_public_text(result.summary) or "Execution result recorded.",
                "outcome": sanitize_public_text(result.outcome) if result.outcome else None,
                "warnings": tuple(sanitize_public_text(item) for item in result.warnings),
                "errors": tuple(sanitize_public_text(item) for item in result.errors),
            }
        )

    def _cancelled_result(self) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            outcome="cancelled",
            summary="Execution was cancelled by the user.",
            warnings=("Execution cancelled by user.",),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=utc_now(self._clock),
        )

    @staticmethod
    def _safe_reference(reference: str) -> str:
        sanitized = sanitize_public_text(reference).casefold()
        sanitized = sanitized.replace("[redacted]", "redacted")
        sanitized = re.sub(r"[^a-z0-9._:-]+", "-", sanitized).strip("-._:")
        return sanitized[:80] or "artifact-reference"

    def _save(self, snapshot: ProjectExecution) -> None:
        self._store.save(self._snapshot(snapshot))

    @staticmethod
    def _snapshot(snapshot: ProjectExecution) -> ProjectExecution:
        return snapshot.model_copy()

    def _adapter(self, mode: ExecutionMode) -> ProjectExecutionAdapter | None:
        if mode is ExecutionMode.FAKE:
            return self._fake_adapter
        return self._production_adapter

    @staticmethod
    def _approval_key(brief: ProjectBrief) -> tuple[str, str, str]:
        if brief.approval_fingerprint is None:
            raise ExecutionServiceError("brief_not_approved")
        return brief.order_id, brief.id, brief.approval_fingerprint

    @staticmethod
    def _domain_blockers(brief: ProjectBrief, handoff: AgentHandoff | None) -> tuple[ExecutionBlocker, ...]:
        blockers: list[ExecutionBlocker] = []
        if brief.open_questions:
            blockers.append(UNRESOLVED_QUESTIONS)
        if str(brief.elena_design_choice) == "ElenaDesignChoice.UNDECIDED" or brief.elena_design_choice.value == "undecided":
            blockers.append(ELENA_CHOICE_REQUIRED)
        if brief.recommended_stack.frontend != "React + Vite":
            blockers.append(UNSUPPORTED_PRODUCT_TYPE)
        if not verify_brief_approval(brief):
            blockers.append(BRIEF_NOT_APPROVED)
        if handoff is not None and (handoff.order_id != brief.order_id or handoff.brief_id != brief.id):
            blockers.append(BRIEF_NOT_APPROVED)
        return tuple(blockers)

    def _validate_handoff(self, brief: ProjectBrief, handoff: AgentHandoff) -> None:
        readiness = self.check_readiness(brief, handoff, mode=self._mode)
        if any(item.code in {"brief_not_approved", "unresolved_questions", "elena_choice_required", "unsupported_product_type"} for item in readiness.blockers):
            raise ExecutionServiceError(readiness.blockers[0].code)
        try:
            self._approval_key(brief)
        except ExecutionServiceError:
            raise
        except BriefServiceError:
            raise ExecutionServiceError("brief_not_approved") from None
