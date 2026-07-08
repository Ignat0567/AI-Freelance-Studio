import json
from pathlib import Path

import pytest

from project_spec import (
    ACCEPTANCE_EVIDENCE_FIELDS,
    ACCEPTANCE_EVIDENCE_HISTORY_KEY,
    ISSUE_FIELDS,
    Issue,
    append_agent_review_issues,
    build_product_judge_input,
    detect_requirement_gaps,
    ensure_acceptance_evidence_history,
    ensure_project_spec_bundle,
    generate_acceptance_criteria,
    normalize_agent_review_issues,
    orphan_mandatory_requirements,
    record_acceptance_evidence,
    requirement_dependency_errors,
    requirement_graph,
    requirements_from_features,
)


def _project(title: str, description: str) -> dict:
    return {"title": title, "description": description, "chat_history": [], "logs": []}


def test_issue_model_has_required_structured_fields():
    issue = Issue(
        id="ISSUE-001",
        source="final_delivery_audit",
        severity="critical",
        requirement_id="REQ-001",
        criterion_id="AC-001",
        title="Runtime smoke failed",
        evidence={"status": "failed"},
        reproduction=["python -m pytest -q"],
        owner="qa",
        status="open",
        attempts=1,
        verification_method="runtime_smoke",
    )

    data = issue.to_dict()

    assert tuple(data) == ISSUE_FIELDS
    assert data["id"] == "ISSUE-001"
    assert data["evidence"] == {"status": "failed"}
    assert data["reproduction"] == ["python -m pytest -q"]


def test_issue_model_defaults_are_json_serializable_and_independent():
    first = Issue("ISSUE-001", "qa_engine", "high", "REQ-001", "AC-001", "First")
    second = Issue("ISSUE-002", "qa_engine", "medium", "REQ-002", "AC-002", "Second")

    first.evidence["detail"] = "failed import"
    first.reproduction.append("python -m pytest")
    restored = json.loads(json.dumps(first.to_dict()))

    assert restored["owner"] == "unassigned"
    assert restored["status"] == "open"
    assert restored["attempts"] == 0
    assert restored["verification_method"] == ""
    assert second.evidence == {}
    assert second.reproduction == []


def test_bugcatcher_findings_normalize_to_open_issue_with_original_text_evidence():
    report = """VERDICT: FAIL
Critical bug: checkout accepts negative quantities.
Reproduction steps:
1. Add item with quantity -1
2. Submit checkout
"""

    issues = normalize_agent_review_issues("bugcatcher", report, iteration=2)

    assert len(issues) == 1
    issue = issues[0]
    assert tuple(issue) == ISSUE_FIELDS
    assert issue["id"] == "ISSUE-BUGCATCHER-002-001"
    assert issue["source"] == "bugcatcher"
    assert issue["severity"] == "critical"
    assert issue["status"] == "open"
    assert issue["owner"] == "codex"
    assert issue["verification_method"] == "qa_review"
    assert issue["evidence"]["original_text_report"] == report
    assert "Reproduction steps:" in issue["reproduction"]


def test_sentinel_and_lupa_findings_normalize_with_agent_specific_methods():
    sentinel = normalize_agent_review_issues("sentinel", "VERDICT: FAIL\nHigh risk: API key exposure.", iteration=1)[0]
    lupa = normalize_agent_review_issues("lupa", "VERDICT: FAIL\nLow quality issue: duplicated parsing logic.", iteration=1)[0]

    assert sentinel["source"] == "sentinel"
    assert sentinel["severity"] == "high"
    assert sentinel["verification_method"] == "security_review"
    assert lupa["source"] == "lupa"
    assert lupa["severity"] == "low"
    assert lupa["verification_method"] == "code_review"


