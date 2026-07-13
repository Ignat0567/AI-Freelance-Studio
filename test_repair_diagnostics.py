import json
from pathlib import Path

import main
import qa_engine
import project_state
from qa_engine import QAEngine


class FakeBridge:
    def __init__(self, result=None, available=True, on_execute=None):
        self._binary = "opencode-test"
        self.result = result or {"success": True, "exit_code": 0, "summary": ""}
        self.available = available
        self.on_execute = on_execute

    def ensure_running(self, workdir):
        return self.available

    def execute_fix_task(self, **_kwargs):
        if self.on_execute:
            self.on_execute()
        return self.result


def _engine(tmp_path):
    return QAEngine({"title": "Repair test", "logs": []}, str(tmp_path), "repair-test", "test", "test", 0)


def _run(tmp_path, monkeypatch, bridge=None, errors=None, report=None):
    monkeypatch.setattr("opencode_bridge.get_bridge", lambda: bridge or FakeBridge())
    return _engine(tmp_path)._request_opencode_fix(errors if errors is not None else [{"id": "ISSUE-AUDIT-CREDENTIAL-SAFETY", "source": "final_delivery_audit"}], "safe error", report or {})


def test_no_repairable_issue_is_structured(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, errors=[])
    assert result["status"] == "issue_not_selected"
    assert result["issue_ids"] == []


def test_prompt_failure_is_structured(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, report={"bad": {1, 2}})
    assert result["status"] == "prompt_build_failed"
    assert result["prompt_built"] is False


def test_missing_executable_and_start_failure_are_distinct(tmp_path, monkeypatch):
    missing = FakeBridge(available=False)
    missing._binary = ""
    monkeypatch.setattr(qa_engine.shutil, "which", lambda _name: None)
    assert _run(tmp_path, monkeypatch, missing)["status"] == "executable_not_found"
    assert _run(tmp_path, monkeypatch, FakeBridge(available=False))["status"] == "subprocess_start_failed"


def test_timeout_and_nonzero_no_change_are_classified(tmp_path, monkeypatch):
    timeout = _run(tmp_path, monkeypatch, FakeBridge({"success": False, "timed_out": True, "summary": "working", "exit_code": None}))
    failed = _run(tmp_path, monkeypatch, FakeBridge({"success": False, "exit_code": 2, "summary": "bad"}))
    assert timeout["status"] == "subprocess_timeout"
    assert failed["status"] == "subprocess_nonzero_exit"


def test_zero_exit_output_and_no_output_without_changes_are_classified(tmp_path, monkeypatch):
    silent = _run(tmp_path, monkeypatch, FakeBridge({"success": True, "exit_code": 0, "summary": ""}))
    output = _run(tmp_path, monkeypatch, FakeBridge({"success": True, "exit_code": 0, "summary": "fixed"}))
    assert silent["status"] == "subprocess_zero_exit_no_changes"
    assert output["textual_result_present"] is True
    assert output["meaningful_changes_detected"] is False


def test_source_changes_override_textual_outcome(tmp_path, monkeypatch):
    good = _run(tmp_path, monkeypatch, FakeBridge({"success": True, "exit_code": 0, "summary": ""}, on_execute=lambda: (tmp_path / "app.py").write_text("good", encoding="utf-8")))
    bad = _run(tmp_path, monkeypatch, FakeBridge({"success": False, "exit_code": 1, "summary": "error"}, on_execute=lambda: (tmp_path / "app.py").write_text("bad", encoding="utf-8")))
    assert good["status"] == "subprocess_zero_exit_changes_detected"
    assert good["meaningful_changes_detected"] is True
    assert bad["status"] == "subprocess_nonzero_exit"
    assert bad["meaningful_changes_detected"] is True


def test_exception_and_sensitive_output_are_safe_and_durable(tmp_path, monkeypatch):
    class ExplodingBridge(FakeBridge):
        def execute_fix_task(self, **_kwargs):
            raise RuntimeError("token=super-secret-value")
    result = _run(tmp_path, monkeypatch, ExplodingBridge())
    state, error = project_state.load_project_state(str(tmp_path))
    assert result["status"] == "exception"
    assert "super-secret-value" not in json.dumps(result)
    assert error == ""
    assert state["repair_attempts"][-1]["attempt_id"] == result["attempt_id"]


def test_audit_caller_uses_meaningful_changes_and_persists_result(tmp_path, monkeypatch):
    calls = []
    attempt = {"attempt_id": "repair-false", "success": False, "status": "subprocess_zero_exit_no_changes", "meaningful_changes_detected": False}
    class FakeEngine:
        def __init__(self, **_kwargs): self.logs = []
        def _request_opencode_fix(self, *_args): return dict(attempt)
    project = {"title": "Demo", "status": "final_audit", "logs": [], "issues": [{"id": "ISSUE-1", "source": "final_delivery_audit", "status": "open"}]}
    monkeypatch.setattr(main, "QAEngine", FakeEngine)
    monkeypatch.setattr(main, "_run_qa_only", lambda *_args: calls.append("qa"))
    assert not main._repair_final_audit_issues(project, str(tmp_path), "repair-test")
    state, error = project_state.load_project_state(str(tmp_path))
    assert calls == []
    assert error == ""
    assert state["repair_attempts"][-1]["status"] == "subprocess_zero_exit_no_changes"
