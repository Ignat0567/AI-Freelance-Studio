from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.state_continuity_check import (
    _CHECK_SCRIPT,
    run_state_continuity_check_in_docker,
)

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, qa_commands, cwd, *, image=None, timeout_seconds=None, docker_client_factory=None):
        self.calls.append({"commands": qa_commands, "cwd": cwd, "image": image})
        from order_workflow.qa_runner import QACommandResult, QAOutcome

        return QAOutcome(
            passed=True,
            results=(QACommandResult(command="state", exit_code=0, stdout_tail="STATE CONTINUITY CHECK PASSED", stderr_tail="", duration=1.0),),
        )


# --- what the script actually asserts --------------------------------------------------


def test_the_check_drives_controls_through_playwright_not_raw_value_assignment():
    # React's controlled inputs ignore a bare `.value = x` (no synthetic event fires), so an
    # in-page mutation would silently do nothing and every app would look broken.
    assert "handle.fill(" in _CHECK_SCRIPT
    assert "handle.selectOption(" in _CHECK_SCRIPT
    assert "handle.click()" in _CHECK_SCRIPT


def test_the_check_navigates_away_and_back_rather_than_reloading():
    # A reload would test persistence, which a shell legitimately may not have. The property
    # here is narrower: state must survive moving between screens.
    assert "goTo(page, elsewhere)" in _CHECK_SCRIPT
    assert "goTo(page, route)" in _CHECK_SCRIPT


def test_an_app_with_one_screen_is_skipped_not_failed():
    # With nothing to navigate between, the property is vacuous rather than violated.
    assert "routes.length < 2" in _CHECK_SCRIPT
    assert "STATE CONTINUITY CHECK SKIPPED" in _CHECK_SCRIPT


def test_a_control_that_refused_the_edit_is_not_reported_as_a_revert():
    # If the value never changed, there is nothing to lose on navigation, and reporting it
    # would blame the wrong defect.
    assert "updated.value !== control.value ? updated : null" in _CHECK_SCRIPT


def test_disabled_and_readonly_controls_are_left_alone():
    assert "!node.disabled" in _CHECK_SCRIPT
    assert "!node.readOnly" in _CHECK_SCRIPT


def test_the_failure_message_names_the_control_and_both_values():
    # The repair loop gets this text verbatim; "state is broken" is not actionable.
    assert 'was set to "${mutated.value}" but reverted to "${same.value}"' in _CHECK_SCRIPT
    assert "lift it into shared state" in _CHECK_SCRIPT


def test_the_preview_connection_is_retried_before_giving_up():
    # Same lesson as the visual check: a slow-starting preview server otherwise reports as a
    # broken app and burns both repair attempts on a problem that does not exist.
    assert "attempt < 15" in _CHECK_SCRIPT
    assert "never accepted a connection" in _CHECK_SCRIPT


# --- the docker wrapper ----------------------------------------------------------------


def test_the_script_is_cleaned_up_from_the_delivered_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", _Recorder())

    run_state_continuity_check_in_docker((), tmp_path)

    assert list(tmp_path.glob("___freelancerstudio_state_check*")) == []


def test_the_check_runs_in_the_shared_playwright_image(tmp_path, monkeypatch):
    from order_workflow.docker_qa_runner import PLAYWRIGHT_IMAGE

    recorder = _Recorder()
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", recorder)

    run_state_continuity_check_in_docker((), Path(tmp_path))

    assert recorder.calls[0]["image"] == PLAYWRIGHT_IMAGE


def test_the_runner_matches_the_repair_loop_signature(tmp_path, monkeypatch):
    # run_qa_repair_loop calls qa_runner(qa_commands, cwd) positionally.
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", _Recorder())

    outcome = run_state_continuity_check_in_docker(("label",), Path(tmp_path))

    assert outcome.passed is True
