import main
from pipeline_stage_metadata import PIPELINE_UI_STAGE_ORDER
from test_security_support import authorized_test_client


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
    p["final_delivery_report"] = {"final_status": "STRICT_MVP_ACCEPTED", "completion_policy": {"accepted": True}}
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


def test_meeting_can_surface_pre_generation_blockers():
    p = _project("meeting")
    assert main._set_project_status(p, "needs_credentials")
    assert p["status"] == "needs_credentials"

    q = _project("meeting")
    assert main._set_project_status(q, "needs_human_input")
    assert q["status"] == "needs_human_input"


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


def test_product_judge_structured_pass_requires_existing_evidence():
    project = _judge_project()
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is True
    assert errors == []
    assert project["product_judge_report"]["read_only"] is True


def test_product_judge_insufficient_evidence_does_not_pass():
    project = _judge_project()
    project["acceptance_criteria"] = [{"id": "AC-VISUAL", "title": "The product has a modern tidy visual design.", "priority": "high", "verification_method": "feature_trace_static_or_smoke"}]
    project["product_judge_results"] = {"AC-VISUAL": {"verdict": "insufficient_evidence"}}
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is False
    assert errors
    assert project["product_judge_report"]["status"] == "not_verified"
    assert project.get("issues", []) == []


def test_product_judge_stage_does_not_create_or_repair_issues_directly():
    project = _judge_project()
    criterion_id = "AC-VISUAL"
    project["acceptance_criteria"] = [{"id": criterion_id, "title": "The product has a modern tidy visual design.", "priority": "high", "verification_method": "feature_trace_static_or_smoke"}]
    project["product_judge_results"] = {criterion_id: {"verdict": "blocking_objection"}}
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "errors": []}

    ok, errors = main._run_product_judge_stage(project, qa_result)

    assert ok is False
    assert errors == [f"{criterion_id}: blocking_objection"]
    assert project.get("issues", []) == []


def test_backend_pipeline_metadata_is_source_of_truth_for_stages_and_agents():
    client = authorized_test_client(main.app)
    metadata = client.get("/api/pipeline/metadata").json()
    agents = main.get_agents()

    assert metadata["stage_order"] == PIPELINE_UI_STAGE_ORDER
    assert metadata["stages"]["product_judge"]["label"] == "Product Judge"
    assert metadata["agent_stages"]["codex"]["stage"] == "coding"
    assert agents["codex"]["stage"] == "coding"
    assert agents["codex"]["display_role"] == "Software Architect"
    assert agents["product_judge"]["role"] == "product_judge"
