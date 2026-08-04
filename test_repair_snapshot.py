import os
import time
from pathlib import Path

import qa_engine
from qa_engine import QAEngine, OPENCODE_FIX_APPLIED, _snapshot_project_files, _compare_snapshots, _SNAPSHOT_FILE_EXTS


class SnapshotQAEngine(QAEngine):
    """QAEngine with controlled _request_opencode_fix behaviour for snapshot/repair tests."""

    def __init__(self, *args, script=None, oc_result=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = script or []
        self.oc_result = oc_result
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
        has_files = any(
            os.path.isfile(os.path.join(self.target_path, f))
            for f in os.listdir(self.target_path) if os.path.splitext(f)[1].lower() in _SNAPSHOT_FILE_EXTS
        ) if os.path.isdir(self.target_path) else False

        if self.oc_result is not None:
            timed_out = self.oc_result.get("timed_out", False)
            success = self.oc_result.get("success", False)
            if success or (timed_out and has_files) or (not success and not timed_out and has_files):
                return {OPENCODE_FIX_APPLIED: True, "session_id": self.oc_result.get("session_id", "test-session"), "changed_files": 1 if has_files else 0}
            return {
                "success": False,
                "status": "subprocess_timeout" if timed_out else "subprocess_nonzero_exit",
                "timeout": timed_out,
                "session_id": self.oc_result.get("session_id", "test-session"),
                "changed_files": [],
                "meaningful_changes_detected": False,
                "filesystem_changes_detected": False,
            }

        if has_files:
            return {OPENCODE_FIX_APPLIED: True, "session_id": "test-session", "changed_files": 1}
        return None


def _engine(tmp_path: Path, script, **kwargs):
    project = kwargs.pop("project", {})
    project.setdefault("title", "Test")
    project.setdefault("logs", [])
    project.setdefault("_qa_repair_limit", kwargs.pop("repair_limit", 3))
    oc_result = kwargs.pop("oc_result", None)
    return SnapshotQAEngine(
        project=project,
        target_path=str(tmp_path),
        project_id="p1",
        provider="test",
        model="test",
        temperature=0,
        script=script,
        oc_result=oc_result,
    )


# ── Snapshot utilities ─────────────────────────────────────────────────

def test_snapshot_captures_relevant_files(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "README.md").write_text("# doc")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_app.py").write_text("def test(): pass")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "app.cpython.pyc").write_text("\x00")
    (tmp_path / "data.db").write_text("binary")
    (tmp_path / "image.png").write_text("png")

    snapshot = _snapshot_project_files(str(tmp_path))

    assert "app.py" in snapshot
    assert "README.md" in snapshot
    assert "tests/test_app.py" in snapshot
    assert "tests/__init__.py" in snapshot
    assert "__pycache__/app.cpython.pyc" not in snapshot
    assert "data.db" not in snapshot
    assert "image.png" not in snapshot


def test_snapshot_detects_added_file(tmp_path: Path):
    before = _snapshot_project_files(str(tmp_path))
    (tmp_path / "app.py").write_text("x = 1")
    after = _snapshot_project_files(str(tmp_path))
    changed = _compare_snapshots(before, after)
    assert len(changed) == 1
    assert "app.py [NEW]" in changed[0]


def test_snapshot_detects_modified_file(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1")
    before = _snapshot_project_files(str(tmp_path))
    time.sleep(0.02)
    (tmp_path / "app.py").write_text("x = 2")
    after = _snapshot_project_files(str(tmp_path))
    changed = _compare_snapshots(before, after)
    assert len(changed) == 1
    assert "app.py [MODIFIED]" in changed[0]


def test_snapshot_detects_deleted_file(tmp_path: Path):
    p = tmp_path / "app.py"
    p.write_text("x = 1")
    before = _snapshot_project_files(str(tmp_path))
    os.remove(str(p))
    after = _snapshot_project_files(str(tmp_path))
    changed = _compare_snapshots(before, after)
    assert len(changed) == 1
    assert "app.py [DELETED]" in changed[0]


def test_snapshot_no_change_detected(tmp_path: Path):
    p = tmp_path / "app.py"
    p.write_text("x = 1")
    before = _snapshot_project_files(str(tmp_path))
    after = _snapshot_project_files(str(tmp_path))
    changed = _compare_snapshots(before, after)
    assert changed == []


# ── Repair orchestration: 5 cases ───────────────────────────────────────

def test_case_a_normal_direct_repair_with_filechange(tmp_path: Path):
    """OpenCode edits files; snapshot detects changes; no JSON required."""
    (tmp_path / "app.py").write_text("fail")
    oc_result = {"success": True, "timed_out": False, "session_id": "sess-1"}
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}, {}],
        oc_result=oc_result,
    )
    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 1
    assert any("LEGACY JSON FALLBACK" not in log for log in engine.logs)
    assert any("OpenCode repair completed" in log or "Skipping legacy fallback" in log for log in engine.logs)
    assert engine.stage_calls == 2


