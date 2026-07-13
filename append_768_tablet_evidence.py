from __future__ import annotations
import hashlib, os
import project_state
root = "generated_projects/test-project-recovery"
s, e = project_state.load_project_state(root); assert not e
criteria = {c["id"]: c for c in s["acceptance_criteria"]}
ac3, _ = project_state.latest_valid_evidence(root, criteria["AC-003"])
d = ac3["evidence"]["collected_evidence"]
path = os.path.join(root, ".freelancerstudio", "evidence_artifacts", "browser_768x1024.png")
digest = hashlib.sha256(open(path, "rb").read()).hexdigest(); assert digest == "282d8d87c246738bc65b863c5a0e00cda7c6412941e8b6e032fb111870c162b1"
d.update({"completed_required_viewports":["1366x768","1920x1080","768x1024"],"remaining_required_viewports":["1024x768"]})
d["viewport_results"].append({"viewport":"768x1024","verdict":"passed","tablet_usability":"not_verified","horizontal_overflow":0,"clipped_controls":0,"offscreen_controls":7,"artifact":{"path":path,"sha256":digest}})
s["target_path"]=root;s["title"]=s.get("project_name","");s["status"]="failed_qa"
project_state.append_evidence_record(s,criteria["AC-003"],ac3["evidence"],root)
tablet={"status":"not_verified","verdict":"not_verified","verifier_type":"incremental_tablet_viewport","assertions":["768x1024 runtime passed; 1024x768 pending"],"collected_evidence":{"contract_fingerprint":s["contract_migration"]["contract_fingerprint"],"completed_tablet_viewports":["768x1024"],"remaining_tablet_viewports":["1024x768"],"artifact":{"path":path,"sha256":digest},"runtime":"passed"},"failure_reason":"tablet_viewport_matrix_incomplete"}
project_state.append_evidence_record(s,criteria["AC-017"],tablet,root);project_state.persist_project_state(s,root)