def test_agent_pass_text_does_not_close_existing_issue():
    project = {
        "issues": [
            Issue("ISSUE-BUGCATCHER-001-001", "bugcatcher", "high", "REQ-001", "AC-001", "Existing", status="open").to_dict()
        ]
    }

    appended = append_agent_review_issues(project, "bugcatcher", "VERDICT: PASS\nAll previously reported issues look fixed.", iteration=2)

    assert appended == []
    assert len(project["issues"]) == 1
    assert project["issues"][0]["status"] == "open"


def test_product_judge_input_contains_only_allowed_serialized_fields():
    project = _project("Judge Demo", "Build a FastAPI todo API with tests.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]
    record_acceptance_evidence(
        project,
        criterion_id,
        "passed",
        {"source": "unit_test", "method": "command", "summary": "README exists", "command": "test -f README.md"},
    )
    project["issues"] = [
        Issue("ISSUE-OPEN", "qa_engine", "high", "REQ-001", criterion_id, "Open issue", evidence={"fingerprint": "abc"}).to_dict(),
        Issue("ISSUE-CLOSED", "qa_engine", "high", "REQ-001", criterion_id, "Closed issue", status="closed").to_dict(),
    ]
    project["bugcatcher_review_v1"] = "VERDICT: FAIL\nBug found."
    project["sentinel_review_v2"] = "VERDICT: PASS\nNo security blockers."
    project["final_delivery_report"] = {
        "runtime_verification": {"status": "passed", "adapter": "fastapi"},
        "known_limitations": ["Credential-free local verification only"],
        "status": "passed",
    }
    qa_result = {
        "success": True,
        "rounds_completed": 2,
        "total_errors": 0,
        "errors": [],
        "policy_groups": ["python", "fastapi"],
        "repair_history": [{"repair_round": 1, "success": True}],
    }

    bundle = build_product_judge_input(project, qa_result)
    restored = json.loads(json.dumps(bundle))

    assert tuple(restored) == (
        "original_request",
        "requirement_graph",
        "acceptance_criteria",
        "evidence",
        "open_issues",
        "qa_summary",
        "runtime_evidence",
        "security_review_summaries",
        "known_limitations",
    )
    assert restored["original_request"] == project["project_spec"]["original_user_request"]
    assert restored["requirement_graph"]
    assert restored["acceptance_criteria"] == project["acceptance_criteria"]
    assert criterion_id in restored["evidence"]
    assert [issue["id"] for issue in restored["open_issues"]] == ["ISSUE-OPEN"]
    assert restored["qa_summary"]["success"] is True
    assert restored["qa_summary"]["policy_groups"] == ["python", "fastapi"]
    assert restored["runtime_evidence"] == {"status": "passed", "adapter": "fastapi"}
    assert restored["security_review_summaries"]["bugcatcher"] == [{"iteration": 1, "summary": "VERDICT: FAIL\nBug found."}]
    assert restored["security_review_summaries"]["sentinel"] == [{"iteration": 2, "summary": "VERDICT: PASS\nNo security blockers."}]
    assert restored["known_limitations"] == ["Credential-free local verification only"]
    assert "final_delivery_report" not in restored
    assert "logs" not in restored


def test_product_judge_input_falls_back_to_spec_limitations_and_project_qa_result():
    project = _project("Generic", "Create a custom local tool.")
    ensure_project_spec_bundle(project)
    project["project_spec"]["risks"] = ["Manual UX judgment may still be needed"]
    project["qa_result"] = {"success": False, "rounds_completed": 1, "total_errors": 1, "errors": ["pytest failed"], "needs_human_input": True}

    bundle = build_product_judge_input(project)

    assert bundle["known_limitations"] == ["Manual UX judgment may still be needed"]
    assert bundle["runtime_evidence"] == {}
    assert bundle["qa_summary"]["success"] is False
    assert bundle["qa_summary"]["needs_human_input"] is True


def test_scenario_1_simple_fastapi_profile_detected():
    project = _project("Todo API", "Build a simple FastAPI REST API with CRUD endpoints and pytest tests.")

    bundle = ensure_project_spec_bundle(project)

    assert "fastapi" in bundle["project_profiles"]
    assert "REST_API" in bundle["project_profiles"]
    assert project["project_spec"]["expected_entrypoint"] == "main.py"


def test_scenario_2_telegram_bot_profile_criteria_added():
    project = _project("Joke Bot", "Create a Telegram bot using aiogram with /start, /help, categories, callbacks, and favorites.")

    bundle = ensure_project_spec_bundle(project)
    titles = "\n".join(c["title"] for c in bundle["acceptance_criteria"])

    assert "telegram_bot" in bundle["project_profiles"]
    assert "Telegram bot configuration is safe" in titles
    assert "Telegram command handlers smoke test" in titles


def test_scenario_3_unknown_project_gets_generic_qa_plan():
    project = _project("Odd Artifact", "Create an unusual local artifact organizer with custom naming rules.")

    bundle = ensure_project_spec_bundle(project)

    assert bundle["project_profiles"]
    assert bundle["qa_plan"]["levels"]
    assert any(level["level"] == "A" for level in bundle["qa_plan"]["levels"])


def test_scenario_4_explicit_user_feature_maps_to_acceptance_criteria():
    feature = "user can choose AI provider and enter API key in the interface"
    project = _project("Provider UI", f"Build a settings page where {feature}. Empty and invalid keys must be handled.")

    bundle = ensure_project_spec_bundle(project)
    criteria_text = "\n".join(c["description"] + " " + c.get("trace", "") for c in bundle["acceptance_criteria"])

    assert "choose AI provider" in criteria_text or "choose ai provider" in criteria_text.lower()
    assert "api key" in criteria_text.lower()


def test_requirement_and_acceptance_ids_are_stable_and_formatted():
    project = _project("Traceable App", "Build a dashboard. Add CSV export. Include admin login.")

    first = ensure_project_spec_bundle(project)
    first_req_ids = [req["id"] for req in first["project_spec"]["requirements"]]
    first_ac_ids = [criterion["id"] for criterion in first["acceptance_criteria"]]
    second = ensure_project_spec_bundle(project)

    assert first_req_ids == [f"REQ-{index:03d}" for index in range(1, len(first_req_ids) + 1)]
    assert first_ac_ids == [f"AC-{index:03d}" for index in range(1, len(first_ac_ids) + 1)]
    assert [req["id"] for req in second["project_spec"]["requirements"]] == first_req_ids
    assert [criterion["id"] for criterion in second["acceptance_criteria"]] == first_ac_ids


def test_every_mandatory_user_requirement_links_to_acceptance_criterion():
    project = _project("Traceable App", "Build a dashboard. Add CSV export. Include admin login.")

    bundle = ensure_project_spec_bundle(project)
    mandatory_req_ids = {
        req["id"]
        for req in bundle["project_spec"]["requirements"]
        if req["mandatory"] and req["priority"] in ("critical", "high") and req["source"] == "user_requirement"
    }
    linked_req_ids = {
        req_id
        for criterion in bundle["acceptance_criteria"]
        for req_id in criterion.get("requirement_ids", [])
    }

    assert mandatory_req_ids
    assert mandatory_req_ids <= linked_req_ids
    assert bundle["traceability"]["orphan_mandatory_requirement_ids"] == []


def test_requirement_graph_fields_are_serializable():
    project = _project("Graph Demo", "Build a dashboard. Add CSV export.")

    bundle = ensure_project_spec_bundle(project)
    requirements = requirement_graph(bundle["project_spec"])
    restored = json.loads(json.dumps(requirements))

    assert requirements
    for requirement in restored:
        assert set(("id", "parent_id", "title", "description", "priority", "source", "dependencies", "status")) <= set(requirement)
        assert requirement["id"].startswith("REQ-")
        assert requirement["parent_id"] is None
        assert isinstance(requirement["dependencies"], list)
        assert requirement["status"] == "pending"


def test_required_features_convert_to_top_level_requirement_nodes_simple():
    original_request = "Build a FastAPI todo API with CRUD endpoints."
    project = _project("Todo API", original_request)

    bundle = ensure_project_spec_bundle(project)
    spec = bundle["project_spec"]
    requirements = requirement_graph(spec)

    assert spec["original_user_request"] == original_request
    assert [req["description"] for req in requirements] == spec["required_features"]
    assert all(req["parent_id"] is None for req in requirements)
    assert all(req["source"] == "user_requirement" for req in requirements)


def test_required_features_convert_to_top_level_requirement_nodes_multi_feature_without_duplicates():
    features = ["dashboard", "CSV export", "dashboard", " admin login "]

    requirements = requirements_from_features(features)

    assert [req["id"] for req in requirements] == ["REQ-001", "REQ-002", "REQ-003"]
    assert [req["description"] for req in requirements] == ["dashboard", "CSV export", "admin login"]
    assert [req["title"] for req in requirements] == ["dashboard", "CSV export", "admin login"]
    assert all(req["parent_id"] is None for req in requirements)
    assert all(req["dependencies"] == [] for req in requirements)


def test_requirement_dependency_validation_accepts_known_dependencies():
    requirements = [
        {"id": "REQ-001", "dependencies": []},
        {"id": "REQ-002", "dependencies": ["REQ-001"]},
    ]

    assert requirement_dependency_errors(requirements) == []


def test_requirement_dependency_validation_reports_unknown_and_self_dependencies():
    requirements = [
        {"id": "REQ-001", "dependencies": ["REQ-001"]},
        {"id": "REQ-002", "dependencies": ["REQ-999"]},
        {"id": "REQ-003", "dependencies": "REQ-001"},
    ]

    errors = requirement_dependency_errors(requirements)

    assert "REQ-001: dependency cannot reference itself" in errors
    assert "REQ-002: unknown dependency REQ-999" in errors
    assert "REQ-003: dependencies must be a list" in errors


def test_orphan_mandatory_user_requirements_are_detected():
    spec = {
        "required_features": ["dashboard", "CSV export"],
        "requirements": [
            {"id": "REQ-001", "description": "dashboard", "priority": "high", "source": "user_requirement", "mandatory": True},
            {"id": "REQ-002", "description": "CSV export", "priority": "high", "source": "user_requirement", "mandatory": True},
        ],
        "project_profiles": [],
    }
    criteria = generate_acceptance_criteria(spec)
    criteria = [criterion for criterion in criteria if "REQ-002" not in criterion.get("requirement_ids", [])]

    orphans = orphan_mandatory_requirements(spec, criteria)

    assert [req["id"] for req in orphans] == ["REQ-002"]


def test_scenario_5_credential_requirement_is_explicit():
    project = _project("OpenAI Tool", "Build an AI application that calls OpenAI using an API key and stores no secrets in code.")

    bundle = ensure_project_spec_bundle(project)
    spec = bundle["project_spec"]
    titles = "\n".join(c["title"] for c in bundle["acceptance_criteria"])

    assert any(c["name"] == "OPENAI_API_KEY" for c in spec["required_credentials"])
    assert "Credentials are documented safely" in titles


def test_requirement_gap_detection_returns_structured_categories():
    text = (
        "Build a modern dashboard that must use React and must not use React. "
        "Integrate Stripe and verify real payment flow with 100% uptime."
    )

    gaps = detect_requirement_gaps(text)
    by_category = {gap["category"]: gap for gap in gaps}

    assert set(("ambiguity", "contradiction", "missing_credential", "impossible_verification_method")) <= set(by_category)
    for gap in gaps:
        assert set(("id", "category", "severity", "summary", "evidence", "suggested_question", "status")) <= set(gap)
        assert gap["id"].startswith("GAP-")
        assert gap["status"] == "unresolved"


def test_requirement_gap_detection_reports_missing_external_service_detail():
    gaps = detect_requirement_gaps("Build an app that integrates Discord notifications for users.", [])

    assert any(gap["category"] == "missing_external_service_detail" for gap in gaps)


def test_project_spec_bundle_includes_requirement_gaps_without_prompting_user():
    project = _project("Vague AI Tool", "Build a fast OpenAI app and prove users will love it.")

    bundle = ensure_project_spec_bundle(project)
    gaps = bundle["project_spec"]["requirement_gaps"]

    assert gaps
    assert project["project_spec"]["requirement_gaps"] == gaps
    assert all("suggested_question" in gap for gap in gaps)


def test_file_based_static_and_vite_detection(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (tmp_path / "vite.config.js").write_text("export default {};", encoding="utf-8")
    (tmp_path / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    project = _project("Frontend", "Build a frontend.")

    bundle = ensure_project_spec_bundle(project, str(tmp_path))

    assert "vite_frontend" in bundle["project_profiles"]
    assert "static_website" in bundle["project_profiles"]


def test_acceptance_evidence_is_structured_and_json_serializable():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    record_acceptance_evidence(
        project,
        criterion_id,
        "passed",
        {
            "source": "unit_test",
            "method": "command",
            "command": "python -m pytest -q",
            "exit_code": 0,
            "summary": "Focused tests passed.",
            "artifacts": ["pytest.log"],
            "legacy_detail": "preserved",
        },
    )

    evidence = project["acceptance_criteria"][0]["evidence"][-1]
    assert all(field in evidence for field in ACCEPTANCE_EVIDENCE_FIELDS)
    assert evidence["criterion_id"] == criterion_id
    assert evidence["verifier"] == "unit_test"
    assert evidence["source"] == "unit_test"
    assert evidence["legacy_detail"] == "preserved"
    assert json.loads(json.dumps(evidence))["status"] == "passed"


def test_passed_acceptance_evidence_requires_evidence():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    with pytest.raises(ValueError, match="requires"):
        record_acceptance_evidence(project, criterion_id, "passed", {})


def test_failed_acceptance_evidence_can_record_empty_legacy_payload():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    record_acceptance_evidence(project, criterion_id, "failed", {})

    evidence = project["acceptance_criteria"][0]["evidence"][-1]
    assert all(field in evidence for field in ACCEPTANCE_EVIDENCE_FIELDS)
    assert evidence["status"] == "failed"


def test_acceptance_evidence_history_is_keyed_by_criterion_and_appends_failures():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    record_acceptance_evidence(project, criterion_id, "failed", {"summary": "First QA round failed."})
    record_acceptance_evidence(project, criterion_id, "failed", {"summary": "Second QA round still failed."})

    history = project[ACCEPTANCE_EVIDENCE_HISTORY_KEY]
    assert list(history)[:1] == [criterion_id]
    assert [entry["summary"] for entry in history[criterion_id]] == [
        "First QA round failed.",
        "Second QA round still failed.",
    ]
    assert project["acceptance_criteria"][0]["evidence"] == history[criterion_id]


def test_acceptance_evidence_history_survives_project_json_persistence_round_trip():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]
    record_acceptance_evidence(project, criterion_id, "failed", {"summary": "Persisted failure evidence."})

    restored = json.loads(json.dumps(project))
    ensure_acceptance_evidence_history(restored)

    history = restored[ACCEPTANCE_EVIDENCE_HISTORY_KEY]
    assert history[criterion_id][0]["summary"] == "Persisted failure evidence."
    assert restored["acceptance_criteria"][0]["evidence"] == history[criterion_id]


def test_legacy_criterion_evidence_migrates_to_project_history_without_overwrite():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]
    legacy_entry = {"criterion_id": criterion_id, "status": "failed", "summary": "Legacy failure."}
    project.pop(ACCEPTANCE_EVIDENCE_HISTORY_KEY)
    project["acceptance_criteria"][0]["evidence"] = [legacy_entry]

    history = ensure_acceptance_evidence_history(project)
    record_acceptance_evidence(project, criterion_id, "failed", {"summary": "New failure."})

    assert history[criterion_id][0] == legacy_entry
    assert history[criterion_id][1]["summary"] == "New failure."
