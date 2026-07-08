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

    assert not main._set_project_status(p, "completed")
    assert p["status"] == "coding"