def test_case_b_timeout_with_file_changes_runs_full_qa(tmp_path: Path):
    """OpenCode timed out but files changed; JSON fallback does NOT run immediately."""
    (tmp_path / "app.py").write_text("fail")
    oc_result = {"success": False, "timed_out": True, "error": "OpenCode task timed out", "session_id": "sess-t"}
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}, {}],
        oc_result=oc_result,
    )
    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 1
    assert any("OpenCode exceeded the timeout" in log or "Skipping legacy fallback" in log or "OpenCode modified" in log for log in engine.logs)
    assert engine.stage_calls == 2


def test_case_c_timeout_no_changes_no_fake_success(tmp_path: Path):
    """OpenCode timed out, no files changed — repair marked unsuccessful."""
    oc_result = {"success": False, "timed_out": True, "error": "OpenCode task timed out", "session_id": "sess-t"}
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}],
        oc_result=oc_result,
        repair_limit=1,
    )
    result = engine.run()

    assert result["success"] is False
    assert engine.repair_calls == 1
    assert any("No meaningful files changed" in log or "LEGACY JSON FALLBACK" in log or "Direct repair produced no changes" in log for log in engine.logs)


def test_case_d_nonzero_exit_with_filechange_qa_runs(tmp_path: Path):
    """OpenCode exits non-zero but changed files — QA runs and determines success."""
    (tmp_path / "app.py").write_text("fail")
    oc_result = {"success": False, "timed_out": False, "error": "some error", "session_id": "sess-e"}
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}, {}],
        oc_result=oc_result,
    )
    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 1
    assert any("exited with error" in log or "OpenCode modified" in log for log in engine.logs)
    assert engine.stage_calls == 2


def test_case_e_invalid_output_not_json_still_works(tmp_path: Path):
    """OpenCode result dict has no JSON content key — still correct since we only use flags."""
    (tmp_path / "app.py").write_text("fail")
    oc_result = {
        "success": True,
        "timed_out": False,
        "session_id": "sess-j",
        "summary": "just random text, not JSON",
    }
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}, {}],
        oc_result=oc_result,
    )
    result = engine.run()

    assert result["success"] is True
    assert engine.repair_calls == 1
    assert any("Skipping legacy fallback" in log for log in engine.logs)
    assert engine.stage_calls == 2


def test_case_f_repair_creates_regression(tmp_path: Path):
    """Files changed, old check passes, new check fails — full QA catches regression."""
    (tmp_path / "app.py").write_text("fail")
    oc_result = {"success": True, "timed_out": False, "session_id": "sess-r"}
    engine = _engine(
        tmp_path,
        [
            {"delivery_readiness": ["missing file"]},
            {"build_and_tests": ["pytest failed"]},
        ],
        oc_result=oc_result,
        repair_limit=1,
    )
    result = engine.run()

    assert result["success"] is False
    assert engine.repair_calls == 1
    assert any("[REGRESSION]" in log for log in engine.logs)


def test_case_g_legacy_fallback_no_filechange(tmp_path: Path, monkeypatch):
    """OpenCode produces no disk changes, legacy fallback is clearly labelled."""
    monkeypatch.setattr(qa_engine, "ask_studio_ai_with_history", lambda **_kwargs: "not json")
    engine = _engine(
        tmp_path,
        [{"delivery_readiness": ["missing file"]}],
        oc_result={"success": False, "timed_out": False, "error": "failed", "session_id": "sess-l"},
        repair_limit=1,
    )
    result = engine.run()

    assert result["success"] is False
    assert engine.repair_calls == 1
    assert any("LEGACY JSON FALLBACK" in log for log in engine.logs)
    assert any("Direct repair produced no changes" in log for log in engine.logs)
