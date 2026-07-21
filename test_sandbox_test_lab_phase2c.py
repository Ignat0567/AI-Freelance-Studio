from __future__ import annotations

import base64
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

import sandbox_test_lab.fixture_launch as launch_module
import sandbox_test_lab.fixture_builder as builder_module
import sandbox_test_lab.launch_evidence as evidence_module
import sandbox_test_lab.launch_runner as runner_module
import test_sandbox_test_lab_phase2c_external as external_fixture
from sandbox_test_lab.cli import ExitCode, main as cli_main
from sandbox_test_lab.fixture_builder import (
    FIXTURE_GUI_EXECUTABLE_NAME,
    TRUSTED_FIXTURE_ARTIFACT_SHA256,
    TRUSTED_FIXTURE_GUI_SHA256,
    fixture_source_root,
)
from sandbox_test_lab.fixture_launch import (
    CONTROLLED_LAUNCH_PROFILE,
    INSTALL_LAUNCH_PROTOCOL,
    LAUNCH_EXTERNAL_OPT_IN,
    LAUNCH_PROFILE,
    FixtureLaunchRequest,
    FixtureLaunchWorkspaceManager,
    build_launch_guest_request,
    launch_external_opt_in_enabled,
)
from sandbox_test_lab.installer import ApplicationTestRequest, InstallationRecipe, InstallerKind
from sandbox_test_lab.launch_evidence import (
    EXPECTED_LAUNCH_EVIDENCE_FILES,
    LaunchEvidenceError,
    validate_launch_evidence_directory,
)
from sandbox_test_lab.launch_runner import InstallLaunchSandboxRunner
from sandbox_test_lab.models import RunStatus, SandboxCapability
from sandbox_test_lab.workspace import WorkspaceError, atomic_write_json, sha256_file


pytestmark = pytest.mark.unit


class FakeProcess:
    def __init__(self):
        self.pid = 4321
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode or 0


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _capability() -> SandboxCapability:
    return SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631,
        virtualization_available=True, sandbox_feature_state="enabled", executable_found=True,
        available=True, executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )


def _request(tmp_path: Path, monkeypatch, *, timeout=120.0) -> FixtureLaunchRequest:
    artifact = tmp_path / "aifs-sandbox-fixture-v1.exe"
    artifact.write_bytes(b"controlled install launch fixture")
    digest = sha256_file(artifact)
    monkeypatch.setattr(launch_module, "fixture_output_path", lambda: artifact)
    monkeypatch.setattr(launch_module, "verify_fixture_provenance", lambda _artifact, _digest: None)
    monkeypatch.setattr(launch_module, "ensure_no_active_windows_sandbox_session", lambda: None)
    recipe = InstallationRecipe(
        InstallerKind.NSIS_EXE, artifact.name, digest, install_timeout_seconds=30,
        launch_timeout_seconds=30, expected_executable=FIXTURE_GUI_EXECUTABLE_NAME,
        expected_process_name=FIXTURE_GUI_EXECUTABLE_NAME,
        success_requirements=(
            "artifact_hash_verified", "installer_exit_zero", "expected_executable_found",
            "process_started", "first_launch_verified",
        ),
    )
    return FixtureLaunchRequest(ApplicationTestRequest(artifact, digest, recipe), CONTROLLED_LAUNCH_PROFILE, timeout)


