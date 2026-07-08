import main


def _project(status: str) -> dict:
    p = {"status": status, "logs": [], "cancel_requested": False}
    main._reset_delivery_gates(p)
    return p


def test_a_generation_finish_does_not_complete_without_qa_and_audit():
    p = _project("coding")
    main._mark_generation_finished(p, True)
    assert not main._set_project_status(p, "completed")
    assert p["status"] == "coding"


def test_b_qa_failure_reaches_failed_qa():
    p = _project("verifying")
    main._mark_generation_finished(p, True)
    main._mark_qa_passed(p, False)
    assert main._set_project_status(p, "failed_qa")
    assert p["status"] == "failed_qa"


def test_c_completion_requires_final_audit_step():
    p = _project("verifying")
    main._mark_generation_finished(p, True)
    main._mark_qa_passed(p, True)

    assert not main._set_project_status(p, "completed")
    assert main._set_project_status(p, "final_audit")

    main._mark_final_audit_passed(p, True)
    assert not main._set_project_status(p, "completed")
    assert main._set_project_status(p, "product_judge")
    main._mark_product_judge_passed(p, True)
    assert main._set_project_status(p, "completed")
    assert p["status"] == "completed"


def test_d_final_audit_failure_blocks_completion():
    p = _project("final_audit")
    main._mark_generation_finished(p, True)
    main._mark_qa_passed(p, True)
    main._mark_final_audit_passed(p, False)

    assert not main._set_project_status(p, "completed")
    assert p["status"] == "final_audit"


def test_e_cancelled_project_cannot_complete_from_stale_callback():
    p = _project("cancelled")
    p["cancel_requested"] = True
    main._mark_generation_finished(p, True)
    main._mark_qa_passed(p, True)
    main._mark_final_audit_passed(p, True)
    main._mark_product_judge_passed(p, True)

    assert not main._set_project_status(p, "completed")
    assert p["status"] == "cancelled"


def test_f_opencode_unavailable_path_never_completes():
    p = _project("blocked")
    main._mark_generation_finished(p, False)
    main._mark_qa_passed(p, False)
    main._mark_final_audit_passed(p, False)

    assert not main._set_project_status(p, "completed")
    assert p["status"] == "blocked"


def test_transition_examples_valid_and_invalid():
    p = _project("coding")
    assert main._set_project_status(p, "verifying")
    assert main._set_project_status(p, "repairing")
    assert main._set_project_status(p, "verifying")
    assert main._set_project_status(p, "final_audit")

    q = _project("created")
    assert not main._set_project_status(q, "completed")


def test_critical_ambiguity_blocks_before_coding_with_human_input_state():
    p = _project("planning")
    p["project_spec"] = {
        "requirement_gaps": [
            {
                "id": "GAP-001",
                "category": "ambiguity",
                "severity": "critical",
                "suggested_question": "Which workflow is required?",
            }
        ]
    }

    assert main._apply_requirement_gap_blockers(p) is True
    assert p["status"] == "needs_human_input"
    assert p["needs_human_input"] is True
    assert not main._set_project_status(p, "coding")


def test_missing_credential_gap_blocks_to_credentials_state():
    p = _project("planning")
    p["project_spec"] = {
        "requirement_gaps": [
            {
                "id": "GAP-001",
                "category": "missing_credential",
                "severity": "blocker",
                "suggested_question": "Provide OPENAI_API_KEY.",
            }
        ]
    }

    assert main._apply_requirement_gap_blockers(p) is True
    assert p["status"] == "needs_credentials"
    assert p["needs_credentials"] is True
    assert p["manual_steps"] == ["Provide OPENAI_API_KEY."]


def test_non_critical_requirement_gaps_continue_as_recorded_assumptions():
    p = _project("planning")
    p["project_spec"] = {
        "requirement_gaps": [
            {
                "id": "GAP-001",
                "category": "ambiguity",
                "severity": "medium",
                "suggested_question": "Define modern UI.",
            }
        ]
    }

    assert main._apply_requirement_gap_blockers(p) is False
    assert p["status"] == "planning"
    assert p["requirement_assumptions"][0]["id"] == "GAP-001"


def test_invalid_generating_to_completed_even_if_gates_are_true():
    p = _project("coding")
    main._mark_generation_finished(p, True)
    main._mark_qa_passed(p, True)
    main._mark_final_audit_passed(p, True)
    main._mark_product_judge_passed(p, True)

    assert not main._set_project_status(p, "completed")
    assert p["status"] == "coding"


def _judge_project() -> dict:
    project = _project("final_audit")
    project.update({"title": "Judge Demo", "description": "Build a todo API.", "chat_history": []})
    main.ensure_project_spec_bundle(project)
    project["final_delivery_report"] = {"status": "passed", "runtime_verification": {"status": "passed"}, "known_limitations": []}
    return project


def test_product_judge_structured_pass_requires_existing_evidence(monkeypatch):
    project = _judge_project()
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}
    monkeypatch.setattr(main, "_call_product_judge", lambda bundle: {"status": "pass", "objections": []})

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is True
    assert errors == []
    assert project["product_judge_report"]["valid_objection_count"] == 0
    assert "product_judge_input" in project


def test_product_judge_text_pass_alone_does_not_pass(monkeypatch):
    project = _judge_project()
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}
    monkeypatch.setattr(main, "_call_product_judge", lambda bundle: main._parse_product_judge_response("PASS"))

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is False
    assert errors
    assert project["product_judge_report"]["status"] == "invalid"
    assert project.get("issues", []) == []


def test_product_judge_valid_objection_becomes_open_issue_without_hidden_log(monkeypatch):
    project = _judge_project()
    requirement_id = project["project_spec"]["requirements"][0]["id"]
    criterion_id = project["acceptance_criteria"][0]["id"]
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}
    objection = {
        "requirement_id": requirement_id,
        "criterion_id": criterion_id,
        "severity": "high",
        "title": "No evidence proves the core todo behavior works",
        "evidence": "Acceptance evidence is documentation-only.",
        "reproduction": ["Run behavior test for todo creation"],
        "verification_method": "behavior_smoke",
        "hidden_reasoning": "do not log this",
    }
    monkeypatch.setattr(main, "_call_product_judge", lambda bundle: {"status": "fail", "objections": [objection]})

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is False
    assert errors == [f"{criterion_id}: No evidence proves the core todo behavior works"]
    assert len(project["issues"]) == 1
    issue = project["issues"][0]
    assert issue["source"] == "product_judge"
    assert issue["status"] == "open"
    assert issue["requirement_id"] == requirement_id
    assert issue["criterion_id"] == criterion_id
    assert issue["evidence"]["requires_verifier_evidence"] is True
    assert not any("hidden_reasoning" in log for log in project["logs"])
