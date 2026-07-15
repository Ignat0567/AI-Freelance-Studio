from pathlib import Path

import delivery_audit
import main
import project_state
from architecture_policy import analyze_architecture
from quality_profiles import ensure_quality_settings, evaluate_quality_completion


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(profile: str = "strict_mvp") -> dict:
    project = {
        "project_id": "arch-test",
        "title": "Architecture Test",
        "description": "Backend API with manager dashboard, auth, workflow and persistence.",
        "quality_profile": profile,
        "project_mode": profile,
        "project_profiles": ["fastapi"],
        "project_spec": {"quality_profile": profile, "project_profiles": ["fastapi"], "requested_target_platforms": []},
        "acceptance_criteria": [
            {"id": "AC-PERSIST", "title": "Data persists after restart", "priority": "high", "status": "passed"},
            {"id": "AC-WORKFLOW", "title": "Real E2E workflow passes", "priority": "high", "status": "passed"},
            {"id": "AC-RBAC", "title": "Authentication and RBAC roles pass", "priority": "high", "status": "passed"},
        ],
        "issues": [],
        "logs": [],
    }
    ensure_quality_settings(project)
    return project


def _audit_ready_project(tmp_path: Path, profile: str = "strict_mvp") -> dict:
    _write(tmp_path / "README.md", "# Demo\nRun with python -m uvicorn main:app\n")
    project = _project(profile)
    project["target_path"] = str(tmp_path)
    return project


def _patch_fast_audit(monkeypatch):
    monkeypatch.setattr(delivery_audit, "_runtime_smoke", lambda *_args: {"status": "passed", "status_code": 200})
    monkeypatch.setattr(delivery_audit, "_evaluate_acceptance", lambda *_args: (True, []))
    monkeypatch.setattr(delivery_audit, "ensure_feature_matrix", lambda *_args: {"summary": {}})
    monkeypatch.setattr(delivery_audit, "matrix_blocks_completion", lambda *_args: (False, []))


def test_line_count_alone_is_not_blocking(tmp_path):
    _write(tmp_path / "main.py", "\n".join(["# long but single concern"] * 1300))
    project = _project("strict_mvp")

    report = analyze_architecture(project, str(tmp_path))

    assert report["large_files"]
    assert report["status"] == "passed"
    assert not report["blocking_findings"]


def test_mixed_critical_responsibilities_can_block(tmp_path):
    mixed = """
from fastapi import FastAPI
from pydantic import BaseModel
import sqlite3, os
app = FastAPI()
class Ticket(BaseModel):
    client_name: str
@app.post('/login')
def login(password: str): return {'role': 'admin'}
@app.post('/tickets')
def create_ticket(ticket: Ticket):
    total = 0
    status = 'new'
    db = sqlite3.connect('app.db')
    db.execute('insert into tickets values (?, ?)', (ticket.client_name, status))
    return {'status': status, 'total': total}
"""
    _write(tmp_path / "main.py", mixed + "\n".join(["# business workflow"] * 1250))

    report = analyze_architecture(_project("strict_mvp"), str(tmp_path))

    assert report["status"] == "failed"
    assert any(item["code"] == "mixed_critical_responsibilities" for item in report["blocking_findings"])


def test_prototype_policy_is_more_permissive(tmp_path):
    mixed = "from fastapi import FastAPI\nfrom pydantic import BaseModel\nimport sqlite3\napp=FastAPI()\nclass M(BaseModel): pass\n@app.get('/')\ndef route():\n db=sqlite3.connect('x.db'); total=1; return {'total': total}\n"
    _write(tmp_path / "main.py", mixed + "\n".join(["# long"] * 1000))

    strict = analyze_architecture(_project("strict_mvp"), str(tmp_path))
    prototype = analyze_architecture(_project("prototype"), str(tmp_path), "prototype")

    assert strict["blocking_findings"]
    assert prototype["status"] == "passed"


def test_production_candidate_requires_migration_strategy(tmp_path):
    _write(tmp_path / "app.py", "import sqlite3\nconn = sqlite3.connect('app.db')\nconn.execute('create table tickets(id int)')\n")

    report = analyze_architecture(_project("production_candidate"), str(tmp_path), "production_candidate")

    assert any(item["code"] == "missing_migration_strategy" for item in report["blocking_findings"])