def _status(run_id: str, digest: str, *, fallback=False) -> dict:
    transitions = [
        ("installed", "2026-01-01T00:00:03Z"),
        ("launching", "2026-01-01T00:00:04Z"),
        ("process_started", "2026-01-01T00:00:05Z"),
        ("window_detecting", "2026-01-01T00:00:06Z"),
        ("window_visible", "2026-01-01T00:00:07Z"),
        ("first_launch_verified", "2026-01-01T00:00:10Z"),
        ("cleanup", "2026-01-01T00:00:11Z"),
        ("completed", "2026-01-01T00:00:12Z"),
    ]
    return {
        "schema_version": 2, "protocol": INSTALL_LAUNCH_PROTOCOL, "run_id": run_id,
        "launch_profile": CONTROLLED_LAUNCH_PROFILE, "status": "passed", "phase": "completed", "outcome": "passed",
        "started_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:13Z", "completed_at": "2026-01-01T00:00:13Z",
        "artifact": "artifact.exe", "installer_kind": "nsis_exe", "installer_started_at": "2026-01-01T00:00:01Z",
        "installer_finished_at": "2026-01-01T00:00:02Z", "installer_exit_code": 0, "install_duration_seconds": 1.0,
        "reboot_required": False, "network_disabled": True, "artifact_sha256_host": digest, "artifact_sha256_guest": digest,
        "hash_verified": True, "installation_passed": True, "installed_marker_verified": True,
        "installed_payload_verified": True, "installed_executable_found": True,
        "installed_executable_sha256": TRUSTED_FIXTURE_GUI_SHA256, "installed_executable_hash_verified": True,
        "launch_started_at": "2026-01-01T00:00:04Z", "process_started_at": "2026-01-01T00:00:05Z",
        "window_detection_started_at": "2026-01-01T00:00:06Z", "window_detected_at": "2026-01-01T00:00:07Z",
        "stable_started_at": "2026-01-01T00:00:07Z", "stable_verified_at": "2026-01-01T00:00:10Z",
        "cleanup_started_at": "2026-01-01T00:00:11Z", "cleanup_finished_at": "2026-01-01T00:00:12Z",
        "process_started": True, "owned_process_id": 4242, "process_image_name": FIXTURE_GUI_EXECUTABLE_NAME,
        "process_image_verified": True, "window_handle": 123456, "window_handle_present": True,
        "window_title": "AIFS Sandbox Fixture", "window_title_verified": True, "window_owned_by_process": True,
        "window_visible": True, "stable_window_duration_seconds": 3.0, "first_launch_verified": True,
        "graceful_close_succeeded": not fallback, "forced_owned_process_cleanup": fallback, "cleanup_process_exited": True,
        "phase_transitions": [{"phase": phase, "at": at} for phase, at in transitions], "errors": [],
        "warnings": ["owned_gui_fallback_kill_used"] if fallback else [],
    }


def _write_evidence(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / "status.json", payload)
    atomic_write_json(directory / "launch-evidence.json", payload)
    atomic_write_json(directory / "completion.json", {
        "schema_version": 2, "protocol": INSTALL_LAUNCH_PROTOCOL, "run_id": payload["run_id"], "completed": True,
        "status": payload["status"], "outcome": payload["outcome"], "phase": "completed", "completed_at": payload["completed_at"],
    })
    completed_transition_at = payload["phase_transitions"][-1]["at"]
    atomic_write_json(directory / "heartbeat.json", {
        "schema_version": 2, "protocol": INSTALL_LAUNCH_PROTOCOL, "run_id": payload["run_id"],
        "phase": "completed", "updated_at": completed_transition_at,
    })
    atomic_write_json(directory / "guest-system.json", {
        "os_version": "Windows", "architecture": "True", "powershell_version": "5.1",
    })
    result = "passed" if payload["status"] == "passed" else "failed"
    (directory / "lifecycle.log").write_text(
        "2026-01-01T00:00:00Z controlled_install_launch_started\n"
        f"2026-01-01T00:00:13Z controlled_install_launch_{result}\n", encoding="utf-8",
    )


