from pathlib import Path

import qa_engine
from qa_engine import OPENCODE_FIX_APPLIED, POLICY_GROUP_RULES, QAEngine, select_policy_groups


class ScriptedQAEngine(QAEngine):
    def __init__(self, *args, script=None, repair_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = script or []
        self.repair_callback = repair_callback
        self.repair_calls = 0
        self.captured_reports = []
        self.stage_calls = 0

    def _round(self):
        index = min(self.stage_calls, max(0, len(self.script) - 1))
        return self.script[index] if self.script else {}

    def stage_check_syntax(self):
        return True, []

    def stage_install_deps(self):
        return True, [], []

    def stage_build_and_run(self):
        data = self._round()
        errors = data.get("build_and_tests", [])
        return not errors, errors, []

    def stage_delivery_readiness(self):
        data = self._round()
        errors = data.get("delivery_readiness", [])
        return not errors, errors, []

    def stage_profile_checks(self):
        data = self._round()
        errors = data.get("profile_checks", [])
        return not errors, errors, []

    def stage_verify_files(self):
        data = self._round()
        errors = data.get("file_verification", [])
        self.stage_calls += 1
        return not errors, errors, []

    def _request_opencode_fix(self, errors, error_text, repair_report=None):
        self.repair_calls += 1
        self.captured_reports.append(repair_report or {})
        if self.repair_callback:
            self.repair_callback(self, repair_report or {})
        return {OPENCODE_FIX_APPLIED: True}


def _engine(tmp_path: Path, script, **kwargs):
    project = kwargs.pop("project", {"title": "Test", "logs": [], "_qa_repair_limit": kwargs.pop("repair_limit", 3)})
    project.setdefault("logs", [])
    return ScriptedQAEngine(
        project=project,
        target_path=str(tmp_path),
        project_id="p1",
        provider="test",
        model="test",
        temperature=0,
        script=script,
        **kwargs,
    )


def test_policy_groups_are_declared_for_supported_profiles():
    assert set(POLICY_GROUP_RULES) == {"python", "fastapi", "telegram", "node", "react_vite", "static_web", "generic"}
    assert "python_requirements" in POLICY_GROUP_RULES["python"]
    assert "fastapi_root_entrypoint" in POLICY_GROUP_RULES["fastapi"]
    assert "telegram_local_smoke" in POLICY_GROUP_RULES["telegram"]
    assert "npm_build" in POLICY_GROUP_RULES["node"]
    assert "vite_runtime_scripts" in POLICY_GROUP_RULES["react_vite"]
    assert "static_assets" in POLICY_GROUP_RULES["static_web"]
    assert "readme_instructions" in POLICY_GROUP_RULES["generic"]


def test_policy_selection_maps_profiles_to_relevant_groups():
    assert select_policy_groups({"project_profiles": ["python_application"]}) == ["python"]
    assert select_policy_groups({"project_profiles": ["fastapi", "python_application"]}) == ["python", "fastapi"]
    assert select_policy_groups({"project_profiles": ["telegram_bot", "python_application"]}) == ["python", "telegram"]
    assert select_policy_groups({"project_profiles": ["node_project"]}) == ["node"]
    assert select_policy_groups({"project_profiles": ["react_frontend", "vite_frontend"]}) == ["node", "react_vite"]
    assert select_policy_groups({"project_profiles": ["static_website"]}) == ["static_web"]
    assert select_policy_groups({"project_profiles": ["unusual_custom"]}) == ["generic"]


def test_static_policy_does_not_apply_python_requirements_rule(tmp_path):
    (tmp_path / "README.md").write_text("# Site\n\nInstall: none\n\nRun: open index.html\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<h1>Static</h1>", encoding="utf-8")
    (tmp_path / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    engine = QAEngine(
        project={"title": "Static", "logs": [], "project_profiles": ["static_website"]},
        target_path=str(tmp_path),
        project_id="p1",
        provider="test",
        model="test",
        temperature=0,
    )

    ok, errors, _manual = engine.stage_delivery_readiness()

    assert ok is True
    assert engine.policy_groups == ["static_web"]
    assert not any("requirements.txt" in error for error in errors)


def test_scenario_a_qa_passes_immediately_no_repair(tmp_path):
    engine = _engine(tmp_path, [{}])

    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 0


def test_scenario_b_failure_invokes_opencode_and_full_qa_reruns(tmp_path):
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}, {}])

    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 1
    assert engine.stage_calls == 2
    assert engine.captured_reports[0]["failed_checks"][0]["check"] == "delivery_readiness"