def test_static_mandatory_ui_is_detected(tmp_path):
    _write(tmp_path / "index.html", "<html><body><h1>Manager dashboard coming soon placeholder</h1></body></html>")

    report = analyze_architecture(_project("strict_mvp"), str(tmp_path))

    assert report["placeholder_status"] == "failed"
    assert any(item["code"] == "placeholder_main_ui" for item in report["blocking_findings"])


def test_hardcoded_secrets_block(tmp_path):
    _write(tmp_path / "settings.py", "API_KEY = 'sk-1234567890abcdefghijklmnop'\n")

    report = analyze_architecture(_project("strict_mvp"), str(tmp_path))

    assert any(item["code"] == "hardcoded_secret" for item in report["blocking_findings"])


def test_architecture_issue_creates_unified_issue(tmp_path, monkeypatch):
    project = _audit_ready_project(tmp_path)
    _write(tmp_path / "index.html", "<html><body>Manager dashboard coming soon placeholder</body></html>")
    _patch_fast_audit(monkeypatch)

    report = delivery_audit.run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["architecture_report"]["blocking_findings"]
    issue = next(item for item in project["issues"] if item["title"] == "Final Audit: architecture_review")
    assert issue["source"] == "final_delivery_audit"
    assert issue["severity"] == "critical"


def test_repaired_architecture_reruns_qa(tmp_path, monkeypatch):
    project = _audit_ready_project(tmp_path)
    project["issues"] = [{"id": "ISSUE-AUDIT-architecture_review-1", "source": "final_delivery_audit", "severity": "critical", "status": "open", "title": "Final Audit: architecture_review", "evidence": {}}]
    calls = {"qa": 0}

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            self.logs = []

        def _request_opencode_fix(self, *_args):
            return {"attempt_id": "repair-1", "meaningful_changes_detected": True, "status": "completed", "success": True}

    monkeypatch.setattr(main, "QAEngine", FakeEngine)
    monkeypatch.setattr(main, "_run_qa_only", lambda *_args: calls.__setitem__("qa", calls["qa"] + 1))
    monkeypatch.setattr(main, "get_agent_provider_model", lambda *_args: ("test", "test"))

    assert main._repair_final_audit_issues(project, str(tmp_path), "arch-test") is True
    assert calls["qa"] == 1
    assert project["issues"][0]["status"] == "verification_pending"


def test_architecture_report_survives_restart(tmp_path, monkeypatch):
    project = _audit_ready_project(tmp_path)
    _write(tmp_path / "main.py", "from fastapi import FastAPI\napp=FastAPI()\n")
    _patch_fast_audit(monkeypatch)

    delivery_audit.run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    state, error = project_state.load_project_state(str(tmp_path))

    assert error == ""
    assert state["architecture_review"]["module_map"] is not None


def test_feature_completion_reflects_blocking_architecture_issue():
    project = _project("strict_mvp")
    checks = [
        {"name": "required_files", "status": "passed", "evidence": {}},
        {"name": "readme_instructions", "status": "passed", "evidence": {}},
        {"name": "runtime_smoke", "status": "passed", "evidence": {}},
        {"name": "latest_full_qa", "status": "passed", "evidence": {}},
        {"name": "acceptance_criteria", "status": "passed", "evidence": {}},
        {"name": "secret_scan", "status": "passed", "evidence": {}},
        {"name": "open_blocking_issues", "status": "passed", "evidence": {}},
        {"name": "target:backend", "status": "passed", "evidence": {}},
        {"name": "target:api_service", "status": "passed", "evidence": {}},
        {"name": "target:manager_web", "status": "passed", "evidence": {}},
        {"name": "feature_completeness_matrix", "status": "passed", "evidence": {}},
        {"name": "architecture_review", "status": "failed", "evidence": {"blocking_findings": [{"severity": "critical"}]}},
    ]

    result = evaluate_quality_completion(project, checks)

    assert result["accepted"] is False
    assert "architecture_review_not_passed" in result["blockers"]
