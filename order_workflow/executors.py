from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import sleep
from typing import Protocol

from .models import (
    AgentHandoff,
    ArtifactKind,
    ExecutionArtifact,
    ExecutionEvent,
    ExecutionResult,
    ExecutionStage,
    EventKind,
    EventLevel,
    ProjectBrief,
    TestSummary,
)
from .readiness import ReadinessResult, readiness_blocker


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class ExecutionEventSink(Protocol):
    def emit(
        self,
        *,
        stage: ExecutionStage,
        agent: str,
        progress: int,
        message: str,
        level: EventLevel = EventLevel.INFO,
        details: tuple[str, ...] = (),
        kind: EventKind = EventKind.ACTIVITY,
    ) -> None: ...

    def artifact(self, *, kind: ArtifactKind, name: str, summary: str, reference: str) -> ExecutionArtifact: ...


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    brief: ProjectBrief
    handoff: AgentHandoff
    execution_id: str
    title: str = ""


class ProjectExecutionAdapter(Protocol):
    def check_readiness(self, brief: ProjectBrief) -> ReadinessResult: ...

    def execute(
        self,
        request: ExecutionRequest,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
    ) -> ExecutionResult: ...


@dataclass(frozen=True, slots=True)
class FakeExecutorConfig:
    fail: bool = False
    raise_unexpected: bool = False
    readiness_blocker: bool = False
    step_delay_seconds: float = 0.0


class FakeProjectExecutionAdapter:
    def __init__(self, config: FakeExecutorConfig | None = None) -> None:
        self.config = config or FakeExecutorConfig()
        self.observed_cancellation = False

    def check_readiness(self, brief: ProjectBrief) -> ReadinessResult:
        if self.config.readiness_blocker:
            return ReadinessResult.blocked(
                readiness_blocker(
                    "execution_provider_not_configured",
                    "Fake execution is configured to simulate a provider readiness blocker.",
                )
            )
        return ReadinessResult.ready_result()

    def execute(
        self,
        request: ExecutionRequest,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
    ) -> ExecutionResult:
        if self.config.raise_unexpected:
            raise RuntimeError("raw provider stack trace with token=secret-value")
        stages = (
            (ExecutionStage.REQUIREMENTS, "Alex", 15, "Reviewing approved requirements"),
            (ExecutionStage.PLANNING, "Codex", 35, "Preparing implementation plan"),
            (ExecutionStage.IMPLEMENTATION, "Codex", 65, "Creating simulated project files"),
            (ExecutionStage.VERIFICATION, "BugCatcher", 85, "Running simulated verification checks"),
        )
        for stage, agent, progress, message in stages:
            if cancellation.is_cancelled():
                self.observed_cancellation = True
                return self._cancelled(request, event_sink)
            event_sink.emit(stage=stage, agent=agent, progress=progress, message=message)
            if self.config.step_delay_seconds > 0 and cancellation.wait(self.config.step_delay_seconds):
                self.observed_cancellation = True
                return self._cancelled(request, event_sink)
        artifacts = (
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="project_summary.md", summary="Simulated generated project summary.", reference="project-summary-md"),
            event_sink.artifact(kind=ArtifactKind.AGENT_HANDOFF, name="handoff.json", summary="Simulated compact Alex to Codex handoff.", reference="handoff-json"),
            event_sink.artifact(kind=ArtifactKind.TEST_SUMMARY, name="verification_report.json", summary="Simulated verification report with four passing checks.", reference="verification-report-json"),
            event_sink.artifact(kind=ArtifactKind.DELIVERY_REPORT, name="delivery_report.md", summary="Simulated delivery report for UI development.", reference="delivery-report-md"),
        )
        if self.config.fail:
            event_sink.emit(
                stage=ExecutionStage.VERIFICATION,
                agent="BugCatcher",
                progress=90,
                message="Simulated verification failure",
                level=EventLevel.ERROR,
            )
            return ExecutionResult(
                success=False,
                outcome="failed",
                summary="Fake execution failed during simulated verification.",
                artifact_ids=tuple(item.id for item in artifacts),
                test_summary=TestSummary(passed=3, failed=1),
                errors=("simulated_verification_failure",),
                final_stage=ExecutionStage.VERIFICATION,
                completed_at=request.brief.updated_at,
            )
        event_sink.emit(stage=ExecutionStage.COMPLETED, agent="Product Judge", progress=100, message="Simulated delivery accepted")
        return ExecutionResult(
            success=True,
            outcome="succeeded",
            summary="Fake execution completed with simulated artifacts and verification summary.",
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(passed=4, failed=0),
            warnings=("Simulated artifacts only; no real project files were generated.",),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=request.brief.updated_at,
        )

    @staticmethod
    def _cancelled(request: ExecutionRequest, event_sink: ExecutionEventSink) -> ExecutionResult:
        event_sink.emit(
            stage=ExecutionStage.COMPLETED,
            agent="Studio",
            progress=100,
            message="Execution cancelled by user",
            level=EventLevel.WARNING,
        )
        return ExecutionResult(
            success=False,
            outcome="cancelled",
            summary="Fake execution was cancelled before completion.",
            test_summary=TestSummary(),
            warnings=("Execution cancelled by user.",),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=request.brief.updated_at,
        )
