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
    # True when the last repair call was stopped by its own clock rather than finishing.
    # Callers turn a gate failure into a summary, and "the page does not match the design"
    # is a different statement from "we never got to re-check it".
    fix_timed_out: bool = False


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
    model: str | None = None,
    fix_timeout: int | None = None,
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
    fix_timed_out = False
    # Only forwarded when a caller actually set one, so the many clients and test doubles
    # implementing execute_project_prompt without the parameter keep working unchanged.
    fix_kwargs: dict = {"model": model}
    if fix_timeout is not None:
        fix_kwargs["timeout"] = fix_timeout
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
            fix_timed_out = False
            try:
                fix_result = opencode_client.execute_project_prompt(fix_prompt_builder(qa_outcome), workspace_path, event_sink, cancellation, **fix_kwargs)
            except Exception as exc:
                # Previously a bare `break`: the loop ended with no trace of why, so the
                # phase reported the gate's own finding as the reason and whoever read it
                # went looking at the code instead of at the provider call that never ran.
                event_sink.emit(
                    stage=stage,
                    agent=agent,
                    progress=65,
                    message="The repair call could not be made, so the repair loop stopped early.",
                    level=EventLevel.WARNING,
                    details=(str(exc)[:2000],),
                )
                break
            if not fix_result.success:
                fix_timed_out = bool(getattr(fix_result, "timed_out", False))
                event_sink.emit(
                    stage=stage,
                    agent=agent,
                    progress=65,
                    message=(
                        "The repair call ran out of time and was stopped before it finished."
                        if fix_timed_out
                        else "The repair call did not complete, so the repair loop stopped early."
                    ),
                    level=EventLevel.WARNING,
                    details=((getattr(fix_result, "summary", "") or "")[:2000],),
                )
                # Out of clock is not a verdict, so the remaining attempt is still worth
                # spending. Any other failure (expired login, rejected model, provider
                # error) repeats identically on a retry, so that one still stops here.
                if not fix_timed_out:
                    break
                continue
            qa_outcome = qa_runner(qa_commands, qa_cwd)
        if cancellation.is_cancelled():
            return RepairLoopResult(qa_outcome=qa_outcome, attempts=attempts, qa_status_message="", cancelled=True, fix_timed_out=fix_timed_out)

    qa_passed = qa_outcome.passed if qa_outcome is not None else False
    if qa_outcome is None:
        qa_status_message = "QA not run because OpenCode execution did not succeed."
    elif not qa_commands:
        qa_status_message = "No QA commands were configured; nothing to verify."
    elif qa_outcome.passed:
        qa_status_message = "QA passed." if attempts == 0 else f"QA passed after {attempts} repair attempt(s)."
    else:
        qa_status_message = f"QA failed after {attempts} repair attempt(s)."
        if fix_timed_out:
            qa_status_message += " The last repair call was stopped by its time limit, so the finding was never re-checked."
    event_sink.emit(
        stage=stage,
        agent=agent,
        progress=80,
        message=qa_status_message,
        level=EventLevel.INFO if qa_passed else EventLevel.ERROR if qa_outcome is not None else EventLevel.WARNING,
    )
    return RepairLoopResult(qa_outcome=qa_outcome, attempts=attempts, qa_status_message=qa_status_message, cancelled=False, fix_timed_out=fix_timed_out)
