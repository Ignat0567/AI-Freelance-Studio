import hashlib
import json

import delivery_audit
import main
import product_judge


def _shot(path, category, dimensions):
    shot = path / f"AC-VISUAL_{category}_{dimensions}.png"
    shot.write_bytes(b"not-a-real-png-but-a-stable-artifact")
    return str(shot)


def _evidence(tmp_path):
    screenshots = [
        _shot(tmp_path, "laptop", "1366x768"),
        _shot(tmp_path, "full_hd_desktop", "1920x1080"),
        _shot(tmp_path, "high_resolution_desktop", "2560x1440"),
        _shot(tmp_path, "tablet_portrait", "768x1024"),
    ]
    criterion = {"id": "AC-ANY", "title": "The interface has a modern tidy visual design."}
    objective = {"passed": True, "viewport_results": [], "layout_findings": [], "primary_actions": []}
    project = {"title": "Visual demo", "description": "Secret token=super-secret-value must not leak", "status": "final_audit"}
    return criterion, objective, screenshots, project


def _approved(**_kwargs):
    return json.dumps({"verdict": "approved", "findings": []})


def _run(tmp_path, response=_approved, objective=None, snapshot="snapshot-a"):
    criterion, default_objective, screenshots, project = _evidence(tmp_path)
    return product_judge.run_product_judge(
        criterion,
        objective or default_objective,
        screenshots,
        project,
        {"product_kind": "web_application", "ui_runtime": "browser"},
        snapshot,
        {"enabled": True, "provider": "openai", "model": "vision-model", "temperature": 0.3},
        {"provider": "anthropic", "model": "independent-model"},
        response,
    ), project, criterion, default_objective, screenshots