def test_scenario_c_repair_fixes_failure(tmp_path):
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}, {}])

    result = engine.run()

    assert result["success"] is True
    assert engine.round_history[-1]["success"] is True
    assert any("[FIXED]" in log for log in engine.logs)


def test_scenario_d_repair_introduces_regression(tmp_path):
    engine = _engine(
        tmp_path,
        [
            {"delivery_readiness": ["Missing README.md"]},
            {"build_and_tests": ["pytest failed: AssertionError in tests/test_main.py"]},
        ],
        repair_limit=1,
    )

    result = engine.run()

    assert result["success"] is False
    assert any("[REGRESSION] Previously passing check now fails: build_and_tests" in log for log in engine.logs)


def test_scenario_e_same_error_fingerprint_escalates_strategy(tmp_path):
    script = [{"delivery_readiness": ["Missing README.md"]}] * 4
    engine = _engine(tmp_path, script, repair_limit=3)

    result = engine.run()

    notes = [report.get("repair_strategy_note", "") for report in engine.captured_reports]
    assert result["success"] is False
    assert engine.repair_calls == 3
    assert any("survived a previous fix" in note for note in notes)
    assert any("survived multiple repair attempts" in note for note in notes)


def test_scenario_f_repair_limit_reached_never_completed(tmp_path):
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}] * 3, repair_limit=1)

    result = engine.run()

    assert result["success"] is False
    assert engine.repair_calls == 1


def test_scenario_g_missing_credentials_stops_repair_loop(tmp_path):
    project = {
        "title": "AI app",
        "logs": [],
        "_qa_repair_limit": 3,
        "project_spec": {"required_credentials": [{"name": "OPENAI_API_KEY"}]},
    }
    engine = _engine(tmp_path, [{"delivery_readiness": ["OPENAI_API_KEY missing or not set"]}], project=project)

    result = engine.run()

    assert result["needs_credentials"] is True
    assert result["success"] is False
    assert engine.repair_calls == 0


def test_requirement_gap_missing_credentials_stops_before_repair(tmp_path):
    project = {
        "title": "AI app",
        "logs": [],
        "_qa_repair_limit": 3,
        "project_spec": {
            "requirement_gaps": [
                {
                    "id": "GAP-001",
                    "category": "missing_credential",
                    "severity": "blocker",
                    "suggested_question": "Provide OPENAI_API_KEY.",
                }
            ]
        },
    }
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}], project=project)

    result = engine.run()

    assert result["needs_credentials"] is True
    assert result["rounds_completed"] == 0
    assert engine.repair_calls == 0


def test_requirement_gap_critical_ambiguity_needs_human_input_no_repair(tmp_path):
    project = {
        "title": "Ambiguous app",
        "logs": [],
        "_qa_repair_limit": 3,
        "project_spec": {
            "requirement_gaps": [
                {
                    "id": "GAP-001",
                    "category": "ambiguity",
                    "severity": "critical",
                    "suggested_question": "Clarify the core workflow.",
                }
            ]
        },
    }
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}], project=project)

    result = engine.run()

    assert result["needs_human_input"] is True
    assert result["manual_steps"] == ["Clarify the core workflow."]
    assert engine.repair_calls == 0


