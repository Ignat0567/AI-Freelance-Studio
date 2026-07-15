from pathlib import Path

import delivery_audit
import project_state
from feature_matrix import (
    apply_implementation_claim,
    build_feature_matrix,
    ensure_feature_matrix,
    feature_matrix_report,
    load_feature_matrix,
    matrix_blocks_completion,
    semantic_fingerprint,
    update_matrix_from_evidence,
)
from project_spec import ensure_project_spec_bundle, record_acceptance_evidence


def _write(path: Path, text: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, profile="strict_mvp"):
    project = {
        "project_id": "feature-matrix-test",
        "title": "Feature Matrix Test",
        "description": "Build a web app where authenticated customers can save favorite items, remove them, and see them after restart. Managers have a dashboard.",
        "quality_profile": profile,
        "project_mode": profile,
        "target_path": str(tmp_path),
        "logs": [],
        "chat_history": [],
    }
    ensure_project_spec_bundle(project, str(tmp_path))
    return project


def _feature(project):
    return project["feature_matrix"]["features"][0]


def test_implementation_claim_does_not_equal_passed_evidence(tmp_path):
    project = _project(tmp_path)
    feature = _feature(project)

    matrix = apply_implementation_claim(project, feature["description"], "Favorites implemented", "codex", str(tmp_path))
    updated = matrix["features"][0]

    assert updated["implementation_status"] == "claimed"
    assert updated["evidence_status"] != "passed"
    assert any(status == "implemented_unverified" for status in updated["dimensions"].values())


def test_missing_ui_keeps_feature_incomplete(tmp_path):
    project = _project(tmp_path)
    feature = _feature(project)
    feature["dimensions"]["backend"] = "passed"
    feature["dimensions"]["automated_tests"] = "passed"
    feature["dimensions"]["e2e"] = "passed"
    from feature_matrix import recalculate_matrix
    recalculate_matrix(project["feature_matrix"])

    assert feature["dimensions"].get("customer_ui") in {"missing", "not_verified", "implemented_unverified"}
    assert feature["evidence_status"] != "passed"


def test_missing_tests_keep_required_feature_incomplete(tmp_path):
    project = _project(tmp_path)
    feature = _feature(project)
    for key, value in list(feature["dimensions"].items()):
        if value != "not_required" and key != "automated_tests":
            feature["dimensions"][key] = "passed"
    from feature_matrix import recalculate_matrix
    recalculate_matrix(project["feature_matrix"])

    assert feature["dimensions"]["automated_tests"] in {"missing", "not_verified", "implemented_unverified"}
    assert feature["evidence_status"] != "passed"


def test_optional_dimension_does_not_block(tmp_path):
    project = _project(tmp_path)
    feature = _feature(project)
    feature["dimensions"]["admin_ui"] = "not_required"
    for key, value in list(feature["dimensions"].items()):
        if value != "not_required":
            feature["dimensions"][key] = "passed"
    from feature_matrix import recalculate_matrix
    recalculate_matrix(project["feature_matrix"])

    assert feature["evidence_status"] == "passed"


def test_required_platform_dimension_blocks(tmp_path):
    project = _project(tmp_path)
    feature = _feature(project)
    feature["dimensions"]["native_runtime"] = "missing"
    from feature_matrix import recalculate_matrix
    recalculate_matrix(project["feature_matrix"])

    blocks, blockers = matrix_blocks_completion(project["feature_matrix"], "strict_mvp")

    assert blocks is True
    assert blockers


def test_feature_semantics_change_invalidates_old_mapping(tmp_path):
    project = _project(tmp_path)
    old = _feature(project)["semantic_fingerprint"]
    project["project_spec"]["requirements"][0]["description"] = "Customers can export invoices as PDF files."
    changed = build_feature_matrix(project)

    assert changed["features"][0]["semantic_fingerprint"] != old


def test_matrix_survives_restart(tmp_path):
    _write(tmp_path / "README.md", "# Demo")
    project = _project(tmp_path)
    project_state.persist_project_state(project, str(tmp_path))

    matrix, error = load_feature_matrix(str(tmp_path))

    assert error == ""
    assert matrix["features"]
    assert matrix["summary"]["total_features"] >= 1


def test_final_audit_reads_matrix(tmp_path, monkeypatch):
    _write(tmp_path / "main.py", "print('ok')")
    _write(tmp_path / "README.md", "# Demo\n\n## Install\nx\n\n## Run\nx\n\n## Test\nx")
    project = _project(tmp_path)
    monkeypatch.setattr(delivery_audit, "_runtime_smoke", lambda *_args: {"status": "passed"})
    monkeypatch.setattr(delivery_audit, "_check_secrets", lambda *_args: (True, []))
    monkeypatch.setattr(delivery_audit, "_check_todos", lambda *_args: (True, []))

    report = delivery_audit.run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert any(check["name"] == "feature_completeness_matrix" for check in report["checks"])
    assert "feature_matrix_report" in report


def test_ac_id_alone_cannot_satisfy_changed_feature(tmp_path):
    project = _project(tmp_path)
    criterion = project["acceptance_criteria"][0]
    old_fp = semantic_fingerprint("old behavior")
    assert old_fp != semantic_fingerprint(criterion.get("title"))


def test_feature_report_is_generated(tmp_path):
    project = _project(tmp_path)
    report = feature_matrix_report(project["feature_matrix"])

    assert "Feature" in report["columns"]
    assert report["rows"]
    assert "feature_id" in report["rows"][0]


def test_strict_mvp_blocks_on_incomplete_mandatory_feature(tmp_path):
    project = _project(tmp_path, "strict_mvp")
    blocks, blockers = matrix_blocks_completion(project["feature_matrix"], "strict_mvp")

    assert blocks is True
    assert blockers


def test_prototype_policy_remains_different(tmp_path):
    project = _project(tmp_path, "prototype")
    blocks, blockers = matrix_blocks_completion(project["feature_matrix"], "prototype")

    assert blocks is False
    assert blockers == []
