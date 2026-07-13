from __future__ import annotations
import hashlib
import os
import project_state
from viewport_evidence_validator import SNAPSHOT, CONTRACT, COMPLETED, REMAINING, HASHES

root = "generated_projects/test-project-recovery"
state, error = project_state.load_project_state(root)
assert not error and project_state.project_snapshot_fingerprint(root) == SNAPSHOT
criterion = next(item for item in state["acceptance_criteria"] if item["id"] == "AC-003")
old, _ = project_state.latest_valid_evidence(root, criterion)
prior = old["evidence"]["collected_evidence"]["viewport_results"]
path = os.path.join(root, ".freelancerstudio", "evidence_artifacts", "browser_2560x1440.png")
assert hashlib.sha256(open(path, "rb").read()).hexdigest() == HASHES["2560x1440"]
data = {"contract_fingerprint": CONTRACT, "completed_viewports": COMPLETED, "remaining_viewports": REMAINING, "viewport_results": prior + [{"viewport": "2560x1440", "verdict": "passed", "horizontal_overflow": 0, "console_diagnostics": [{"status": 401, "classification": "expected_authentication_challenge", "blocking": False}], "page_errors": [], "failed_requests": [], "large_desktop": {"main_content_width": 1220, "layout": "acceptable"}, "artifact": {"path": path, "sha256": HASHES["2560x1440"]}}]}
state["target_path"] = root; state["title"] = state.get("project_name", ""); state["status"] = "failed_qa"
project_state.append_evidence_record(state, criterion, {"status": "not_verified", "verdict": "not_verified", "verifier_type": "incremental_playwright_viewport_runtime", "assertions": ["three viewport runtimes passed; matrix incomplete"], "collected_evidence": data, "failure_reason": "viewport_matrix_incomplete"}, root)
project_state.persist_project_state(state, root)