def test_launch_profile_and_guest_request_are_exact_and_host_bound(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = FixtureLaunchWorkspaceManager(tmp_path / "runtime")
    paths, artifact, digest = manager.create(request)
    guest = build_launch_guest_request(request, artifact, digest)
    assert guest["launch_profile_name"] == CONTROLLED_LAUNCH_PROFILE
    assert guest["launch_profile"] == LAUNCH_PROFILE
    assert set(guest["launch_profile"]) == {
        "logical_executable_name", "expected_installed_exe_sha256", "process_image_name", "exact_window_title",
        "launch_timeout_seconds", "minimum_stable_duration_seconds", "cleanup_timeout_seconds",
    }
    serialized = json.dumps(guest)
    for forbidden in ("command", "arguments", "working_directory", "environment", str(request.source_artifact)):
        assert forbidden not in serialized
    assert paths.evidence_directory.is_dir()


def test_wrong_profile_and_caller_controlled_recipe_identity_are_rejected(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="profile_required"):
        FixtureLaunchRequest(request.application_request, "arbitrary", 120)
    recipe = request.application_request.installation_recipe
    changed = InstallationRecipe(
        recipe.installer_kind, recipe.artifact_name, recipe.artifact_sha256, install_timeout_seconds=30,
        launch_timeout_seconds=30, expected_executable="Other.exe", expected_process_name="Other.exe",
        success_requirements=recipe.success_requirements,
    )
    with pytest.raises(ValueError, match="identity is fixed"):
        FixtureLaunchRequest(ApplicationTestRequest(request.source_artifact, request.expected_sha256, changed), CONTROLLED_LAUNCH_PROFILE, 120)


def test_two_stage_sources_are_fixed_gui_and_installer_only():
    root = fixture_source_root()
    gui = (root / "aifs_sandbox_fixture_gui.nsi").read_text(encoding="utf-8")
    installer = (root / "aifs_sandbox_fixture.nsi").read_text(encoding="utf-8")
    assert 'Caption "AIFS Sandbox Fixture"' in gui
    assert 'OutFile "${GUI_OUTPUT_FILE}"' in gui
    assert 'File "/oname=AIFS Sandbox Fixture.exe"' in installer
    assert "RequestExecutionLevel user" in gui and "RequestExecutionLevel user" in installer
    assert TRUSTED_FIXTURE_ARTIFACT_SHA256 != "179773764ae8843d27dbde78b9162a94fc9c924f30bb3e9d2f3a02a0722aa79f"


def test_builder_runs_pinned_gui_then_installer_with_normalized_gui_timestamp(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    source_files = {
        "aifs_sandbox_fixture_gui.nsi": b"gui source",
        "aifs_sandbox_fixture.nsi": b"installer source",
        "fixture-manifest.json": b"marker",
        "payload.txt": b"payload",
    }
    for name, content in source_files.items():
        (source / name).write_bytes(content)
    compiler = tmp_path / "makensis.exe"
    compiler.write_bytes(b"compiler")
    output = source / "build" / builder_module.FIXTURE_EXECUTABLE_NAME
    gui_output = source / "build" / builder_module.FIXTURE_GUI_EXECUTABLE_NAME
    provenance = source / "build" / builder_module.FIXTURE_PROVENANCE_NAME
    gui_bytes = b"reproducible gui"
    installer_bytes = b"reproducible installer"
    calls = []

    def fake_run(argv, *, cwd):
        calls.append(tuple(argv))
        if "/VERSION" in argv:
            return type("Result", (), {"returncode": 0, "stdout": "v3.04\n", "stderr": ""})()
        if "aifs_sandbox_fixture_gui.nsi" in argv[-1]:
            gui_output.write_bytes(gui_bytes)
        else:
            assert int(gui_output.stat().st_mtime) == 946684800
            output.write_bytes(installer_bytes)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(builder_module, "fixture_source_root", lambda: source)
    monkeypatch.setattr(builder_module, "fixture_output_path", lambda: output)
    monkeypatch.setattr(builder_module, "fixture_gui_output_path", lambda: gui_output)
    monkeypatch.setattr(builder_module, "fixture_provenance_path", lambda: provenance)
    monkeypatch.setattr(builder_module, "trusted_makensis_path", lambda: compiler.resolve())
    monkeypatch.setattr(builder_module, "TRUSTED_MAKENSIS_SHA256", sha256_file(compiler))
    monkeypatch.setattr(builder_module, "TRUSTED_FIXTURE_SOURCE_SHA256", {
        name: sha256_file(source / name) for name in source_files
    })
    monkeypatch.setattr(builder_module, "TRUSTED_FIXTURE_GUI_SHA256", hashlib.sha256(gui_bytes).hexdigest())
    monkeypatch.setattr(builder_module, "TRUSTED_FIXTURE_ARTIFACT_SHA256", hashlib.sha256(installer_bytes).hexdigest())
    monkeypatch.setattr(builder_module, "_run", fake_run)

    result = builder_module.build_fixture(compiler=compiler)
    assert result.gui_sha256 == sha256_file(gui_output)
    assert result.sha256 == sha256_file(output)
    assert "aifs_sandbox_fixture_gui.nsi" in calls[1][-1]
    assert "aifs_sandbox_fixture.nsi" in calls[2][-1]


def test_builder_rejects_reparse_build_directory_before_deleting_outputs(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    build = source / "build"
    try:
        build.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")
    protected = target / builder_module.FIXTURE_EXECUTABLE_NAME
    protected.write_bytes(b"must remain")
    with pytest.raises(ValueError, match="non-reparse"):
        builder_module._prepare_build_directory(source, build / builder_module.FIXTURE_EXECUTABLE_NAME)
    assert protected.read_bytes() == b"must remain"


def test_guest_uses_direct_owned_process_window_and_cleanup_only():
    script = (fixture_source_root().parents[1] / "guest" / "install_launch_fixture.ps1").read_text(encoding="utf-8")
    for required in (
        '$GuiStartInfo.FileName = $GuiPath', '$GuiStartInfo.UseShellExecute = $false',
        "FindOwnedVisibleWindow", "IsOwnedVisibleWindow", "$GuiProcess.CloseMainWindow()", "$GuiProcess.Kill()",
        'Set-Phase "installed"', 'Set-Phase "first_launch_verified"',
    ):
        assert required in script
    for forbidden in ("Invoke-Expression", "Start-Process", "taskkill", "Get-Process", "shell=True"):
        assert forbidden.lower() not in script.lower()
    assert "EnvironmentVariables" not in script
    assert "phase_transitions = [object[]]$Transitions" in script
    assert "Assert-NonReparsePathChain -Path $ActualInstallRoot" in script
    assert script.count("[AifsSandboxWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $ProcessId, $ExactTitle)") >= 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 is Windows-only")
def test_windows_powershell_51_serializes_evidence_collections_as_json_arrays():
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = r'''
$Transitions = New-Object System.Collections.Generic.List[object]
$Transitions.Add([ordered]@{ phase = "installed"; at = "2026-01-01T00:00:00Z" })
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
$Warnings.Add("one")
$Many = New-Object System.Collections.Generic.List[string]
$Many.Add("one")
$Many.Add("two")
[ordered]@{
    phase_transitions = [object[]]$Transitions
    errors = @($Errors)
    warnings = @($Warnings)
    multiple = @($Many)
} | ConvertTo-Json -Depth 10 -Compress
'''
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(result.stdout)
    assert payload["phase_transitions"] == [{"phase": "installed", "at": "2026-01-01T00:00:00Z"}]
    assert payload["errors"] == []
    assert payload["warnings"] == ["one"]
    assert payload["multiple"] == ["one", "two"]


def test_valid_exact_launch_evidence_and_fallback_warning(tmp_path):
    run_id = "1f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    digest = "a" * 64
    _write_evidence(tmp_path, _status(run_id, digest, fallback=True))
    validated = validate_launch_evidence_directory(tmp_path, run_id, digest)
    assert validated.first_launch_verified is True
    assert validated.forced_owned_process_cleanup is True
    assert set(path.name for path in tmp_path.iterdir()) == EXPECTED_LAUNCH_EVIDENCE_FILES


def test_passed_evidence_rejects_contradictory_log_cleanup_transitions_and_heartbeat(tmp_path):
    run_id = "0f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    digest = "9" * 64

    payload = _status(run_id, digest)
    _write_evidence(tmp_path, payload)
    (tmp_path / "lifecycle.log").write_text(
        "2026-01-01T00:00:00Z controlled_install_launch_started\n"
        "2026-01-01T00:00:13Z controlled_install_launch_failed\n",
        encoding="utf-8",
    )
    with pytest.raises(LaunchEvidenceError, match="contradicts terminal status"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)

    payload = _status(run_id, digest, fallback=True)
    payload["graceful_close_succeeded"] = True
    _write_evidence(tmp_path, payload)
    with pytest.raises(LaunchEvidenceError, match="mutually exclusive"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)

    payload = _status(run_id, digest)
    payload["phase_transitions"][5]["at"] = "2026-01-01T00:00:09Z"
    _write_evidence(tmp_path, payload)
    with pytest.raises(LaunchEvidenceError, match="contradict lifecycle"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)

    payload = _status(run_id, digest)
    _write_evidence(tmp_path, payload)
    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    heartbeat["updated_at"] = payload["completed_at"]
    atomic_write_json(tmp_path / "heartbeat.json", heartbeat)
    with pytest.raises(LaunchEvidenceError, match="heartbeat timestamp"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"installation_passed": False}, "installation"),
        ({"installed_executable_sha256": "b" * 64}, "hash"),
        ({"owned_process_id": 0}, "PID"),
        ({"window_handle": 0}, "window handle"),
        ({"window_owned_by_process": False}, "association"),
        ({"window_title": "Wrong"}, "exact title"),
        ({"stable_window_duration_seconds": 2.9}, "stable interval"),
        ({"stable_verified_at": "2026-01-01T00:00:08Z"}, "contradict lifecycle"),
        ({"first_launch_verified": False}, "stability"),
        ({"cleanup_process_exited": False}, "cleanup"),
        ({"graceful_close_succeeded": False}, "cleanup"),
        ({"network_disabled": False}, "installation"),
        ({"reboot_required": True}, "installation"),
    ],
)
def test_passed_evidence_fails_closed_on_required_claims(tmp_path, updates, message):
    run_id = "2f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status(run_id, "c" * 64)
    payload.update(updates)
    _write_evidence(tmp_path, payload)
    with pytest.raises(LaunchEvidenceError, match=message):
        validate_launch_evidence_directory(tmp_path, run_id, "c" * 64)


