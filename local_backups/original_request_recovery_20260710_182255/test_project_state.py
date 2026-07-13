import json
from pathlib import Path

import project_state


def _write(path: Path, text: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path, criterion=None):
    criterion = criterion or {"id": "AC-ONE", "title": "The interface has a modern tidy visual design.", "priority": "high", "verification_method": "feature_trace_static_or_smoke", "status": "pending"}
    return {
        "project_id": "project-state-test",
        "title": "State Test",
        "target_path": str(root),
        "status": "final_audit",
        "project_spec": {"project_type": "web_application"},
        "acceptance_criteria": [criterion],
        "issues": [],
        "_qa_passed": True,
    }


def _evidence(criterion_id="AC-ONE", status="passed", screenshot=""):
    collected = {"observation": "direct verifier observation"}
    if screenshot:
        collected["screenshots"] = [screenshot]
    return {"criterion_id": criterion_id, "verifier_type": "browser_usability", "status": status, "verdict": status, "classification": "IMPLEMENTED_BUT_NOT_VERIFIED", "assertions": ["direct observation"], "collected_evidence": collected}


def test_ledger_persists_history_and_latest_valid_evidence(tmp_path):
    _write(tmp_path / "app.py", "print('app')")
    project = _project(tmp_path)
    project_state.persist_project_state(project)
    first = project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence(status="failed"))
    project["acceptance_criteria"][0]["status"] = "passed"
    second = project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence())
    ledger, error = project_state.load_evidence_ledger(str(tmp_path))
    current, reason = project_state.latest_valid_evidence(str(tmp_path), project["acceptance_criteria"][0], ledger)

    assert error == ""
    assert len(ledger["history"]["AC-ONE"]) == 2
    assert second["supersedes"] == first["event_id"]
    assert current["event_id"] == second["event_id"]
    assert reason == ""


def test_snapshot_and_semantic_changes_reject_old_evidence(tmp_path):
    _write(tmp_path / "app.py", "one")
    project = _project(tmp_path)
    project_state.persist_project_state(project)
    project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence())
    _write(tmp_path / "app.py", "two")
    assert project_state.latest_valid_evidence(str(tmp_path), project["acceptance_criteria"][0])[0] is None

    _write(tmp_path / "app.py", "one")
    changed = {**project["acceptance_criteria"][0], "title": "A different semantic requirement"}
    assert project_state.latest_valid_evidence(str(tmp_path), changed)[0] is None


def test_missing_or_changed_artifact_rejects_current_evidence(tmp_path):
    screenshot = tmp_path / "evidence_artifacts" / "snap" / "AC-ONE" / "shot.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"image-a")
    project = _project(tmp_path)
    project_state.persist_project_state(project)
    project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence(screenshot=str(screenshot)))
    assert project_state.latest_valid_evidence(str(tmp_path), project["acceptance_criteria"][0])[0] is not None
    screenshot.write_bytes(b"image-b")
    assert project_state.latest_valid_evidence(str(tmp_path), project["acceptance_criteria"][0])[0] is None
    screenshot.unlink()
    assert project_state.latest_valid_evidence(str(tmp_path), project["acceptance_criteria"][0])[0] is None


def test_report_staleness_requires_snapshot_and_ledger_provenance(tmp_path):
    _write(tmp_path / "README.md", "# Demo")
    _write(tmp_path / "DELIVERY_REPORT.json", json.dumps({"status": "passed"}))

    staleness = project_state.report_staleness(str(tmp_path))

    assert staleness["stale"] is True
    assert "missing_project_snapshot_provenance" in staleness["reason"]


def test_new_ledger_evidence_makes_an_old_report_stale(tmp_path):
    _write(tmp_path / "app.py", "one")
    project = _project(tmp_path)
    project_state.persist_project_state(project)
    old_ledger = project_state.evidence_ledger_fingerprint(str(tmp_path))
    _write(tmp_path / "DELIVERY_REPORT.json", json.dumps({"project_snapshot_fingerprint": project_state.project_snapshot_fingerprint(str(tmp_path)), "evidence_ledger_fingerprint": old_ledger}))
    project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence())

    assert project_state.report_staleness(str(tmp_path))["stale"] is True


def test_replay_survives_fresh_reload_and_is_deterministic(tmp_path):
    _write(tmp_path / "app.py", "print('app')")
    project = _project(tmp_path)
    project["acceptance_criteria"][0]["status"] = "passed"
    project_state.persist_project_state(project)
    project_state.append_evidence_record(project, project["acceptance_criteria"][0], _evidence())

    first = project_state.replay_final_audit(str(tmp_path))
    second = project_state.replay_final_audit(str(tmp_path))
    state, error = project_state.load_project_state(str(tmp_path))

    assert error == ""
    assert state["project_id"] == "project-state-test"
    assert first["status"] == second["status"] == "passed"
    assert first["criteria"] == second["criteria"]


def test_discovery_recovers_per_project_state_without_global_index(tmp_path):
    generated = tmp_path / "generated_projects"
    project_root = generated / "durable"
    project_root.mkdir(parents=True)
    project = _project(project_root)
    project_state.persist_project_state(project)

    discovered = project_state.discover_projects(str(generated))

    assert discovered[0]["classification"] == "fully_recovered"
    assert discovered[0]["project"]["project_id"] == "project-state-test"


def test_random_directory_is_not_fabricated_as_a_project(tmp_path):
    random = tmp_path / "random"
    random.mkdir()
    _write(random / "notes.txt", "nothing to recover")

    result = project_state.recover_legacy_project(str(random))

    assert result["recovery_status"] == "insufficient_metadata"


def test_legacy_recovery_is_conservative_and_persists_uncertainty(tmp_path):
    _write(tmp_path / "README.md", "# Legacy")
    _write(tmp_path / "tests" / "test_app.py", "def test_ok(): assert True")
    _write(tmp_path / "DELIVERY_REPORT.json", json.dumps({"project_name": "Legacy", "acceptance_criteria_summary": [{"id": "AC-1", "title": "A feature", "priority": "high", "status": "passed"}]}))

    result = project_state.recover_legacy_project(str(tmp_path), persist=True)
    replay = project_state.replay_final_audit(str(tmp_path))

    assert result["recovery"]["evidence_records_recovered"] == 0
    assert result["recovery"]["persisted_report_stale"] is True
    assert replay["criteria"][0]["status"] == "not_verified"


def test_atomic_write_keeps_previous_complete_state_when_temp_file_exists(tmp_path):
    path = tmp_path / "state.json"
    project_state.atomic_write_json(str(path), {"revision": 1})
    (tmp_path / ".state.json.interrupted.tmp").write_text('{"revision":', encoding="utf-8")

    value, error = project_state.read_json(str(path))

    assert error == ""
    assert value == {"revision": 1}


def test_product_judge_and_unavailable_results_persist_without_secrets(tmp_path):
    project = _project(tmp_path)
    project_state.persist_project_state(project)
    evidence = _evidence(status="not_verified")
    evidence["collected_evidence"]["INDEPENDENT_PRODUCT_JUDGE_REVIEW"] = {
        "provider": "openai",
        "model": "vision",
        "independence_level": "same_model_separate_role",
        "verdict": "insufficient_evidence",
        "objective_evidence_fingerprint": "objective",
        "reason": "API_KEY=not-for-storage",
    }
    record = project_state.append_evidence_record(project, project["acceptance_criteria"][0], evidence)

    assert record["product_judge"]["verdict"] == "insufficient_evidence"
    assert "not-for-storage" not in json.dumps(record)
