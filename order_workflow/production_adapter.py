from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from .execution_plan import ProductionExecutionPackage, build_production_execution_package
from .executors import CancellationToken, ExecutionEventSink, ExecutionRequest
from .models import ArtifactKind, ExecutionResult, ExecutionStage, EventLevel, ProjectBrief, TestSummary
from .readiness import (
    MODEL_NOT_SELECTED,
    PROVIDER_NOT_CONFIGURED,
    QA_TOOLS_UNAVAILABLE,
    WORKSPACE_NOT_WRITABLE,
    WORKSPACE_ROOT_UNAVAILABLE,
    ReadinessResult,
)
from .workspace import plan_project_workspace


class ProductionProjectExecutionAdapter:
    """Dry-run boundary for future live OpenCode, QA, and audit execution."""

    def __init__(
        self,
        *,
        provider_name: str | None,
        model_name: str | None,
        workspace_root: str | Path | None,
        qa_commands: tuple[str, ...] = ("npm test",),
        dry_run: bool = True,
        writable_probe: Callable[[Path], bool] | None = None,
    ) -> None:
        if not dry_run:
            raise ValueError("ProductionProjectExecutionAdapter only supports dry-run execution")
        self.provider_name = (provider_name or "").strip()
        self.model_name = (model_name or "").strip()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else None
        self.qa_commands = tuple(item.strip() for item in qa_commands if item.strip())
        self._writable_probe = writable_probe or _is_writable_directory

    def check_readiness(self, brief: ProjectBrief) -> ReadinessResult:
        blockers = []
        if not self.provider_name:
            blockers.append(PROVIDER_NOT_CONFIGURED)
        if not self.model_name:
            blockers.append(MODEL_NOT_SELECTED)
        if self.workspace_root is None or not self.workspace_root.is_dir():
            blockers.append(WORKSPACE_ROOT_UNAVAILABLE)
        elif not self._writable_probe(self.workspace_root):
            blockers.append(WORKSPACE_NOT_WRITABLE)
        if not self.qa_commands:
            blockers.append(QA_TOOLS_UNAVAILABLE)
        if blockers:
            return ReadinessResult.blocked(*blockers)
        return ReadinessResult.ready_result()

    def prepare_execution(self, request: ExecutionRequest) -> ProductionExecutionPackage:
        if self.workspace_root is None:
            raise RuntimeError("workspace root is not configured")
        workspace = plan_project_workspace(self.workspace_root, order_id=request.brief.order_id, brief_id=request.brief.id)
        return build_production_execution_package(
            execution_id=request.execution_id,
            brief=request.brief,
            handoff=request.handoff,
            workspace=workspace,
            provider_name=self.provider_name,
            model_name=self.model_name,
            qa_commands=self.qa_commands,
        )

    def execute(
        self,
        request: ExecutionRequest,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
    ) -> ExecutionResult:
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        package = self.prepare_execution(request)
        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=20, message="Prepared production execution package")
        event_sink.emit(stage=ExecutionStage.IMPLEMENTATION, agent="OpenCode", progress=45, message="Dry-run mode: live coding provider was not invoked")
        event_sink.emit(stage=ExecutionStage.VERIFICATION, agent="BugCatcher", progress=75, message="Dry-run mode: QA commands were not run", level=EventLevel.WARNING)
        artifacts = (
            event_sink.artifact(kind=ArtifactKind.AGENT_HANDOFF, name="production_handoff.json", summary="Dry-run production execution package for the approved brief.", reference="production-handoff-json"),
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="execution_prompt.md", summary="Prompt prepared for the future OpenCode execution boundary.", reference="execution-prompt-md"),
            event_sink.artifact(kind=ArtifactKind.TEST_SUMMARY, name="qa_plan.json", summary="QA commands selected for future production execution; not run in dry-run mode.", reference="qa-plan-json"),
            event_sink.artifact(kind=ArtifactKind.DELIVERY_REPORT, name="dry_run_report.md", summary=f"Production dry run prepared for {package.provider_name} using {package.model_name}.", reference="dry-run-report-md"),
        )
        event_sink.emit(stage=ExecutionStage.COMPLETED, agent="Product Judge", progress=100, message="Dry-run production package prepared")
        return ExecutionResult(
            success=True,
            outcome="dry_run_prepared",
            summary="Production execution dry run prepared the handoff, prompt, workspace plan, and QA plan without invoking live providers.",
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(skipped=len(package.qa_commands)),
            warnings=("Dry-run only; no project files were generated and no QA commands were run.",),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=request.brief.updated_at,
        )


def _is_writable_directory(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK)


def _cancelled_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        success=False,
        outcome="cancelled",
        summary="Production dry run was cancelled before execution package preparation.",
        warnings=("Execution cancelled by user.",),
        final_stage=ExecutionStage.COMPLETED,
        completed_at=request.brief.updated_at,
    )