def test_scenario_h_opencode_unavailable_no_fake_completion(tmp_path, monkeypatch):
    class UnavailableEngine(ScriptedQAEngine):
        def _request_opencode_fix(self, errors, error_text, repair_report=None):
            self.repair_calls += 1
            self.captured_reports.append(repair_report or {})
            return None

    monkeypatch.setattr(qa_engine, "ask_studio_ai_with_history", lambda **_kwargs: "not json")
    engine = UnavailableEngine(
        project={"title": "Test", "logs": [], "_qa_repair_limit": 1},
        target_path=str(tmp_path),
        project_id="p1",
        provider="test",
        model="test",
        temperature=0,
        script=[{"delivery_readiness": ["Missing README.md"]}],
    )

    result = engine.run()

    assert result["success"] is False
    assert engine.repair_calls == 1
    assert any("LEGACY JSON FALLBACK" in log for log in engine.logs)


def test_duplicate_qa_failure_reuses_issue_lineage_and_preserves_repair_history(tmp_path):
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}] * 2, repair_limit=1)

    result = engine.run()
    issues = engine.project["issues"]

    assert result["success"] is False
    assert len(issues) == 1
    issue = issues[0]
    assert issue["source"] == "qa_engine"
    assert issue["id"] == "ISSUE-QA-" + issue["evidence"]["fingerprint"].upper()
    assert issue["verification_method"] == "delivery_readiness"
    assert issue["attempts"] == 1
    assert issue["evidence"]["failure_registry"]["occurrence_count"] == 2
    assert len(issue["evidence"]["repair_history"]) == 1
    assert issue["evidence"]["repair_history"] == engine.repair_history


def test_new_qa_failure_creates_new_issue_without_overwriting_existing_lineage(tmp_path):
    engine = _engine(
        tmp_path,
        [
            {"delivery_readiness": ["Missing README.md"]},
            {"build_and_tests": ["pytest failed: AssertionError in tests/test_main.py"]},
        ],
        repair_limit=1,
    )

    result = engine.run()
    issues = engine.project["issues"]
    fingerprints = [issue["evidence"]["fingerprint"] for issue in issues]

    assert result["success"] is False
    assert len(issues) == 2
    assert len(set(fingerprints)) == 2
    assert {issue["verification_method"] for issue in issues} == {"delivery_readiness", "build_and_tests"}
    by_method = {issue["verification_method"]: issue for issue in issues}
    assert by_method["delivery_readiness"]["status"] == "closed"
    assert by_method["delivery_readiness"]["evidence"]["resolution"]["status"] == "passed"
    assert by_method["build_and_tests"]["status"] == "open"


def test_qa_issue_closes_only_after_verifier_rerun_evidence(tmp_path):
    engine = _engine(tmp_path, [{"delivery_readiness": ["Missing README.md"]}, {}], repair_limit=1)

    result = engine.run()
    issue = engine.project["issues"][0]

    assert result["success"] is True
    assert issue["status"] == "closed"
    assert issue["evidence"]["resolution"]["source"] == "qa_engine"
    assert issue["evidence"]["resolution"]["status"] == "passed"
    assert issue["evidence"]["resolution"]["round"] == 2


def test_opencode_fixed_text_alone_does_not_close_qa_issue(tmp_path):
    class TextOnlyFixEngine(ScriptedQAEngine):
        def _request_opencode_fix(self, errors, error_text, repair_report=None):
            self.repair_calls += 1
            self.captured_reports.append(repair_report or {})
            return {OPENCODE_FIX_APPLIED: True, "summary": "Fixed all issues"}

    engine = TextOnlyFixEngine(
        project={"title": "Test", "logs": [], "_qa_repair_limit": 1},
        target_path=str(tmp_path),
        project_id="p1",
        provider="test",
        model="test",
        temperature=0,
        script=[{"delivery_readiness": ["Missing README.md"]}] * 2,
    )

    result = engine.run()
    issue = engine.project["issues"][0]

    assert result["success"] is False
    assert issue["status"] == "open"
    assert "resolution" not in issue["evidence"]
    assert issue["attempts"] == 1
