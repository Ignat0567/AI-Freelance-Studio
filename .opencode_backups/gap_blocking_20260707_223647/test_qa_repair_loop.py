from pathlib import Path

import qa_engine
from qa_engine import QAEngine, OPENCODE_FIX_APPLIED


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
