from __future__ import annotations

import pytest

from order_workflow.qa_runner import QAOutcome, run_qa_commands

pytestmark = pytest.mark.unit


def test_run_qa_commands_passes_when_every_command_exits_zero(tmp_path):
    outcome = run_qa_commands(('python -c "print(1)"', 'python -c "pass"'), tmp_path)

    assert isinstance(outcome, QAOutcome)
    assert outcome.passed is True
    assert len(outcome.results) == 2
    assert all(result.exit_code == 0 for result in outcome.results)


def test_run_qa_commands_fails_when_any_command_exits_nonzero(tmp_path):
    outcome = run_qa_commands(('python -c "print(1)"', 'python -c "import sys; sys.exit(1)"'), tmp_path)

    assert outcome.passed is False
    assert outcome.results[0].exit_code == 0
    assert outcome.results[1].exit_code == 1


def test_run_qa_commands_empty_input_vacuously_passes(tmp_path):
    outcome = run_qa_commands((), tmp_path)

    assert outcome.passed is True
    assert outcome.results == ()


def test_qa_outcome_failure_summary_includes_only_failing_commands(tmp_path):
    outcome = run_qa_commands(
        ('python -c "print(1)"', 'python -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"'),
        tmp_path,
    )

    summary = outcome.failure_summary()

    assert "python -c \"print(1)\"" not in summary  # the passing command isn't included
    assert "Exit code: 3" in summary
    assert "boom" in summary


def test_run_qa_commands_uses_npm_cmd_on_windows(monkeypatch, tmp_path):
    import order_workflow.qa_runner as qa_runner_module

    captured = {}

    def _fake_run_command(argv, cwd, timeout=120):
        captured["argv"] = argv
        return {"exit_code": 0, "stdout_tail": "", "stderr_tail": "", "duration": 0.01}

    monkeypatch.setattr(qa_runner_module, "_run_command", _fake_run_command)
    monkeypatch.setattr(qa_runner_module, "_NPM_EXECUTABLE", "npm.cmd")

    run_qa_commands(("npm run build",), tmp_path)

    assert captured["argv"][0] == "npm.cmd"
