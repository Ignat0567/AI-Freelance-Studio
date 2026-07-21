import json
import os
from pathlib import Path

import pytest

from sandbox_test_lab.capability import detect_sandbox_capability
from sandbox_test_lab.fixture_builder import FIXTURE_GUI_EXECUTABLE_NAME, build_fixture, trusted_makensis_path
from sandbox_test_lab.fixture_launch import (
    CONTROLLED_LAUNCH_PROFILE,
    LAUNCH_EXTERNAL_OPT_IN,
    LAUNCH_TIMEOUT_SECONDS,
    FixtureLaunchRequest,
    FixtureLaunchWorkspaceManager,
)
from sandbox_test_lab.installer import ApplicationTestRequest, InstallationRecipe, InstallerKind
from sandbox_test_lab.launch_runner import InstallLaunchSandboxRunner
from sandbox_test_lab.models import RunStatus


pytestmark = pytest.mark.external
EXTERNAL_MARK_EXPRESSION = "external"


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(LAUNCH_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == EXTERNAL_MARK_EXPRESSION


@pytest.mark.timeout(390)
def test_windows_sandbox_phase2c_controlled_gui_first_launch(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {LAUNCH_EXTERNAL_OPT_IN}=1 and select exactly -m external to run the controlled GUI test")
    capability = detect_sandbox_capability()
    if not capability.available:
        pytest.skip("Windows Sandbox capability is unavailable")
    try:
        compiler = trusted_makensis_path()
    except (FileNotFoundError, ValueError):
        pytest.skip("trusted electron-builder makensis.exe is unavailable")

    build = build_fixture(compiler=compiler)
    artifact = Path(build.output)
    recipe = InstallationRecipe(
        InstallerKind.NSIS_EXE, artifact.name, build.sha256, install_timeout_seconds=180,
        launch_timeout_seconds=LAUNCH_TIMEOUT_SECONDS, expected_executable=FIXTURE_GUI_EXECUTABLE_NAME,
        expected_process_name=FIXTURE_GUI_EXECUTABLE_NAME,
        success_requirements=(
            "artifact_hash_verified", "installer_exit_zero", "expected_executable_found",
            "process_started", "first_launch_verified",
        ),
    )
    launch_request = FixtureLaunchRequest(
        ApplicationTestRequest(artifact, build.sha256, recipe), CONTROLLED_LAUNCH_PROFILE, timeout_seconds=300,
    )
    result = InstallLaunchSandboxRunner(
        workspace_manager=FixtureLaunchWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability,
    ).run(launch_request)

    evidence = json.loads(Path(result.evidence_path).read_text(encoding="utf-8")) if result.evidence_path else {}
    report = {
        "build": build.to_dict(), "run_id": result.run_id, "status": result.status.value,
        "exit_reason": result.exit_reason, "installation_passed": evidence.get("installation_passed"),
        "owned_process_id": evidence.get("owned_process_id"), "window_handle": evidence.get("window_handle"),
        "window_title": evidence.get("window_title"), "stable_window_duration_seconds": evidence.get("stable_window_duration_seconds"),
        "first_launch_verified": evidence.get("first_launch_verified"),
        "forced_owned_process_cleanup": evidence.get("forced_owned_process_cleanup"),
        "cleanup_process_exited": evidence.get("cleanup_process_exited"),
        "errors": list(result.errors), "warnings": list(result.warnings),
    }
    print("PHASE2C_EXTERNAL_REPORT=" + json.dumps(report, sort_keys=True))
    assert result.status == RunStatus.PASSED, report
    assert evidence["installation_passed"] is True
    assert evidence["installed_executable_hash_verified"] is True
    assert evidence["process_image_verified"] is True
    assert evidence["window_owned_by_process"] is True
    assert evidence["window_handle_present"] is True
    assert evidence["window_title"] == "AIFS Sandbox Fixture"
    assert evidence["window_title_verified"] is True
    assert evidence["stable_window_duration_seconds"] >= 3
    assert evidence["first_launch_verified"] is True
    assert evidence["graceful_close_succeeded"] is not evidence["forced_owned_process_cleanup"]
    assert evidence["cleanup_process_exited"] is True
