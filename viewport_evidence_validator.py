"""Fresh-process validation for incremental AC-003 viewport evidence."""
from __future__ import annotations
import hashlib
from pathlib import Path
import project_state

SNAPSHOT = "9b71fb4ce69b78c4396d3b6f34c3079d22e512049049cfa19d1d0bc1a5060e5e"
CONTRACT = "7d7c5b8dc3120596d56fd94ce3858930c86bb2bc61d29bb6e29528d7d1a1e735"
COMPLETED = ["1366x768", "1920x1080"]
REMAINING = ["768x1024", "1024x768"]
SUPPLEMENTAL = ["2560x1440"]
EXCLUDED = ["3440x1440"]
HASHES = {"1366x768": "337b59809667c232bc89bcd6303277e20372c4a0384ca0d040338211229e0fc9", "1920x1080": "3f879f27a7744eeee53e641ffe48da20657082feda987cc6ec06d893f319402b", "2560x1440": "6998b6a58739251199f72c3265794eee2a5899e38f4ced63db96e81824504f6d"}

def validate(root: str) -> None:
    state, error = project_state.load_project_state(root); assert not error
    assert project_state.project_snapshot_fingerprint(root) == SNAPSHOT
    assert state["contract_migration"]["contract_fingerprint"] == CONTRACT
    assert len(state["acceptance_criteria"]) == 23
    ledger, error = project_state.load_evidence_ledger(root); assert not error
    criterion = next(item for item in state["acceptance_criteria"] if item["id"] == "AC-003")
    record, reason = project_state.latest_valid_evidence(root, criterion, ledger); assert record and not reason
    evidence = record["evidence"]; data = evidence["collected_evidence"]
    assert record["status"] == "not_verified" and evidence["failure_reason"] == "viewport_matrix_incomplete"
    assert data["contract_fingerprint"] == CONTRACT and data["completed_required_viewports"] == COMPLETED and data["remaining_required_viewports"] == REMAINING
    assert data["supplemental_completed_viewports"] == SUPPLEMENTAL and data["excluded_optional_viewports"] == EXCLUDED
    results = {item["viewport"]: item for item in data["viewport_results"]}
    for viewport in COMPLETED + SUPPLEMENTAL:
        item = results[viewport]; artifact = item["artifact"]
        assert item["verdict"] == "passed" and Path(artifact["path"]).is_file()
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == HASHES[viewport] == artifact["sha256"]
        if viewport == "1920x1080": assert item["horizontal_overflow"] == 0

if __name__ == "__main__":
    validate(str(Path(__file__).resolve().parent / "generated_projects" / "test-project-recovery"))
