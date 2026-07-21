import json
import os
from pathlib import Path

import pytest

from sandbox_test_lab.capability import detect_sandbox_capability
from sandbox_test_lab.fixture_builder import build_fixture, trusted_makensis_path
from sandbox_test_lab.fixture_installation import (
    CONTROLLED_FIXTURE_PROFILE,
    INSTALL_EXTERNAL_OPT_IN,
    FixtureInstallationRequest,
    FixtureInstallationWorkspaceManager,
)
from sandbox_test_lab.installation_runner import InstallationSandboxRunner
from sandbox_test_lab.installer import ApplicationTestRequest, InstallationRecipe, InstallerKind
from sandbox_test_lab.models import RunStatus


pytestmark = pytest.mark.external
EXTERNAL_MARK_EXPRESSION = "external"


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(INSTALL_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == EXTERNAL_MARK_EXPRESSION


@pytest.mark.timeout(330)
def test_windows_sandbox_phase2b_controlled_nsis_fixture(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {INSTALL_EXTERNAL_OPT_IN}=1 and select exactly -m external to run the controlled NSIS installation test")
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
        installer_kind=InstallerKind.NSIS_EXE,
        artifact_name=artifact.name,
        artifact_sha256=build.sha256,
        install_timeout_seconds=180,
        launch_timeout_seconds=1,
        success_requirements=("artifact_hash_verified", "installer_exit_zero"),
    )
    application = ApplicationTestRequest(artifact, build.sha256, recipe)
    install_request = FixtureInstallationRequest(application, CONTROLLED_FIXTURE_PROFILE, timeout_seconds=240)
    manager = FixtureInstallationWorkspaceManager(tmp_path / "runtime")
    result = InstallationSandboxRunner(
        workspace_manager=manager,
        capability_detector=lambda: capability,
    ).run(install_request)

    evidence = {}
    if result.evidence_path:
        evidence = json.loads(Path(result.evidence_path).read_text(encoding="utf-8"))
    report = {
        "build": build.to_dict(),
        "run_id": result.run_id,
        "status": result.status.value,
        "exit_reason": result.exit_reason,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "launcher_return_code": result.launcher_return_code,
        "installer_started_at": evidence.get("installer_started_at"),
        "installer_finished_at": evidence.get("installer_finished_at"),
        "installer_exit_code": evidence.get("installer_exit_code"),
        "installed_marker_found": evidence.get("installed_marker_found"),
        "installed_payload_found": evidence.get("installed_payload_found"),
        "reboot_required": evidence.get("reboot_required"),
        "installed_executable_found": evidence.get("installed_executable_found"),
        "first_launch_verified": evidence.get("first_launch_verified"),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    print("PHASE2B_EXTERNAL_REPORT=" + json.dumps(report, sort_keys=True))

    assert result.status == RunStatus.PASSED, report
    assert evidence["outcome"] == "passed"
    assert evidence["installer_exit_code"] == 0
    assert evidence["installed_marker_found"] is True
    assert evidence["installed_payload_found"] is True
    assert evidence["reboot_required"] is False
    assert evidence["installed_executable_found"] is True
    assert evidence["installed_executable_sha256"] == build.gui_sha256
    assert evidence["first_launch_verified"] is False
