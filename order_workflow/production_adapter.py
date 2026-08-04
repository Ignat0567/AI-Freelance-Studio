from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol

from ai_utils import ask_studio_ai_with_history

from .execution_plan import ProductionExecutionPackage, build_production_execution_package
from .executors import CancellationToken, ExecutionEventSink, ExecutionRequest
from .models import ArtifactKind, ExecutionResult, ExecutionStage, EventLevel, ProjectBrief, TestSummary
from .qa_runner import QAOutcome, run_qa_commands
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
from .website_generation import build_website_execution_plan, detect_cinematic_website_intent
from .workspace import plan_project_workspace, reserve_owned_project_workspace, scan_meaningful_generated_artifacts, summarize_generated_workspace, validate_owned_project_workspace

MAX_QA_REPAIR_ATTEMPTS = 2  # matches the legacy pipeline's own MAX_REVIEW_ITERATIONS precedent


def live_opencode_execution_enabled(environ: dict[str, str] | None = None) -> bool:
    return (environ or os.environ).get("FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION") == "1"


@dataclass(frozen=True, slots=True)
class OpenCodeExecutionResult:
    success: bool
    summary: str
    warnings: tuple[str, ...] = ()
    outcome: str = "generated"
    errors: tuple[str, ...] = ()
    timed_out: bool = False
    meaningful_artifacts: tuple[str, ...] = ()


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
        qa_commands: tuple[str, ...] = ("npm test",),
        environ: dict[str, str] | None = None,
        writable_probe: Callable[[Path], bool] | None = None,
        website_section_ai_ask: Callable[[str], str] | None = None,
        qa_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, model_name=model_name, workspace_root=workspace_root, qa_commands=qa_commands, dry_run=True, writable_probe=writable_probe)
        self._opencode_client = opencode_client or UnavailableOpenCodeExecutionClient()
        self._environ = environ
        self._website_section_ai_ask = website_section_ai_ask or self._default_website_section_ai_ask
        self._qa_runner = qa_runner or run_qa_commands

    def _default_website_section_ai_ask(self, prompt: str) -> str:
        return ask_studio_ai_with_history(self.provider_name, self.model_name, prompt, [], temperature=0.3)

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
        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=20, message="Creating project workspace")
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=request.execution_id, brief_fingerprint=request.brief.approval_fingerprint)
        if detect_cinematic_website_intent(request.brief):
            event_sink.emit(stage=ExecutionStage.PLANNING, agent="Elena", progress=25, message="Selecting cinematic website sections")
            # website_sections' scaffold package.json has no "test" script (it's a
            # static site with no unit tests scaffolded) -- self.qa_commands' generic
            # default ("npm test") would always fail here, so use the one command
            # that's actually meaningful for this curated project shape.
            package = build_website_execution_plan(
                execution_id=request.execution_id,
                brief=request.brief,
                handoff=request.handoff,
                workspace=workspace,
                provider_name=self.provider_name,
                model_name=self.model_name,
                qa_commands=("npm run build",),
                ai_ask=self._website_section_ai_ask,
            )
        else:
            package = self.prepare_execution(request)
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

        qa_outcome: QAOutcome | None = None
        qa_attempts = 0
        if result.success:
            qa_cwd = workspace.project_path / "frontend" if detect_cinematic_website_intent(request.brief) else workspace.project_path
            event_sink.emit(stage=ExecutionStage.VERIFICATION, agent="BugCatcher", progress=60, message="Running QA commands")
            qa_outcome = self._qa_runner(package.qa_commands, qa_cwd)
            while not qa_outcome.passed and qa_attempts < MAX_QA_REPAIR_ATTEMPTS and not cancellation.is_cancelled():
                qa_attempts += 1
                event_sink.emit(
                    stage=ExecutionStage.VERIFICATION,
                    agent="BugCatcher",
                    progress=65,
                    message=f"QA failed; asking Codex to fix (attempt {qa_attempts} of {MAX_QA_REPAIR_ATTEMPTS})",
                    level=EventLevel.WARNING,
                    details=(qa_outcome.failure_summary()[:2000],),
                )
                try:
                    fix_result = self._opencode_client.execute_project_prompt(_build_qa_fix_prompt(qa_outcome), workspace.project_path, event_sink, cancellation)
                except Exception:
                    break
                if not fix_result.success:
                    break
                qa_outcome = self._qa_runner(package.qa_commands, qa_cwd)
            if cancellation.is_cancelled():
                cancelled = _cancelled_result(request)
                return cancelled.model_copy(update={"warnings": (*cancelled.warnings, "OpenCode cancellation was requested; no unrelated processes were terminated.")})

        qa_passed = qa_outcome.passed if qa_outcome is not None else False
        if qa_outcome is None:
            qa_status_message = "QA not run because OpenCode execution did not succeed."
        elif not package.qa_commands:
            qa_status_message = "No QA commands were configured; nothing to verify."
        elif qa_outcome.passed:
            qa_status_message = "QA passed." if qa_attempts == 0 else f"QA passed after {qa_attempts} repair attempt(s)."
        else:
            qa_status_message = f"QA failed after {qa_attempts} repair attempt(s)."
        event_sink.emit(
            stage=ExecutionStage.VERIFICATION,
            agent="BugCatcher",
            progress=80,
            message=qa_status_message,
            level=EventLevel.INFO if qa_passed else EventLevel.ERROR if qa_outcome is not None else EventLevel.WARNING,
        )

        summary = summarize_generated_workspace(workspace)
        meaningful_artifacts = scan_meaningful_generated_artifacts(workspace)
        studio_package_files = {"execution_package.json", "execution_prompt.md"}
        top_level_entries = set(summary.get("top_level_entries", ()))
        app_file_count = max(0, int(summary["files_created"]) - len(top_level_entries.intersection(studio_package_files)))
        if result.outcome == "generated_needs_review":
            status_line = "OpenCode created project files but did not exit before timeout. The workspace requires review."
        elif result.outcome == "timed_out_without_artifacts":
            status_line = "OpenCode did not finish and no generated project files were detected."
        elif result.success:
            status_line = "Live OpenCode execution completed."
        else:
            status_line = "Live OpenCode execution failed."
        delivery_report = (
            f"{status_line}\n"
            f"QA status: {qa_status_message}\n"
            f"Generated app files detected: {app_file_count}.\n"
            f"Meaningful artifacts detected: {len(meaningful_artifacts)}.\n"
            f"Workspace files inspected: {summary['files_created']}.\n"
        )
        (workspace.project_path / "delivery_report.md").write_text(delivery_report, encoding="utf-8")
        workspace_summary = (
            f"Workspace contains {summary['files_created']} non-marker files; "
            f"generated app files detected: {app_file_count}."
        )
        delivery_summary = (
            "OpenCode created project files but timed out; manual review required. "
            if result.outcome == "generated_needs_review"
            else "OpenCode timed out without generated project files. "
            if result.outcome == "timed_out_without_artifacts"
            else "Live OpenCode execution completed. "
            if result.success
            else "Live OpenCode execution failed. "
        ) + qa_status_message
        artifacts = (
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="generated_project_summary.json", summary=workspace_summary, reference="generated-project-summary-json"),
            event_sink.artifact(kind=ArtifactKind.AGENT_HANDOFF, name="execution_package.json", summary="Live execution package written to the owned workspace.", reference="execution-package-json"),
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="execution_prompt.md", summary="Implementation prompt sent to OpenCode.", reference="execution-prompt-md"),
            event_sink.artifact(kind=ArtifactKind.DELIVERY_REPORT, name="delivery_report.md", summary=delivery_summary, reference="delivery-report-md"),
        )
        event_sink.emit(stage=ExecutionStage.COMPLETED, agent="Product Judge", progress=100, message="Delivery summary prepared")

        if not result.success:
            outcome_value = "timed_out_without_artifacts" if result.outcome == "timed_out_without_artifacts" else "failed"
            final_stage_value = ExecutionStage.IMPLEMENTATION
            errors_value = result.errors or ("opencode_execution_failed",)
        elif not qa_passed:
            outcome_value = "qa_failed"
            final_stage_value = ExecutionStage.VERIFICATION
            errors_value = ("qa_failed",)
        else:
            outcome_value = result.outcome
            final_stage_value = ExecutionStage.COMPLETED
            errors_value = ()
        if result.outcome == "generated_needs_review":
            summary_value = f"OpenCode created project files but did not exit before timeout. {qa_status_message}"
        elif result.success:
            summary_value = f"Project generated by live OpenCode execution. {qa_status_message}"
        elif result.outcome == "timed_out_without_artifacts":
            summary_value = "OpenCode did not finish and no generated project files were detected."
        else:
            summary_value = "Live OpenCode execution did not complete successfully."

        return ExecutionResult(
            success=result.success and qa_passed,
            outcome=outcome_value,
            summary=summary_value,
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(skipped=1),
            warnings=(qa_status_message, *result.warnings),
            errors=errors_value,
            final_stage=final_stage_value,
            completed_at=request.brief.updated_at,
        )


def _build_qa_fix_prompt(qa_outcome: QAOutcome) -> str:
    return (
        "The QA commands you were asked to satisfy are now failing. Fix the code so they pass.\n"
        "Do not rewrite unrelated parts of the project.\n\n"
        f"{qa_outcome.failure_summary()}\n\n"
        "After fixing, stop and exit. Do not keep rewriting files."
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
