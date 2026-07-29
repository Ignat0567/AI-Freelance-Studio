from workflow_contracts import ExecutionBrief, WorkflowState, validate_transition


def test_workflow_state_blocks_planning_to_completed():
    assert validate_transition(WorkflowState.PLANNING, WorkflowState.AWAITING_PLAN_APPROVAL)
    assert not validate_transition(WorkflowState.PLANNING, WorkflowState.COMPLETED)
    assert not validate_transition(WorkflowState.EXECUTING, WorkflowState.COMPLETED)


def test_execution_brief_requires_evidence_contract_fields():
    brief = ExecutionBrief(
        project_id="p1",
        task_id="t1",
        title="Demo",
        objective="Implement demo",
        project_root="C:/project",
        requirements=["Do it"],
        constraints=["No fake success"],
        acceptance_criteria=["Tests pass"],
        allowed_paths=["C:/project"],
        forbidden_paths=["C:/Users"],
        implementation_steps=["Edit files"],
        test_commands=["python -m pytest -q"],
        validation_commands=["Final audit"],
        requires_browser_validation=False,
        requires_security_review=True,
        approval_policy="required",
        sandbox_policy="project_root_containment",
    )

    assert brief.validate() == []
    assert brief.to_dict()["version"] == "1.0"
