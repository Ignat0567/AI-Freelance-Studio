import asyncio
import sys
from pathlib import Path

from completion_semantics import compute_completion_status
from safe_command_runner import run_command
from snapshot_manager import SnapshotManager
from workflow_artifacts import RunArtifactStore


def test_run_artifact_store_creates_required_tree_and_masks_secrets(tmp_path):
    store = RunArtifactStore("run-test", tmp_path / "artifacts" / "runs")
    run_dir = store.initialize({"token": "secret-value"})
    store.write_json("effective_execution_config.json", {"model_id": "openai/gpt-5.5", "api_key": "test-secret-placeholder"})
    store.mark_skipped("browser_evidence/result.json", "non-ui project", required=False)
    store.finalize("PARTIALLY_COMPLETED", {"reason": "browser skipped"})

    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "stdout").is_dir()
    assert (run_dir / "stderr").is_dir()
    assert (run_dir / "snapshots" / "before").is_dir()
    assert "test-secret-placeholder" not in (run_dir / "effective_execution_config.json").read_text(encoding="utf-8")


def test_safe_command_runner_success_nonzero_and_timeout(tmp_path):
    ok = asyncio.run(run_command([sys.executable, "-c", "print('ok')"], str(tmp_path), timeout_seconds=5))
    bad = asyncio.run(run_command([sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"], str(tmp_path), timeout_seconds=5))
    slow = asyncio.run(run_command([sys.executable, "-c", "import time; time.sleep(2)"], str(tmp_path), timeout_seconds=1))

    assert ok.exit_code == 0 and ok.stdout.strip() == "ok"
    assert bad.exit_code == 7 and "bad" in bad.stdout
    assert slow.timed_out is True
    assert slow.exit_code is not None


def test_snapshot_create_modify_rollback(tmp_path):
    project = tmp_path / "project"
    artifacts = tmp_path / "artifacts" / "runs" / "r1"
    project.mkdir()
    (project / "app.txt").write_text("before", encoding="utf-8")
    manager = SnapshotManager(str(project), str(artifacts))
    before = manager.create_snapshot("before")
    (project / "app.txt").write_text("after", encoding="utf-8")
    (project / "new.txt").write_text("new", encoding="utf-8")
    after = manager.create_snapshot("after")
    diff = manager.diff_snapshots(before, after)
    rollback = manager.rollback(before)

    assert diff["created_files"] == ["new.txt"]
    assert diff["modified_files"] == ["app.txt"]
    assert rollback["status"] == "rolled_back"
    assert (project / "app.txt").read_text(encoding="utf-8") == "before"
    assert not (project / "new.txt").exists()


def test_completion_status_requires_evidence_and_browser_when_required():
    completed = compute_completion_status({"execution_completed": True, "changes_confirmed": True, "required_tests_passed": True, "browser_required": True, "browser_passed": True, "product_judge_required": True, "product_judge_verdict": "approved", "evidence_available": True})
    missing_browser = compute_completion_status({"execution_completed": True, "changes_confirmed": True, "required_tests_passed": True, "browser_required": True, "browser_passed": False, "product_judge_required": True, "product_judge_verdict": "approved", "evidence_available": True})

    assert completed["status"] == "COMPLETED"
    assert missing_browser["status"] == "PARTIALLY_COMPLETED"
    assert "browser_passed" in missing_browser["missing_evidence"]