def test_timestamp_and_phase_order_are_enforced(tmp_path):
    run_id = "3f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status(run_id, "d" * 64)
    payload["launch_started_at"] = "2025-12-31T23:59:59Z"
    _write_evidence(tmp_path, payload)
    with pytest.raises(LaunchEvidenceError, match="timestamps"):
        validate_launch_evidence_directory(tmp_path, run_id, "d" * 64)


def test_duplicate_keys_unknown_path_fields_and_path_values_are_rejected(tmp_path):
    run_id = "4f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    digest = "e" * 64
    _write_evidence(tmp_path, _status(run_id, digest))
    (tmp_path / "status.json").write_text('{"schema_version":2,"schema_version":2}', encoding="utf-8")
    with pytest.raises(LaunchEvidenceError, match="duplicate JSON keys"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)
    payload = _status(run_id, digest)
    payload["process_path"] = r"C:\secret\fixture.exe"
    _write_evidence(tmp_path, payload)
    with pytest.raises(LaunchEvidenceError, match="exact schema"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)


@pytest.mark.parametrize("name", ["unexpected.exe", "unexpected.ps1", "inventory.json"])
def test_unexpected_executable_script_or_inventory_evidence_is_rejected(tmp_path, name):
    run_id = "5f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    digest = "f" * 64
    _write_evidence(tmp_path, _status(run_id, digest))
    (tmp_path / name).write_bytes(b"unexpected")
    with pytest.raises(LaunchEvidenceError, match="unexpected file"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)


