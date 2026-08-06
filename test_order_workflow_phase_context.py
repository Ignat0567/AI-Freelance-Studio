from __future__ import annotations

import pytest

from order_workflow.phase_context import build_phase_context
from order_workflow.qa_runner import QACommandResult, QAOutcome
from order_workflow.workspace import ProjectWorkspace

pytestmark = pytest.mark.unit


def _workspace(tmp_path) -> ProjectWorkspace:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "package.json").write_text("{}", encoding="utf-8")
    (project_path / "src").mkdir()
    (project_path / "src" / "App.jsx").write_text("export default function App() {}", encoding="utf-8")
    return ProjectWorkspace(root=tmp_path, project_path=project_path)


def _outcome(passed: bool) -> QAOutcome:
    return QAOutcome(passed=passed, results=(QACommandResult(command="npm test", exit_code=0 if passed else 1, stdout_tail="", stderr_tail="", duration=0.1),))


def test_build_phase_context_lists_real_files_and_qa_status(tmp_path):
    context = build_phase_context("ui_shell", _workspace(tmp_path), _outcome(True))

    assert context.phase == "ui_shell"
    assert "package.json" in context.files
    assert context.qa_status == "QA passed."
    assert "ui_shell" in context.summary


def test_build_phase_context_reports_failed_qa(tmp_path):
    context = build_phase_context("core_feature", _workspace(tmp_path), _outcome(False))

    assert context.qa_status == "QA failed."
    assert "QA failed." in context.summary


def test_build_phase_context_handles_no_qa_outcome(tmp_path):
    context = build_phase_context("backend_decision", _workspace(tmp_path), None)

    assert context.qa_status == "QA was not run for this phase."
