from __future__ import annotations
import project_state

root = "generated_projects/test-project-recovery"
state, error = project_state.load_project_state(root)
assert not error
criterion = next(item for item in state["acceptance_criteria"] if item["id"] == "AC-003")
record, _ = project_state.latest_valid_evidence(root, criterion)
data = record["evidence"]["collected_evidence"]
data.update({
    "required_viewports": ["1366x768", "1920x1080", "768x1024", "1024x768"],
    "supplemental_viewports": ["2560x1440"],
    "excluded_optional_viewports": ["3440x1440"],
    "completed_required_viewports": ["1366x768", "1920x1080"],
    "remaining_required_viewports": ["768x1024", "1024x768"],
    "supplemental_completed_viewports": ["2560x1440"],
})
state["target_path"] = root; state["title"] = state.get("project_name", ""); state["status"] = "failed_qa"
project_state.append_evidence_record(state, criterion, record["evidence"], root)
project_state.persist_project_state(state, root)
