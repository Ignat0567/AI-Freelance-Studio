from pathlib import Path

import opencode_bridge
from opencode_bridge import OpencodeBridge, _resolve_cli_task_file
from qa_engine import QAEngine


def test_project_cwd_relative_task_file_resolves(tmp_path):
    cwd, resolved, error = _resolve_cli_task_file(str(tmp_path), ".opencode_task.md")
    assert cwd == tmp_path.resolve()
    assert resolved == tmp_path.resolve() / ".opencode_task.md"
    assert error == ""


def test_repo_cwd_project_relative_and_absolute_paths_resolve(tmp_path):
    project = tmp_path / "generated_projects" / "ticket app"
    project.mkdir(parents=True)
    _cwd, relative, error = _resolve_cli_task_file(str(tmp_path), "generated_projects\\ticket app\\.opencode_task.md")
    _cwd, absolute, absolute_error = _resolve_cli_task_file(str(tmp_path), str(project / ".opencode_task.md"))
    assert relative == project / ".opencode_task.md"
    assert absolute == project / ".opencode_task.md"
    assert error == absolute_error == ""


def test_duplicate_project_relative_path_is_rejected(tmp_path):
    project = tmp_path / "generated_projects" / "test-project-recovery"
    project.mkdir(parents=True)
    _cwd, _resolved, error = _resolve_cli_task_file(str(project), "generated_projects/test-project-recovery/.opencode_task.md")
    assert error == "duplicated_project_relative_path"


def test_missing_preflight_does_not_start_subprocess(tmp_path, monkeypatch):
    bridge = OpencodeBridge()
    bridge._binary = "opencode-test"
    started = []
    monkeypatch.setattr(opencode_bridge, "_preferred_provider_model", lambda: ("openai", "test"))
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: started.append(True))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    result = bridge._run_cli_task(str(tmp_path), "system", "task")
    assert result["preflight_status"] == "task_file_not_found"
    assert result["subprocess_started"] is False
    assert result["task_file_argument"] == ".opencode_task.md"
    assert result["task_file_resolved_path"] == str(tmp_path.resolve() / ".opencode_task.md")
    assert result["task_file_exists"] is False
    assert started == []


def test_repair_result_preserves_task_path_metadata(tmp_path, monkeypatch):
    class Bridge:
        _binary = "opencode-test"
        def ensure_running(self, _workdir=None, **_kwargs): return True
        def execute_fix_task(self, **_kwargs):
            return {"success": False, "subprocess_started": False, "preflight_status": "task_file_not_found", "task_file_argument": ".opencode_task.md", "task_file_resolved_path": str(tmp_path / ".opencode_task.md"), "task_file_exists": False}
    monkeypatch.setattr("opencode_bridge.get_bridge", lambda: Bridge())
    result = QAEngine({"title": "Test", "logs": []}, str(tmp_path), "p1", "test", "test", 0)._request_opencode_fix([{"id": "ISSUE-1", "status": "open"}], "error", {})
    assert result["status"] == "task_file_not_found"
    assert result["task_file_argument"] == ".opencode_task.md"
    assert result["task_file_exists"] is False
    assert result["snapshot_before"] == result["snapshot_after"]