def test_judge_is_read_only_and_cannot_complete_project(tmp_path):
    source = tmp_path / "app.css"
    source.write_text("body { color: black; }", encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result, project, *_ = _run(tmp_path)

    assert result["verdict"] == "approved"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert project["status"] == "final_audit"


def test_objective_failure_cannot_be_overridden_by_approval(tmp_path):
    result, *_ = _run(tmp_path, objective={"passed": False})

    assert result["verdict"] == "insufficient_evidence"
    assert product_judge.combined_subjective_verdict(False, "approved") == "failed"


def test_approved_and_nonblocking_notes_pass_only_with_objective_evidence(tmp_path):
    approved, *_ = _run(tmp_path)
    notes, *_ = _run(tmp_path, lambda **_kwargs: json.dumps({"verdict": "approved_with_nonblocking_notes", "findings": [{"id": "F-1", "category": "consistency", "severity": "minor", "viewport": "all", "observation": "Minor spacing refinement.", "evidence_reference": "screenshot", "blocking": False, "recommended_action": "Consider aligning labels."}]}))

    assert product_judge.combined_subjective_verdict(True, approved["verdict"]) == "passed"
    assert product_judge.combined_subjective_verdict(True, notes["verdict"]) == "passed"


def test_blocking_objection_fails_and_creates_unified_issue(tmp_path):
    response = lambda **_kwargs: json.dumps({"verdict": "blocking_objection", "findings": [{"id": "F-1", "category": "task_clarity", "severity": "major", "viewport": "tablet_portrait", "observation": "The main task cannot be understood.", "evidence_reference": "tablet screenshot", "blocking": True, "recommended_action": "Clarify the primary action."}]})
    result, project, criterion, objective, _ = _run(tmp_path, response)
    delivery_audit._record_product_judge_issue(project, criterion, objective, result)

    assert product_judge.combined_subjective_verdict(True, result["verdict"]) == "failed"
    assert project["issues"][0]["source"] == "product_judge"
    assert project["issues"][0]["owner"] == "opencode"


def test_unavailable_malformed_or_missing_screenshots_do_not_pass(tmp_path):
    malformed, *_ = _run(tmp_path, lambda **_kwargs: "this looks good")
    criterion, objective, _screenshots, project = _evidence(tmp_path)
    missing = product_judge.run_product_judge(criterion, objective, [], project, {}, "snapshot-a", {"enabled": True, "provider": "openai", "model": "vision-model"}, invoke=_approved)

    assert malformed["verdict"] == "insufficient_evidence"
    assert missing["verdict"] == "insufficient_evidence"
    assert product_judge.combined_subjective_verdict(True, malformed["verdict"]) == "not_verified"


def test_image_input_capability_is_required(tmp_path, monkeypatch):
    monkeypatch.setattr(product_judge, "provider_capabilities", lambda _provider: {"image_input": False})

    result, *_ = _run(tmp_path)

    assert result["verdict"] == "insufficient_evidence"
    assert result["availability"] == "unavailable"


def test_judge_result_is_bound_to_exact_artifacts_criterion_and_snapshot(tmp_path):
    result, _project, criterion, objective, screenshots = _run(tmp_path)
    fingerprint = product_judge.evidence_fingerprint(objective)

    assert product_judge.judge_result_is_fresh(result, criterion["id"], "snapshot-a", fingerprint)
    assert not product_judge.judge_result_is_fresh(result, "AC-OTHER", "snapshot-a", fingerprint)
    assert not product_judge.judge_result_is_fresh(result, criterion["id"], "snapshot-after-code-change", fingerprint)
    with open(screenshots[0], "ab") as artifact:
        artifact.write(b"changed")
    assert not product_judge.judge_result_is_fresh(result, criterion["id"], "snapshot-a", fingerprint)


def test_independence_levels_are_honest_and_dispatch_is_semantic(tmp_path):
    assert product_judge.independence_level("openai", "gpt-5.5", "openai", "gpt-5.5") == "same_model_separate_role"
    assert product_judge.independence_level("openai", "gpt-5.5", "anthropic", "claude") == "different_provider"
    assert product_judge.independence_level("openai", "gpt-5.5", "openai", "gpt-5") == "same_provider_different_model"
    assert product_judge.is_subjective_product_quality({"id": "AC-013", "title": "Modern tidy design"})
    assert product_judge.is_subjective_product_quality({"id": "AC-999", "title": "Modern tidy design"})
    assert not product_judge.is_subjective_product_quality({"id": "AC-013", "title": "Primary buttons perform their actions"})


def test_input_excludes_secrets_and_old_approval_is_not_reused_after_repair(tmp_path):
    result, _project, criterion, objective, _ = _run(tmp_path)
    assert "super-secret-value" not in json.dumps(result["input_summary"])
    assert not product_judge.judge_result_is_fresh(result, criterion["id"], "snapshot-after-repair", product_judge.evidence_fingerprint(objective))


def test_prompt_is_runtime_evidence_not_implementation_history(tmp_path):
    criterion, objective, screenshots, project = _evidence(tmp_path)
    bundle, reason = product_judge.build_judge_input(criterion, objective, screenshots, project, {}, "snapshot-a")

    assert not reason
    prompt = product_judge._prompt(bundle)
    assert "implementation history" not in prompt.lower()
    assert "PRODUCT JUDGE INPUT" in prompt


def test_bridge_failure_and_malformed_response_are_distinct(tmp_path, monkeypatch):
    criterion, objective, screenshots, project = _evidence(tmp_path)
    connection = type("Connection", (), {"execute": lambda self, _request: {"status": "error", "failure_stage": "cli_process_exit", "error_category": "cli_argument_parsing", "exit_code": 2, "stderr_summary": "unknown option", "attachment_count": 4}})()
    monkeypatch.setattr(product_judge, "OpenCodeBridgeConnection", type("FakeConnection", (), {"from_dict": staticmethod(lambda _value: connection)}))
    monkeypatch.setattr(product_judge, "bridge_effective_capabilities", lambda _connection: {"image_input": True})

    failed = product_judge.run_product_judge(criterion, objective, screenshots, project, {}, "snapshot-a", {"enabled": True, "provider": "opencode_bridge", "model": "openai/gpt-5.5", "connection": {}})
    malformed, *_ = _run(tmp_path, lambda **_kwargs: "not json")

    assert failed["request_diagnostics"]["failure_stage"] == "cli_process_exit"
    assert malformed["request_diagnostics"]["failure_stage"] == "response_parsing"


def test_blocking_judge_issue_uses_snapshot_repair_then_full_qa(tmp_path, monkeypatch):
    calls = []

    class FakeEngine:
        def __init__(self, **_kwargs):
            self.logs = ["snapshot captured"]

        def _request_opencode_fix(self, *_args):
            calls.append("repair")
            return {main.qa_engine_module.OPENCODE_FIX_APPLIED: True, "changed_files": 1, "meaningful_changes_detected": True}

    project = {
        "status": "final_audit",
        "title": "Demo",
        "logs": [],
        "issues": [{"id": "ISSUE-JUDGE-1", "source": "product_judge", "status": "open", "criterion_id": "AC-VISUAL"}],
    }
    monkeypatch.setattr(main, "QAEngine", FakeEngine)
    monkeypatch.setattr(main, "_run_qa_only", lambda project_id, target: calls.append(("full_qa", project_id, target)))

    assert main._repair_product_judge_objections(project, str(tmp_path), "demo-id")
    assert calls == ["repair", ("full_qa", "demo-id", str(tmp_path))]
    assert project["issues"][0]["status"] == "verification_pending"
