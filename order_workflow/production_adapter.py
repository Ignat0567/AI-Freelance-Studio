from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol

from .execution_plan import ProductionExecutionPackage, build_production_execution_package
from .executors import CancellationToken, ExecutionEventSink, ExecutionRequest
from .models import ArtifactKind, ExecutionResult, ExecutionStage, EventLevel, ProjectBrief, TestSummary
from .readiness import (
    MODEL_NOT_SELECTED,
    PROVIDER_NOT_CONFIGURED,
    QA_TOOLS_UNAVAILABLE,
    LIVE_EXECUTION_OPT_IN_REQUIRED,
    OPENCODE_UNAVAILABLE,
    WORKSPACE_NOT_WRITABLE,
    WORKSPACE_ROOT_UNAVAILABLE,
    ReadinessResult,
)
from .workspace import plan_project_workspace, reserve_owned_project_workspace, summarize_generated_workspace, validate_owned_project_workspace


def live_opencode_execution_enabled(environ: dict[str, str] | None = None) -> bool:
    return (environ or os.environ).get("FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION") == "1"


@dataclass(frozen=True, slots=True)
class OpenCodeExecutionResult:
    success: bool
    summary: str
    warnings: tuple[str, ...] = ()


class OpenCodeExecutionClient(Protocol):
    def check_readiness(self) -> ReadinessResult: ...

    def execute_project_prompt(
        self,
        prompt: str,
        workspace_path: Path,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
    ) -> OpenCodeExecutionResult: ...


class UnavailableOpenCodeExecutionClient:
    def check_readiness(self) -> ReadinessResult:
        return ReadinessResult.blocked(OPENCODE_UNAVAILABLE)

    def execute_project_prompt(self, prompt: str, workspace_path: Path, event_sink: ExecutionEventSink, cancellation: CancellationToken) -> OpenCodeExecutionResult:
        raise RuntimeError("OpenCode client is unavailable")


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


class LiveOpenCodeExecutionAdapter(ProductionProjectExecutionAdapter):
    def __init__(
        self,
        *,
        provider_name: str | None,
        model_name: str | None,
        workspace_root: str | Path | None,
        opencode_client: OpenCodeExecutionClient | None = None,
        qa_commands: tuple[str, ...] = ("QA not run in first live MVP",),
        environ: dict[str, str] | None = None,
        writable_probe: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, model_name=model_name, workspace_root=workspace_root, qa_commands=qa_commands, dry_run=True, writable_probe=writable_probe)
        self._opencode_client = opencode_client or UnavailableOpenCodeExecutionClient()
        self._environ = environ

    def check_readiness(self, brief: ProjectBrief) -> ReadinessResult:
        blockers = list(super().check_readiness(brief).blockers)
        if not live_opencode_execution_enabled(self._environ):
            blockers.append(LIVE_EXECUTION_OPT_IN_REQUIRED)
        blockers.extend(self._opencode_client.check_readiness().blockers)
        if blockers:
            return ReadinessResult.blocked(*blockers)
        return ReadinessResult.ready_result()

    def execute(self, request: ExecutionRequest, event_sink: ExecutionEventSink, cancellation: CancellationToken) -> ExecutionResult:
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=10, message="Preparing live OpenCode execution")
        package = self.prepare_execution(request)
        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=20, message="Creating project workspace")
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=request.execution_id, brief_fingerprint=request.brief.approval_fingerprint)
        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=30, message="Writing execution package")
        (workspace.project_path / "execution_prompt.md").write_text(package.prompt, encoding="utf-8")
        (workspace.project_path / "execution_package.json").write_text(package.to_json(), encoding="utf-8")
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        validate_owned_project_workspace(workspace, order_id=request.brief.order_id, execution_id=request.execution_id)
        event_sink.emit(stage=ExecutionStage.IMPLEMENTATION, agent="OpenCode", progress=45, message="Sending implementation prompt to OpenCode")
        try:
            result = self._opencode_client.execute_project_prompt(package.prompt, workspace.project_path, event_sink, cancellation)
        except Exception:
            return ExecutionResult(success=False, outcome="failed", summary="Live OpenCode execution failed before completion.", test_summary=TestSummary(failed=1), errors=("opencode_execution_failed",), final_stage=ExecutionStage.IMPLEMENTATION, completed_at=request.brief.updated_at)
        if cancellation.is_cancelled():
            cancelled = _cancelled_result(request)
            return cancelled.model_copy(update={"warnings": (*cancelled.warnings, "OpenCode cancellation was requested; no unrelated processes were terminated.")})
        event_sink.emit(stage=ExecutionStage.VERIFICATION, agent="BugCatcher", progress=80, message="QA not run in first live MVP", level=EventLevel.WARNING)
        summary = summarize_generated_workspace(workspace)
        studio_package_files = {"execution_package.json", "execution_prompt.md"}
        top_level_entries = set(summary.get("top_level_entries", ()))
        app_file_count = max(0, int(summary["files_created"]) - len(top_level_entries.intersection(studio_package_files)))
        status_line = "Live OpenCode execution completed." if result.success else "Live OpenCode execution failed."
        delivery_report = (
            f"{status_line}\n"
            "QA status: not_run.\n"
            f"Generated app files detected: {app_file_count}.\n"
            f"Workspace files inspected: {summary['files_created']}.\n"
        )
        (workspace.project_path / "delivery_report.md").write_text(delivery_report, encoding="utf-8")
        workspace_summary = (
            f"Workspace contains {summary['files_created']} non-marker files; "
            f"generated app files detected: {app_file_count}."
        )
        delivery_summary = (
            "Live OpenCode execution completed; QA was not run."
            if result.success
            else "Live OpenCode execution failed; QA was not run."
        )
        artifacts = (
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="generated_project_summary.json", summary=workspace_summary, reference="generated-project-summary-json"),
            event_sink.artifact(kind=ArtifactKind.AGENT_HANDOFF, name="execution_package.json", summary="Live execution package written to the owned workspace.", reference="execution-package-json"),
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="execution_prompt.md", summary="Implementation prompt sent to OpenCode.", reference="execution-prompt-md"),
            event_sink.artifact(kind=ArtifactKind.DELIVERY_REPORT, name="delivery_report.md", summary=delivery_summary, reference="delivery-report-md"),
        )
        event_sink.emit(stage=ExecutionStage.COMPLETED, agent="Product Judge", progress=100, message="Delivery summary prepared")
        return ExecutionResult(
            success=result.success,
            outcome="live_generated" if result.success else "failed",
            summary="Project generated by live OpenCode execution. QA was not run in this live opt-in commit." if result.success else "Live OpenCode execution did not complete successfully.",
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(skipped=1),
            warnings=("QA was not run in this live opt-in commit.", *result.warnings),
            errors=() if result.success else ("opencode_execution_failed",),
            final_stage=ExecutionStage.COMPLETED if result.success else ExecutionStage.IMPLEMENTATION,
            completed_at=request.brief.updated_at,
        )


def _cancelled_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        success=False,
        outcome="cancelled",
        summary="Production dry run was cancelled before execution package preparation.",
        warnings=("Execution cancelled by user.",),
        final_stage=ExecutionStage.COMPLETED,
        completed_at=request.brief.updated_at,
    )
