import json
import os
from pathlib import Path

import pytest

from sandbox_test_lab.capability import detect_sandbox_capability
from sandbox_test_lab.models import RunStatus
from sandbox_test_lab.screenshot_runner import ScreenshotSelfTestRunner
from sandbox_test_lab.screenshot_self_test import (
    SCREENSHOT_EXTERNAL_OPT_IN,
    ScreenshotSelfTestRequest,
    ScreenshotSelfTestWorkspaceManager,
)


pytestmark = pytest.mark.external


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(SCREENSHOT_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == "external"


@pytest.mark.timeout(600)
def test_windows_sandbox_production_window_screenshot(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {SCREENSHOT_EXTERNAL_OPT_IN}=1 and select exactly -m external")
    capability = detect_sandbox_capability()
    if not capability.available:
        pytest.skip("Windows Sandbox capability is unavailable")

    workspace_manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    self_test = ScreenshotSelfTestRequest()
    result = ScreenshotSelfTestRunner(
        workspace_manager=workspace_manager,
        capability_detector=lambda: capability,
    ).run(self_test)
    snapshot = json.loads(Path(result.evidence_path).read_text(encoding="utf-8")) if result.evidence_path else {}
    if not snapshot:
        manifest_path = workspace_manager.paths_for(self_test.run_id).evidence_directory / "screenshot-evidence.json"
        try:
            if manifest_path.is_file() and not manifest_path.is_symlink() and manifest_path.stat().st_size <= 32 * 1024:
                candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    snapshot = candidate
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    report = {
        "run_id": result.run_id,
        "status": result.status.value,
        "exit_reason": result.exit_reason,
        "capture_method": snapshot.get("capture_method"),
        "window_bounds": snapshot.get("window_bounds"),
        "client_bounds": snapshot.get("client_bounds"),
        "dpi_scale": snapshot.get("dpi_scale"),
        "image_summary": snapshot.get("host_image_summary"),
        "dimensions": [snapshot.get("width"), snapshot.get("height")],
        "png_size": snapshot.get("file_size"),
        "png_sha256": snapshot.get("sha256"),
        "production_fully_ready": snapshot.get("provenance", {}).get("production_fully_ready"),
        "production_cleanup_complete": snapshot.get("provenance", {}).get("production_cleanup_complete"),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    print("PHASE3B_EXTERNAL_REPORT=" + json.dumps(report, sort_keys=True))
    assert result.status == RunStatus.PASSED, report
    assert snapshot["validation_status"] == "passed"
    assert snapshot["provenance"]["production_fully_ready"] is True
    assert snapshot["provenance"]["production_cleanup_complete"] is True
