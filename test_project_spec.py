import json
from pathlib import Path

import pytest

from project_spec import (
    ACCEPTANCE_CONTRACT_FIELDS,
    ACCEPTANCE_EVIDENCE_FIELDS,
    ACCEPTANCE_EVIDENCE_HISTORY_KEY,
    ACCEPTANCE_VERDICTS,
    ISSUE_FIELDS,
    VERIFIER_PLAN_FIELDS,
    VERIFIER_PLAN_TYPES,
    Issue,
    acceptance_evidence_is_direct,
    append_agent_review_issues,
    build_product_judge_input,
    detect_project_profiles,
    detect_requirement_gaps,
    ensure_acceptance_evidence_history,
    ensure_project_spec_bundle,
    generate_acceptance_criteria,
    normalize_agent_review_issues,
    orphan_mandatory_requirements,
    plan_acceptance_verifier,
    record_acceptance_evidence,
    product_runtime_decision,
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


def test_russian_requirement_survives_spec_json_round_trip():
    request = "Иметь возможность создать новую заявку клиента"
    project = _project("Unicode Demo", request)

    bundle = ensure_project_spec_bundle(project)
    restored = json.loads(json.dumps(bundle, ensure_ascii=False))

    assert restored["project_spec"]["original_user_request"] == request
    assert request in restored["project_spec"]["required_features"]
    assert any(criterion["trace"] == request for criterion in restored["acceptance_criteria"])


def test_already_mojibake_text_is_not_double_decoded():
    text = "РёРјРµС‚СЊ"
    project = _project("No Double Decode", text)

    bundle = ensure_project_spec_bundle(project)
    restored = json.loads(json.dumps(bundle, ensure_ascii=False))

    assert restored["project_spec"]["original_user_request"] == text


def test_russian_requirements_generate_self_contained_acceptance_criteria():
    project = _project(
        "Ticket Desk",
        """Сотрудник должен иметь возможность создать новую заявку клиента.

В заявке нужно сохранить:
- имя клиента;
- телефон или email;
- краткое описание проблемы;
- приоритет;
- статус заявки.

После создания заявка должна появляться в общем списке.

Нужно иметь возможность:
- открыть заявку;
- изменить данные;
- поменять статус;
- добавить комментарий;
- удалить заявку, но желательно с подтверждением.

Программа должна нормально работать на обычном компьютере и на планшете.
Данные не должны исчезать после перезапуска программы.
Очень важно:
- программа должна реально запускаться;
- должна быть инструкция, как установить и запустить проект на Windows.
""",
    )

    bundle = ensure_project_spec_bundle(project)
    user_criteria = [criterion for criterion in bundle["acceptance_criteria"] if criterion["source"] == "user_requirement"]
    titles = [criterion["title"] for criterion in user_criteria]

    assert len(user_criteria) >= 7
    assert "User can create a new client request." in titles
    assert "Client request records store all required fields." in titles
    assert "After creation, the new request appears in the common request list." in titles
    assert "User can open an existing request and update its data." in titles
    assert "The application layout remains usable on desktop and tablet viewport sizes." in titles
    assert "Application data persists after restart." in titles
    assert "The application starts successfully with the documented run command." in titles
    assert "Windows installation and run instructions are documented." in titles
    forbidden = {"feature: сохранить", "feature: иметь возможность", "save", "appear", "have the ability"}
    assert not forbidden.intersection({title.lower().strip(" .") for title in titles})


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


def test_creation_list_criterion_plans_http_sequence_without_execution():
    criterion = {
        "id": "AC-CREATE-LIST",
        "title": "After creation, the new request appears in the common request list.",
        "description": "A newly created client request is visible in the shared request list.",
        "expected_result": "The request list contains the new request after creation.",
    }

    plan = plan_acceptance_verifier(criterion)

    assert tuple(plan) == VERIFIER_PLAN_FIELDS
    assert plan["criterion_id"] == "AC-CREATE-LIST"
    assert plan["verifier_type"] == "http_sequence"
    assert plan["setup"] == ["start application"]
    assert plan["required_fixtures"] == ["request payload with a unique client or request identifier"]
    assert plan["actions"] == ["create request", "list requests"]
    assert plan["assertions"] == ["create succeeds", "created identifier appears in list"]
    assert plan["observable_expected_outcomes"]
    assert plan["execution_status"] == "not_executed"
    assert plan["verifier_type"] in VERIFIER_PLAN_TYPES


def test_update_criterion_plans_http_sequence_with_fixture_data():
    criterion = {
        "id": "AC-UPDATE",
        "title": "User can open an existing request and update its data.",
        "description": "Existing request operations change the stored request state.",
        "expected_result": "Updated request fields are observable through the API or UI.",
    }

    plan = plan_acceptance_verifier(criterion)

    assert plan["criterion_id"] == "AC-UPDATE"
    assert plan["verifier_type"] == "http_sequence"
    assert "existing request record" in plan["required_fixtures"]
    assert plan["actions"] == ["create or seed request", "update request", "fetch or list request"]
    assert "updated fields or status are observable after update" in plan["assertions"]
    assert plan["execution_status"] == "not_executed"


def test_persistence_criterion_plans_restart_verifier():
    criterion = {
        "id": "AC-PERSIST",
        "title": "Application data persists after restart.",
        "description": "Saved request data remains available after the process is stopped and started again.",
        "expected_result": "Data created before restart can be retrieved after restart.",
    }

    plan = plan_acceptance_verifier(criterion)

    assert plan["criterion_id"] == "AC-PERSIST"
    assert plan["verifier_type"] == "persistence_restart"
    assert plan["setup"] == ["start application with persistent storage"]
    assert plan["actions"] == ["create record", "stop application", "restart application", "retrieve or list records"]
    assert "same record is observable after restart" in plan["assertions"]
    assert plan["observable_expected_outcomes"] == ["post-restart read response contains the pre-restart identifier and data"]
    assert plan["execution_status"] == "not_executed"


def test_runtime_criterion_plans_runtime_start_not_pass():
    criterion = {
        "id": "AC-RUNTIME",
        "title": "The application starts successfully with the documented run command.",
        "description": "The delivered application can be started locally using the documented command.",
        "expected_result": "The documented run command starts the application successfully.",
        "verification_method": "runtime_smoke",
    }

    plan = plan_acceptance_verifier(criterion)

    assert plan["criterion_id"] == "AC-RUNTIME"
    assert plan["verifier_type"] == "runtime_start"
    assert "documented run command" in plan["required_fixtures"]
    assert plan["actions"] == ["start application", "probe observable endpoint or page", "stop owned process"]
    assert plan["execution_status"] == "not_executed"


def test_responsive_tablet_criterion_plans_responsive_ui():
    criterion = {
        "id": "AC-RESPONSIVE",
        "title": "The application layout remains usable on desktop and tablet viewport sizes.",
        "description": "Core screens and controls remain readable and usable on ordinary desktop and tablet viewport sizes.",
        "expected_result": "The UI remains usable on desktop and tablet widths without hiding required actions.",
    }

    plan = plan_acceptance_verifier(criterion)

    assert plan["criterion_id"] == "AC-RESPONSIVE"
    assert plan["verifier_type"] == "responsive_ui"
    assert plan["required_fixtures"] == ["desktop viewport size", "tablet viewport size", "core screen route or page"]
    assert plan["actions"] == ["render core screen at desktop width", "render core screen at tablet width"]
    assert "required controls remain visible" in plan["assertions"]
    assert plan["execution_status"] == "not_executed"


def test_unsupported_and_incomplete_criteria_do_not_get_fake_plans():
    unknown = {
        "id": "AC-UNKNOWN",
        "title": "The product feels delightful to users.",
        "description": "Subjective acceptance with no observable local outcome.",
        "expected_result": "Users feel delighted.",
    }
    incomplete = {"id": "AC-INCOMPLETE", "title": "Works"}

    unknown_plan = plan_acceptance_verifier(unknown)
    incomplete_plan = plan_acceptance_verifier(incomplete)

    for plan, criterion_id in ((unknown_plan, "AC-UNKNOWN"), (incomplete_plan, "AC-INCOMPLETE")):
        assert plan["criterion_id"] == criterion_id
        assert plan["verifier_type"] == "manual_or_unsupported"
        assert plan["setup"] == []
        assert plan["required_fixtures"] == []
        assert plan["actions"] == []
        assert plan["assertions"] == []
        assert plan["observable_expected_outcomes"] == []
        assert plan["execution_status"] == "not_executed"


def test_generated_acceptance_criteria_include_non_executed_verifier_plans():
    project = _project("Planner Demo", "After creation, the new request appears in the common request list.")

    bundle = ensure_project_spec_bundle(project)

    planned = [criterion for criterion in bundle["acceptance_criteria"] if criterion.get("verifier_plan")]
    assert planned
    assert all(criterion["status"] == "pending" for criterion in planned)
    assert all(criterion["verifier_plan"]["execution_status"] == "not_executed" for criterion in planned)


@pytest.mark.parametrize(
    ("criterion", "expected_type"),
    [
        (
            {"id": "AC-FILE", "title": "Project has delivery documentation", "description": "Root documentation explains installation, run, and test commands.", "expected_result": "README.md exists.", "verification_method": "file_check"},
            "file_artifact",
        ),
        (
            {"id": "AC-COMMAND", "title": "Frontend production build succeeds", "description": "Frontend builds in production mode.", "expected_result": "npm run build exits with code 0.", "verification_method": "command"},
            "command",
        ),
        (
            {"id": "AC-IMPORT", "title": "FastAPI application imports", "description": "FastAPI app module imports successfully.", "expected_result": "Expected ASGI app can be imported.", "verification_method": "python_import"},
            "python_import",
        ),
        (
            {"id": "AC-HTTP", "title": "The home page displays current request summary counts.", "description": "The application shows request counts.", "expected_result": "Summary counters reflect current data."},
            "http_single",
        ),
    ],
)
def test_remaining_supported_verifier_plan_types_are_planned_not_executed(criterion, expected_type):
    plan = plan_acceptance_verifier(criterion)

    assert plan["criterion_id"] == criterion["id"]
    assert plan["verifier_type"] == expected_type
    assert plan["required_fixtures"]
    assert plan["observable_expected_outcomes"]
    assert plan["execution_status"] == "not_executed"


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


def test_credential_fallback_strategy_does_not_block_generation():
    project = _project(
        "Credential Fallback MVP",
        """
        Build a booking MVP with DATABASE_URL, STRIPE_API_KEY, and SMTP_PASSWORD in .env.example.
        The app must remain runnable and testable without external credentials.
        Use SQLite fallback when DATABASE_URL is not provided.
        Use mock payment provider / disabled Stripe mode when STRIPE_API_KEY is not provided.
        Use console/log notification provider when SMTP settings are not provided.
        Tests must mock Stripe, SMTP, and all network calls.
        """,
    )

    bundle = ensure_project_spec_bundle(project)
    credentials = {c["name"]: c for c in bundle["project_spec"]["required_credentials"]}
    missing_credential_gaps = [
        gap for gap in bundle["project_spec"]["requirement_gaps"] if gap["category"] == "missing_credential"
    ]

    assert {"DATABASE_URL", "STRIPE_API_KEY", "SMTP_PASSWORD"} <= set(credentials)
    assert credentials["DATABASE_URL"]["blocks_completion"] is False
    assert credentials["STRIPE_API_KEY"]["blocks_completion"] is False
    assert credentials["SMTP_PASSWORD"]["blocks_completion"] is False
    assert missing_credential_gaps == []


def test_mockable_infrastructure_credentials_do_not_block_manual_generation_by_default():
    project = _project(
        "Beauty Booking",
        "Build a salon booking app with payments, database persistence, appointment reminders, and email notifications.",
    )

    bundle = ensure_project_spec_bundle(project)
    credentials = {c["name"]: c for c in bundle["project_spec"]["required_credentials"]}
    missing_credential_gaps = [
        gap for gap in bundle["project_spec"]["requirement_gaps"] if gap["category"] == "missing_credential"
    ]

    assert {"STRIPE_API_KEY", "DATABASE_URL"} <= set(credentials)
    assert credentials["STRIPE_API_KEY"]["blocks_completion"] is False
    assert credentials["DATABASE_URL"]["blocks_completion"] is False
    assert "SMTP_PASSWORD" not in credentials
    assert missing_credential_gaps == []


def test_mobile_native_requirement_cannot_be_satisfied_by_responsive_web_runtime():
    decision = product_runtime_decision(
        "Create a cross-platform mobile application for Android and iOS.",
        ["fastapi", "REST_API", "react_frontend", "vite_frontend"],
    )

    assert decision["mobile_native_installation_mandatory"] is True
    assert decision["requested_product_kind"] == "android_ios_mobile_application"
    assert decision["selected_product_kind"] == "web_application"
    assert decision["selected_ui_runtime"] == "browser"
    assert decision["compatible_before_coding"] is False


def test_requested_product_kind_and_selected_runtime_must_be_compatible_before_coding():
    decision = product_runtime_decision(
        "Create a cross-platform mobile application for Android and iOS using Expo React Native.",
        ["expo_react_native", "mobile_application", "fastapi"],
    )

    assert decision["selected_implementation_framework"] == "Expo / React Native"
    assert decision["packaging_target"] == "android_ios_app"
    assert decision["compatible_before_coding"] is True


def test_incompatible_product_runtime_blocks_before_generation_or_requests_clarification():
    project = _project("Mobile mismatch", "Create a cross-platform mobile application for Android and iOS.")

    bundle = ensure_project_spec_bundle(project)
    spec = bundle["project_spec"]
    gaps = [gap for gap in spec["requirement_gaps"] if gap["category"] == "product_runtime_mismatch"]

    assert spec["mobile_native_installation_mandatory"] is True
    assert spec["product_runtime_compatible_before_coding"] is False
    assert gaps and gaps[0]["severity"] == "blocker"


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


def test_fastapi_profile_detected_from_strong_file_evidence(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

    profiles = detect_project_profiles(project_path=str(tmp_path), text="Build a local request tracker.")

    assert "fastapi" in profiles
    assert "python_application" in profiles


def test_generic_python_script_is_not_fastapi(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")

    profiles = detect_project_profiles(project_path=str(tmp_path), text="Build a generic Python script.")

    assert "python_application" in profiles
    assert "fastapi" not in profiles


def test_static_web_project_detected_from_files(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><h1>Site</h1>", encoding="utf-8")

    profiles = detect_project_profiles(project_path=str(tmp_path), text="Build a local site.")

    assert "static_website" in profiles


def test_weak_fastapi_mentions_do_not_create_fastapi_profile(tmp_path: Path):
    (tmp_path / "README.md").write_text("This project is not a FastAPI application.", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('no framework')\n", encoding="utf-8")

    profiles = detect_project_profiles(project_path=str(tmp_path), text="Build a tool without FastAPI.")

    assert "fastapi" not in profiles


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
            "command": "python -c \"from pathlib import Path; assert Path('README.md').exists()\"",
            "exit_code": 0,
            "summary": "README existence check passed.",
            "artifacts": ["acceptance-readme-check.log"],
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


def test_acceptance_evidence_execution_contract_fields_are_recorded():
    project = _project("Contract Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    record_acceptance_evidence(
        project,
        criterion_id,
        "passed",
        {
            "source": "unit_test",
            "method": "command",
            "setup_steps": ["Create fixture project"],
            "action_steps": ["Run focused criterion check"],
            "assertions": ["README.md exists"],
            "collected_evidence": {"path": "README.md", "exists": True},
        },
    )

    evidence = project["acceptance_criteria"][0]["evidence"][-1]
    assert all(field in evidence for field in ACCEPTANCE_CONTRACT_FIELDS)
    assert evidence["verifier_type"] == "command"
    assert evidence["verdict"] == "passed"
    assert evidence["failure_reason"] == ""
    assert evidence["collected_evidence"] == {"path": "README.md", "exists": True}
    assert evidence["verdict"] in ACCEPTANCE_VERDICTS
    assert acceptance_evidence_is_direct(evidence, criterion_id) is True


def test_passed_acceptance_evidence_requires_evidence():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    with pytest.raises(ValueError, match="requires"):
        record_acceptance_evidence(project, criterion_id, "passed", {})


def test_passed_acceptance_evidence_rejects_global_qa_and_keyword_only_payloads():
    project = _project("Evidence Demo", "Build a simple FastAPI app.")
    ensure_project_spec_bundle(project)
    criterion_id = project["acceptance_criteria"][0]["id"]

    with pytest.raises(ValueError, match="direct criterion-specific"):
        record_acceptance_evidence(
            project,
            criterion_id,
            "passed",
            {"source": "qa_engine", "method": "global_qa", "summary": "Global QA passed", "qa_success": True},
        )

    with pytest.raises(ValueError, match="direct criterion-specific"):
        record_acceptance_evidence(
            project,
            criterion_id,
            "passed",
            {"source": "pytest", "method": "global_pytest", "summary": "python -m pytest -q passed", "command": "python -m pytest -q", "exit_code": 0},
        )

    with pytest.raises(ValueError, match="direct criterion-specific"):
        record_acceptance_evidence(
            project,
            criterion_id,
            "passed",
            {"source": "unit_test", "method": "command", "summary": "All tests passed", "command": "python -m pytest -q", "exit_code": 0},
        )

    with pytest.raises(ValueError, match="direct criterion-specific"):
        record_acceptance_evidence(
            project,
            criterion_id,
            "passed",
            {"source": "qa_engine", "method": "keyword_presence", "summary": "Keyword appeared in generated files."},
        )


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
