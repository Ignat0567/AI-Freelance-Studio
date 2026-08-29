"""What the repair loop leaves behind when it gives up.

Every repair *request* carried the gate's findings. The event that ends the loop carried
none, so a failed run recorded only that it failed: on 2026-08-26 the reading-journal order
ended with "QA failed after 2 repair attempt(s)" and nothing anywhere -- not the event
stream, not the transcript -- said whether the 3px overflow its last repair was chasing had
shrunk, moved, or come back. The one gate result nobody can reconstruct afterwards is the
final one.
"""

from __future__ import annotations

import pytest

from order_workflow.executors import CancellationToken
from order_workflow.models import ExecutionStage
from order_workflow.phase_repair import run_qa_repair_loop
from order_workflow.qa_runner import QACommandResult, QAOutcome


pytestmark = pytest.mark.unit


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
        self.calls += 1

        class _Result:
            success = True
            usage = None
            timed_out = False
            summary = ""

        return _Result()


def _failing(text: str) -> QAOutcome:
    return QAOutcome(passed=False, results=(QACommandResult(command="visual", exit_code=1, stdout_tail=text, stderr_tail="", duration=9.0),))


def _run(outcomes: list[QAOutcome], sink: _RecordingSink, tmp_path):
    remaining = list(outcomes)

    def qa_runner(commands, cwd):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return run_qa_repair_loop(
        opencode_client=_Client(),
        opencode_succeeded=True,
        workspace_path=tmp_path,
        qa_commands=("visual",),
        qa_cwd=tmp_path,
        qa_runner=qa_runner,
        event_sink=sink,
        cancellation=CancellationToken(),
        stage=ExecutionStage.UI_SHELL,
        agent="Elena",
        max_attempts=2,
        fix_prompt_builder=lambda outcome, budget_seconds=None: "fix it",
    )


def test_the_last_thing_the_gate_said_is_recorded_when_the_loop_gives_up(tmp_path):
    sink = _RecordingSink()

    _run([_failing("VISUAL CHECK FAILED:\n- cut off by 329px"), _failing("VISUAL CHECK FAILED:\n- cut off by 3px")], sink, tmp_path)

    closing = sink.events[-1]
    assert "QA failed after 2 repair attempt(s)." in closing["message"]
    assert closing["details"], "the run that failed says only that it failed"
    assert "cut off by 3px" in closing["details"][0]


def test_a_loop_that_succeeds_does_not_attach_a_stale_failure(tmp_path):
    sink = _RecordingSink()
    passing = QAOutcome(passed=True, results=(QACommandResult(command="visual", exit_code=0, stdout_tail="VISUAL CHECK PASSED", stderr_tail="", duration=9.0),))

    _run([_failing("VISUAL CHECK FAILED:\n- cut off by 329px"), passing], sink, tmp_path)

    closing = sink.events[-1]
    assert "QA passed after 1 repair attempt(s)." in closing["message"]
    assert closing["details"] == ()


def test_a_repair_the_provider_refused_is_not_the_app_s_fault(tmp_path):
    """b05, 2026-08-29: the visual gate found one overflow, the repair call came back with
    "You've hit your session limit", the loop stopped after one of its three attempts, and the
    run was filed as `generated_code` -- the column that is supposed to measure the quality of
    what the CLI writes. The provider's outage was recorded as the app's defect."""
    from order_workflow.failure_cause import classify_failure_cause

    class _RefusingClient:
        def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
            class _Result:
                success = False
                timed_out = False
                errors = ("claude_code_process_failed",)
                summary = "Claude Code execution failed: You've hit your session limit"
                usage = None

            return _Result()

    sink = _RecordingSink()
    result = run_qa_repair_loop(
        opencode_client=_RefusingClient(),
        opencode_succeeded=True,
        workspace_path=tmp_path,
        qa_commands=("visual",),
        qa_cwd=tmp_path,
        qa_runner=lambda commands, cwd: _failing("VISUAL CHECK FAILED"),
        event_sink=sink,
        cancellation=CancellationToken(),
        stage=ExecutionStage.UI_SHELL,
        agent="Elena",
        max_attempts=3,
        fix_prompt_builder=lambda outcome, budget_seconds=None: "fix it",
    )

    assert result.fix_error_code == "claude_code_process_failed"
    assert "could not run" in result.qa_status_message
    assert "session limit" in result.qa_status_message
    # And the code the caller puts first is what the classifier reads.
    assert classify_failure_cause((result.fix_error_code, "qa_failed")) == "provider"
    assert classify_failure_cause(("qa_failed",)) == "generated_code"
