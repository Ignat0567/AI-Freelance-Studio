from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .executors import CancellationToken, ExecutionEventSink
from .models import ExecutionStage, EventLevel
from .qa_runner import QAOutcome


@dataclass(frozen=True, slots=True)
class RepairLoopResult:
    qa_outcome: QAOutcome | None
    attempts: int
    qa_status_message: str
    cancelled: bool


def run_qa_repair_loop(
    *,
    opencode_client,
    opencode_succeeded: bool,
    workspace_path: Path,
    qa_commands: tuple[str, ...],
    qa_cwd: Path,
    qa_runner: Callable[[tuple[str, ...], Path], QAOutcome],
    event_sink: ExecutionEventSink,
    cancellation: CancellationToken,
    stage: ExecutionStage,
    agent: str,
    max_attempts: int,
    fix_prompt_builder: Callable[[QAOutcome], str],
) -> RepairLoopResult:
    """Run QA, and on failure re-prompt the coding provider up to max_attempts times.

    Extracted verbatim from LiveOpenCodeExecutionAdapter.execute() so the phased
    pipeline can reuse the exact same shape per-phase instead of only at the end;
    stage/agent/max_attempts/fix_prompt_builder are now parameters instead of
    hardcoded VERIFICATION/BugCatcher/MAX_QA_REPAIR_ATTEMPTS/_build_qa_fix_prompt,
    but the legacy caller passes those same values so its behavior is unchanged.
    """
    qa_outcome: QAOutcome | None = None
    attempts = 0
    if opencode_succeeded:
        event_sink.emit(stage=stage, agent=agent, progress=60, message="Running QA commands")
        qa_outcome = qa_runner(qa_commands, qa_cwd)
        while not qa_outcome.passed and attempts < max_attempts and not cancellation.is_cancelled():
            attempts += 1
            event_sink.emit(
                stage=stage,
                agent=agent,
                progress=65,
                message=f"QA failed; asking Codex to fix (attempt {attempts} of {max_attempts})",
                level=EventLevel.WARNING,
                details=(qa_outcome.failure_summary()[:2000],),
            )
            try:
                fix_result = opencode_client.execute_project_prompt(fix_prompt_builder(qa_outcome), workspace_path, event_sink, cancellation)
            except Exception:
                break
            if not fix_result.success:
                break
            qa_outcome = qa_runner(qa_commands, qa_cwd)
        if cancellation.is_cancelled():
            return RepairLoopResult(qa_outcome=qa_outcome, attempts=attempts, qa_status_message="", cancelled=True)

    qa_passed = qa_outcome.passed if qa_outcome is not None else False
    if qa_outcome is None:
        qa_status_message = "QA not run because OpenCode execution did not succeed."
    elif not qa_commands:
        qa_status_message = "No QA commands were configured; nothing to verify."
    elif qa_outcome.passed:
        qa_status_message = "QA passed." if attempts == 0 else f"QA passed after {attempts} repair attempt(s)."
    else:
        qa_status_message = f"QA failed after {attempts} repair attempt(s)."
    event_sink.emit(
        stage=stage,
        agent=agent,
        progress=80,
        message=qa_status_message,
        level=EventLevel.INFO if qa_passed else EventLevel.ERROR if qa_outcome is not None else EventLevel.WARNING,
    )
    return RepairLoopResult(qa_outcome=qa_outcome, attempts=attempts, qa_status_message=qa_status_message, cancelled=False)