def test_reparse_and_oversized_evidence_are_rejected(tmp_path, monkeypatch):
    run_id = "6f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    digest = "1" * 64
    _write_evidence(tmp_path, _status(run_id, digest))
    monkeypatch.setattr(evidence_module, "_is_reparse", lambda path: path.name == "status.json")
    with pytest.raises(LaunchEvidenceError, match="regular non-reparse"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)
    monkeypatch.setattr(evidence_module, "_is_reparse", lambda path: False)
    (tmp_path / "lifecycle.log").write_bytes(b"x" * (evidence_module.MAX_LOG_BYTES + 1))
    with pytest.raises(LaunchEvidenceError, match="size limit"):
        validate_launch_evidence_directory(tmp_path, run_id, digest)


def test_launch_runner_passes_only_validated_complete_evidence(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = FixtureLaunchWorkspaceManager(tmp_path / "runtime")
    process = FakeProcess()

    def launch(argv):
        run_root = Path(argv[1]).parent
        guest = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        _write_evidence(run_root / "evidence", _status(guest["run_id"], guest["artifact_sha256_host"]))
        return process

    result = InstallLaunchSandboxRunner(workspace_manager=manager, capability_detector=_capability, launcher=launch).run(request)
    assert result.status == RunStatus.PASSED
    assert result.exit_reason == "install_launch_completed"
    assert Path(result.evidence_path).name == "validated-launch-evidence.json"


def test_phase2c_active_sandbox_session_blocks_before_launcher(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    monkeypatch.setattr(
        launch_module,
        "ensure_no_active_windows_sandbox_session",
        lambda: (_ for _ in ()).throw(WorkspaceError("active_windows_sandbox_session")),
    )
    launched = []
    result = InstallLaunchSandboxRunner(
        workspace_manager=FixtureLaunchWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_capability,
        launcher=lambda argv: launched.append(argv),
    ).run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.errors == ("active_windows_sandbox_session",)
    assert launched == []


def test_launch_runner_rechecks_deadline_after_evidence_validation(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = FixtureLaunchWorkspaceManager(tmp_path / "runtime")
    process = FakeProcess()
    clock = FakeClock()
    original_validate = runner_module.validate_launch_evidence_directory

    def launch(argv):
        run_root = Path(argv[1]).parent
        guest = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        _write_evidence(run_root / "evidence", _status(guest["run_id"], guest["artifact_sha256_host"]))
        return process

    def validate(*args):
        evidence = original_validate(*args)
        clock.value = request.timeout_seconds
        return evidence

    monkeypatch.setattr(runner_module, "validate_launch_evidence_directory", validate)
    result = InstallLaunchSandboxRunner(
        workspace_manager=manager,
        capability_detector=_capability,
        launcher=launch,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).run(request)
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "timeout"
    assert process.terminated is True


def test_cli_and_external_test_require_new_exact_dual_gate(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "fixture.exe"
    artifact.write_bytes(b"fixture")
    digest = sha256_file(artifact)
    monkeypatch.delenv(LAUNCH_EXTERNAL_OPT_IN, raising=False)
    monkeypatch.setenv("FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_NSIS_INSTALL_EXTERNAL", "1")
    code = cli_main([
        "run-install-launch", "--artifact", str(artifact), "--sha256", digest,
        "--launch-profile", CONTROLLED_LAUNCH_PROFILE, "--external", "--json",
    ])
    assert code == ExitCode.INVALID_INPUT
    assert "GUI external opt-in" in capsys.readouterr().err
    assert launch_external_opt_in_enabled(True) is False
    assert external_fixture.external_opt_in_enabled("external") is False
    monkeypatch.setenv(LAUNCH_EXTERNAL_OPT_IN, "1")
    assert launch_external_opt_in_enabled(False) is False
    assert launch_external_opt_in_enabled(True) is True
    assert external_fixture.external_opt_in_enabled("") is False
    assert external_fixture.external_opt_in_enabled("external and not slow") is False
    assert external_fixture.external_opt_in_enabled("external") is True


def test_phase2c_offline_sources_do_not_build_or_launch_implicitly():
    package = fixture_source_root().parents[1]
    combined = "\n".join((package / name).read_text(encoding="utf-8") for name in (
        "fixture_launch.py", "launch_evidence.py", "launch_runner.py",
    ))
    assert "shell=True" not in combined
    assert "build_fixture(" not in combined
    assert "taskkill" not in combined.lower()
