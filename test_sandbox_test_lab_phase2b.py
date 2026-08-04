from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading

import pytest

import sandbox_test_lab.fixture_builder as builder_module
import sandbox_test_lab.fixture_installation as fixture_module
import sandbox_test_lab.installation_evidence as evidence_module
import test_sandbox_test_lab_phase2b_external as external_fixture
from sandbox_test_lab.cli import ExitCode, main as cli_main
from sandbox_test_lab.fixture_builder import fixture_source_root
from sandbox_test_lab.fixture_installation import (
    CONTROLLED_FIXTURE_PROFILE,
    FIXTURE_INSTALL_ROOT,
    INSTALL_EXECUTION_PROTOCOL,
    FixtureInstallationRequest,
    FixtureInstallationWorkspaceManager,
    build_installation_guest_request,
    installation_external_opt_in_enabled,
)
from sandbox_test_lab.installation_evidence import (
    EXPECTED_INSTALLATION_EVIDENCE_FILES,
    InstallationEvidenceError,
    validate_installation_evidence_directory,
)
from sandbox_test_lab.installation_runner import InstallationSandboxRunner
from sandbox_test_lab.installer import ApplicationTestRequest, InstallationRecipe, InstallerKind, plan_installation
from sandbox_test_lab.models import RunStatus, SandboxCapability, SandboxRunRequest
from sandbox_test_lab.workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file


pytestmark = pytest.mark.unit


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = 4321
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
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


class UncooperativeProcess(FakeProcess):
    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("WindowsSandbox.exe", timeout)


def _available_capability() -> SandboxCapability:
    return SandboxCapability(
        supported_os=True,
        windows_edition="Professional",
        windows_build=22631,
        virtualization_available=True,
        sandbox_feature_state="enabled",
        executable_found=True,
        available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe",
        powershell_found=True,
    )


def _controlled_request(tmp_path: Path, monkeypatch, *, host_timeout=60.0, install_timeout=30) -> FixtureInstallationRequest:
    artifact = tmp_path / "aifs-sandbox-fixture-v1.exe"
    artifact.write_bytes(b"controlled NSIS fixture")
    digest = sha256_file(artifact)
    monkeypatch.setattr(fixture_module, "fixture_output_path", lambda: artifact)
    monkeypatch.setattr(fixture_module, "verify_fixture_provenance", lambda _artifact, _digest: None)
    monkeypatch.setattr(fixture_module, "ensure_no_active_windows_sandbox_session", lambda: None)
    recipe = InstallationRecipe(
        installer_kind=InstallerKind.NSIS_EXE,
        artifact_name=artifact.name,
        artifact_sha256=digest,
        install_timeout_seconds=install_timeout,
        launch_timeout_seconds=1,
        success_requirements=("artifact_hash_verified", "installer_exit_zero"),
    )
    application = ApplicationTestRequest(artifact, digest, recipe, metadata={"ticket": "host-only"})
    return FixtureInstallationRequest(application, CONTROLLED_FIXTURE_PROFILE, host_timeout)


def _status_payload(run_id: str, digest: str, *, status="passed", outcome="passed", exit_code=0, marker=True, payload=True):
    passed = status == "passed"
    return {
        "schema_version": 2,
        "protocol": INSTALL_EXECUTION_PROTOCOL,
        "run_id": run_id,
        "status": status,
        "phase": "completed",
        "outcome": outcome,
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:03Z",
        "completed_at": "2026-01-01T00:00:03Z",
        "artifact": "artifact.exe",
        "installer_kind": "nsis_exe",
        "installer_started_at": "2026-01-01T00:00:01Z",
        "installer_finished_at": "2026-01-01T00:00:02Z",
        "installer_exit_code": exit_code,
        "install_duration_seconds": 1.0,
        "reboot_required": outcome == "reboot_required",
        "artifact_sha256_host": digest,
        "artifact_sha256_guest": digest,
        "hash_verified": True,
        "expected_install_root": FIXTURE_INSTALL_ROOT,
        "installed_marker_found": marker,
        "installed_payload_found": payload,
        "installed_executable_found": True,
        "installed_executable_sha256": builder_module.TRUSTED_FIXTURE_GUI_SHA256,
        "first_launch_verified": False,
        "errors": [] if passed else ["controlled_installation_failed"],
        "warnings": [],
    }


def _write_evidence(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / "status.json", payload)
    atomic_write_json(directory / "installation-evidence.json", payload)
    atomic_write_json(
        directory / "completion.json",
        {
            "schema_version": 2,
            "protocol": INSTALL_EXECUTION_PROTOCOL,
            "run_id": payload["run_id"],
            "completed": True,
            "status": payload["status"],
            "outcome": payload["outcome"],
            "phase": "completed",
            "completed_at": payload["completed_at"],
        },
    )
    atomic_write_json(
        directory / "heartbeat.json",
        {
            "schema_version": 2,
            "protocol": INSTALL_EXECUTION_PROTOCOL,
            "run_id": payload["run_id"],
            "phase": "completed",
            "updated_at": payload["completed_at"],
        },
    )
    atomic_write_json(
        directory / "guest-system.json",
        {"os_version": "Windows", "architecture": "True", "powershell_version": "5.1"},
    )
    (directory / "installer.log").write_text(
        "2026-01-01T00:00:00.0000000Z controlled_fixture_bootstrap_started\n",
        encoding="utf-8",
    )


def test_valid_controlled_fixture_request_is_host_bound_and_metadata_free(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    manager = FixtureInstallationWorkspaceManager(tmp_path / "runtime")
    paths, artifact, digest = manager.create(request)
    guest = build_installation_guest_request(request, artifact, digest)
    assert guest["schema_version"] == 2
    assert guest["protocol"] == INSTALL_EXECUTION_PROTOCOL
    assert guest["controlled_fixture_profile"] == CONTROLLED_FIXTURE_PROFILE
    assert guest["artifact_name"] == "artifact.exe"
    assert guest["installation_recipe"]["network_policy"] == "disabled"
    assert guest["installation_recipe"]["reboot_policy"] == "forbid"
    serialized = json.dumps(guest)
    assert "host-only" not in serialized
    assert str(request.source_artifact) not in serialized
    assert "command" not in serialized
    assert "argument" not in serialized
    assert paths.evidence_directory.is_dir()


def test_arbitrary_nsis_path_and_profile_are_rejected(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="controlled_fixture_required"):
        FixtureInstallationRequest(request.application_request, "arbitrary_nsis", 60)
    other = tmp_path / "other.exe"
    other.write_bytes(b"arbitrary")
    digest = sha256_file(other)
    recipe = InstallationRecipe(
        InstallerKind.NSIS_EXE,
        other.name,
        digest,
        install_timeout_seconds=30,
        launch_timeout_seconds=1,
        success_requirements=("artifact_hash_verified", "installer_exit_zero"),
    )
    application = ApplicationTestRequest(other, digest, recipe)
    with pytest.raises(ValueError, match="controlled_fixture_required"):
        FixtureInstallationRequest(application, CONTROLLED_FIXTURE_PROFILE, 60)


def test_fixture_provenance_detects_canonical_artifact_replacement(tmp_path, monkeypatch):
    artifact = tmp_path / builder_module.FIXTURE_EXECUTABLE_NAME
    artifact.write_bytes(b"controlled build")
    digest = sha256_file(artifact)
    gui_artifact = tmp_path / builder_module.FIXTURE_GUI_EXECUTABLE_NAME
    gui_artifact.write_bytes(b"controlled GUI build")
    gui_digest = sha256_file(gui_artifact)
    provenance = tmp_path / builder_module.FIXTURE_PROVENANCE_NAME
    atomic_write_json(
        provenance,
        {
            "schema_version": 2,
            "profile": CONTROLLED_FIXTURE_PROFILE,
            "compiler_version": "v3.04",
            "compiler_sha256": builder_module.TRUSTED_MAKENSIS_SHA256,
            "source_sha256": dict(builder_module.TRUSTED_FIXTURE_SOURCE_SHA256),
            "gui_artifact_name": builder_module.FIXTURE_GUI_EXECUTABLE_NAME,
            "gui_artifact_sha256": gui_digest,
            "artifact_name": builder_module.FIXTURE_EXECUTABLE_NAME,
            "artifact_sha256": digest,
        },
    )
    monkeypatch.setattr(builder_module, "fixture_output_path", lambda: artifact)
    monkeypatch.setattr(builder_module, "fixture_provenance_path", lambda: provenance)
    monkeypatch.setattr(builder_module, "TRUSTED_FIXTURE_ARTIFACT_SHA256", digest)
    monkeypatch.setattr(builder_module, "TRUSTED_FIXTURE_GUI_SHA256", gui_digest)
    with pytest.raises(ValueError, match="pinned artifact"):
        builder_module.verify_fixture_provenance(artifact, "179773764ae8843d27dbde78b9162a94fc9c924f30bb3e9d2f3a02a0722aa79f")
    builder_module.verify_fixture_provenance(artifact, digest)
    artifact.write_bytes(b"arbitrary replacement")
    replacement_hash = sha256_file(artifact)
    replacement_provenance = json.loads(provenance.read_text(encoding="utf-8"))
    replacement_provenance["artifact_sha256"] = replacement_hash
    atomic_write_json(provenance, replacement_provenance)
    with pytest.raises(ValueError, match="pinned artifact"):
        builder_module.verify_fixture_provenance(artifact, replacement_hash)


def test_guest_executor_has_exact_nsis_argument_and_no_dynamic_execution():
    script = (fixture_source_root().parents[1] / "guest" / "install_fixture.ps1").read_text(encoding="utf-8")
    assert '$StartInfo.Arguments = "/S"' in script
    assert "$StartInfo.FileName = $ArtifactPath" in script
    assert "$StartInfo.UseShellExecute = $false" in script
    for forbidden in ("Invoke-Expression", "cmd /c", "Start-Job", "taskkill", "ShellExecute"):
        if forbidden == "ShellExecute":
            continue
        assert forbidden.lower() not in script.lower()


def test_fixture_source_is_user_scope_inert_and_contains_no_launch():
    source = (fixture_source_root() / "aifs_sandbox_fixture.nsi").read_text(encoding="utf-8")
    assert "RequestExecutionLevel user" in source
    assert '$LOCALAPPDATA\\Programs\\AIFS Sandbox Fixture' in source
    assert 'File "/oname=fixture-manifest.json"' in source
    assert 'File "/oname=payload.txt"' in source
    assert 'File "/oname=AIFS Sandbox Fixture.exe"' in source
    for forbidden in ("Exec ", "ExecWait", "ShellExec", "WriteReg", "CreateShortCut", "WriteUninstaller", "Reboot"):
        assert forbidden.lower() not in source.lower()


def test_valid_installation_evidence_requires_marker_payload_and_no_launch(tmp_path):
    digest = "a" * 64
    run_id = "1f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest)
    _write_evidence(tmp_path, payload)
    validated = validate_installation_evidence_directory(tmp_path, run_id, digest)
    assert validated.status == "passed"
    assert validated.installed_marker_found is True
    assert validated.installed_payload_found is True
    assert validated.raw["first_launch_verified"] is False
    assert validated.raw["installed_executable_found"] is True
    assert set(path.name for path in tmp_path.iterdir()) == EXPECTED_INSTALLATION_EVIDENCE_FILES


@pytest.mark.parametrize(
    "updates",
    [
        {"installed_marker_found": False},
        {"installed_payload_found": False},
        {"installer_exit_code": 7},
        {"installer_started_at": None, "installer_finished_at": None, "install_duration_seconds": None},
    ],
)
def test_passed_evidence_requires_successful_execution_marker_and_payload(tmp_path, updates):
    digest = "b" * 64
    run_id = "2f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest)
    payload.update(updates)
    _write_evidence(tmp_path, payload)
    with pytest.raises(InstallationEvidenceError):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def test_hash_mismatch_cannot_pass(tmp_path):
    run_id = "3f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, "c" * 64)
    _write_evidence(tmp_path, payload)
    with pytest.raises(InstallationEvidenceError, match="matching artifact hashes"):
        validate_installation_evidence_directory(tmp_path, run_id, "d" * 64)


def test_nonzero_exit_failed_evidence_is_valid_but_not_passed(tmp_path):
    digest = "d" * 64
    run_id = "4f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest, status="failed", outcome="failed", exit_code=7, marker=False, payload=False)
    _write_evidence(tmp_path, payload)
    validated = validate_installation_evidence_directory(tmp_path, run_id, digest)
    assert validated.status == "failed"
    assert validated.installer_exit_code == 7


def test_reboot_required_is_failed_and_consistent(tmp_path):
    digest = "e" * 64
    run_id = "5f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(
        run_id, digest, status="failed", outcome="reboot_required", exit_code=3010, marker=False, payload=False
    )
    _write_evidence(tmp_path, payload)
    validated = validate_installation_evidence_directory(tmp_path, run_id, digest)
    assert validated.outcome == "reboot_required"
    assert validated.reboot_required is True


def test_timed_out_evidence_is_never_passed(tmp_path):
    digest = "f" * 64
    run_id = "6f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest, status="failed", outcome="timed_out", exit_code=None, marker=False, payload=False)
    _write_evidence(tmp_path, payload)
    validated = validate_installation_evidence_directory(tmp_path, run_id, digest)
    assert validated.status == "failed"
    assert validated.outcome == "timed_out"


@pytest.mark.parametrize(
    ("file_name", "mutation", "message"),
    [
        ("status.json", lambda value: value.update(schema_version=3), "schema_version"),
        ("status.json", lambda value: value.update(run_id="wrong"), "run_id"),
        ("status.json", lambda value: value.update(phase="installing"), "terminal"),
        ("status.json", lambda value: value.update(expected_install_root=r"C:\escape"), "install root"),
        ("status.json", lambda value: value.update(installed_executable_found=False), "installed executable"),
        ("status.json", lambda value: value.update(first_launch_verified=True), "first launch"),
    ],
)
def test_malformed_v2_identity_phase_paths_and_launch_are_rejected(tmp_path, file_name, mutation, message):
    digest = "1" * 64
    run_id = "7f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest)
    mutation(payload)
    _write_evidence(tmp_path, payload)
    with pytest.raises(InstallationEvidenceError, match=message):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def test_duplicate_json_keys_are_rejected(tmp_path):
    digest = "2" * 64
    run_id = "8f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    payload = _status_payload(run_id, digest)
    _write_evidence(tmp_path, payload)
    (tmp_path / "status.json").write_text('{"schema_version":2,"schema_version":2}', encoding="utf-8")
    with pytest.raises(InstallationEvidenceError, match="duplicate JSON keys"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


@pytest.mark.parametrize("unexpected_name", ["unexpected.exe", "unexpected.ps1"])
def test_missing_or_unexpected_evidence_files_fail_closed(tmp_path, unexpected_name):
    digest = "3" * 64
    run_id = "9f5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    _write_evidence(tmp_path, _status_payload(run_id, digest))
    (tmp_path / "installation-evidence.json").unlink()
    with pytest.raises(InstallationEvidenceError, match="missing or unexpected"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)
    atomic_write_json(tmp_path / "installation-evidence.json", _status_payload(run_id, digest))
    (tmp_path / unexpected_name).write_bytes(b"unexpected")
    with pytest.raises(InstallationEvidenceError, match="missing or unexpected"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def test_oversized_installer_log_is_rejected(tmp_path):
    digest = "4" * 64
    run_id = "af5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    _write_evidence(tmp_path, _status_payload(run_id, digest))
    (tmp_path / "installer.log").write_bytes(b"x" * (evidence_module.MAX_INSTALLER_LOG_BYTES + 1))
    with pytest.raises(InstallationEvidenceError, match="size limits"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def test_symlink_evidence_is_rejected_when_supported(tmp_path):
    digest = "5" * 64
    run_id = "bf5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    _write_evidence(tmp_path, _status_payload(run_id, digest))
    status = tmp_path / "status.json"
    target = tmp_path / "target.json"
    status.replace(target)
    try:
        status.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(InstallationEvidenceError, match="regular non-reparse"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def test_reparse_evidence_is_rejected(tmp_path, monkeypatch):
    digest = "6" * 64
    run_id = "cf5b6d7a-5be7-49aa-8ca6-bde2ec618af2"
    _write_evidence(tmp_path, _status_payload(run_id, digest))
    monkeypatch.setattr(evidence_module, "_is_reparse", lambda path: path.name == "status.json")
    with pytest.raises(InstallationEvidenceError, match="regular non-reparse"):
        validate_installation_evidence_directory(tmp_path, run_id, digest)


def _run_with_evidence(tmp_path, monkeypatch, payload_factory):
    request = _controlled_request(tmp_path, monkeypatch)
    manager = FixtureInstallationWorkspaceManager(tmp_path / "runtime")
    process = FakeProcess()

    def launch(argv):
        run_root = Path(argv[1]).parent
        guest_request = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        payload = payload_factory(guest_request["run_id"], guest_request["artifact_sha256_host"])
        _write_evidence(run_root / "evidence", payload)
        return process

    result = InstallationSandboxRunner(
        workspace_manager=manager,
        capability_detector=_available_capability,
        launcher=launch,
    ).run(request)
    return result, manager, process


def test_installation_runner_passes_only_complete_validated_installation(tmp_path, monkeypatch):
    result, manager, _ = _run_with_evidence(tmp_path, monkeypatch, _status_payload)
    assert result.status == RunStatus.PASSED
    assert result.exit_reason == "installation_completed"
    assert Path(result.evidence_path).parent == manager.paths_for(result.run_id).logs_directory
    assert Path(result.evidence_path).name == "validated-installation-evidence.json"


def test_installation_runner_nonzero_exit_is_failed(tmp_path, monkeypatch):
    result, _, process = _run_with_evidence(
        tmp_path,
        monkeypatch,
        lambda run_id, digest: _status_payload(run_id, digest, status="failed", outcome="failed", exit_code=5, marker=False, payload=False),
    )
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "installation_failed"
    assert process.terminated is True


def test_installation_runner_guest_timeout_is_terminal_timeout(tmp_path, monkeypatch):
    result, _, _ = _run_with_evidence(
        tmp_path,
        monkeypatch,
        lambda run_id, digest: _status_payload(run_id, digest, status="failed", outcome="timed_out", exit_code=None, marker=False, payload=False),
    )
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "installer_timed_out"


def test_installation_runner_reboot_required_is_failed(tmp_path, monkeypatch):
    result, _, _ = _run_with_evidence(
        tmp_path,
        monkeypatch,
        lambda run_id, digest: _status_payload(run_id, digest, status="failed", outcome="reboot_required", exit_code=1641, marker=False, payload=False),
    )
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "installer_reboot_required"


def test_installation_runner_host_timeout_stops_only_owned_broker(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    clock = FakeClock()
    process = FakeProcess()
    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=lambda _argv: process,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        poll_interval=10,
    ).run(request)
    assert result.status == RunStatus.TIMED_OUT
    assert process.terminated is True
    assert "does_not_prove_guest_installer_termination" in result.warnings[0]


def test_installation_runner_host_cancellation_is_terminal(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    cancellation = threading.Event()
    process = FakeProcess()

    def launch(_argv):
        cancellation.set()
        return process

    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=launch,
    ).run(request, cancellation=cancellation)
    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled"
    assert process.terminated is True


def test_phase2b_active_sandbox_session_blocks_before_launcher(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fixture_module,
        "ensure_no_active_windows_sandbox_session",
        lambda: (_ for _ in ()).throw(WorkspaceError("active_windows_sandbox_session")),
    )
    launched = []
    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=lambda argv: launched.append(argv),
    ).run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.errors == ("active_windows_sandbox_session",)
    assert launched == []


def test_uncooperative_owned_broker_does_not_break_cancellation_result(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    cancellation = threading.Event()
    process = UncooperativeProcess()

    def launch(_argv):
        cancellation.set()
        return process

    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=launch,
    ).run(request, cancellation=cancellation)
    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled"
    assert process.terminated is True
    assert process.killed is True


def test_installation_runner_invalid_evidence_stops_owned_broker(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    process = FakeProcess()

    def launch(argv):
        evidence = Path(argv[1]).parent / "evidence"
        (evidence / "unexpected.ps1").write_text("bad", encoding="utf-8")
        return process

    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=launch,
    ).run(request)
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "invalid_installation_evidence"
    assert process.terminated is True


def test_installation_runner_rejects_completion_after_host_deadline(tmp_path, monkeypatch):
    request = _controlled_request(tmp_path, monkeypatch)
    process = FakeProcess()
    clock = FakeClock()

    def launch(argv):
        run_root = Path(argv[1]).parent
        guest_request = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        _write_evidence(run_root / "evidence", _status_payload(guest_request["run_id"], guest_request["artifact_sha256_host"]))
        clock.value = request.timeout_seconds
        return process

    result = InstallationSandboxRunner(
        workspace_manager=FixtureInstallationWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=launch,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).run(request)
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "timeout"
    assert process.terminated is True


def test_phase1_request_stays_schema_v1(tmp_path):
    artifact = tmp_path / "phase1.bin"
    artifact.write_bytes(b"phase1")
    request = SandboxRunRequest(artifact)
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    payload = json.loads(manager.write_guest_request(request, paths, staged, digest).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "protocol" not in payload


def test_phase2a_plan_install_remains_dry_run(tmp_path):
    artifact = tmp_path / "plan.exe"
    artifact.write_bytes(b"plan")
    digest = sha256_file(artifact)
    recipe = InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest)
    plan = plan_installation(ApplicationTestRequest(artifact, digest, recipe))
    assert plan.dry_run is True
    assert plan.argument_tokens == ("/S",)


def test_run_install_cli_requires_dual_opt_in(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "fixture.exe"
    artifact.write_bytes(b"fixture")
    digest = sha256_file(artifact)
    monkeypatch.delenv(fixture_module.INSTALL_EXTERNAL_OPT_IN, raising=False)
    code = cli_main(
        [
            "run-install", "--artifact", str(artifact), "--sha256", digest,
            "--installer-kind", "nsis_exe", "--expected-fixture-profile", CONTROLLED_FIXTURE_PROFILE,
            "--external", "--json",
        ]
    )
    assert code == ExitCode.INVALID_INPUT
    assert "opt-in" in capsys.readouterr().err


def test_installation_external_opt_in_requires_environment_and_explicit_flag(monkeypatch):
    monkeypatch.delenv(fixture_module.INSTALL_EXTERNAL_OPT_IN, raising=False)
    assert installation_external_opt_in_enabled(False) is False
    assert installation_external_opt_in_enabled(True) is False
    monkeypatch.setenv(fixture_module.INSTALL_EXTERNAL_OPT_IN, "1")
    assert installation_external_opt_in_enabled(False) is False
    assert installation_external_opt_in_enabled(True) is True


def test_active_session_detection_is_fresh_read_only_and_not_pid_based(tmp_path, monkeypatch):
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"trusted test powershell")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(fixture_module.os, "name", "nt")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(fixture_module, "validate_source_artifact", lambda _path: powershell)
    monkeypatch.setattr(fixture_module.subprocess, "run", run)
    fixture_module.ensure_no_active_windows_sandbox_session()
    assert len(calls) == 1
    argv, kwargs = calls[0]
    query = argv[-1]
    assert "WindowsSandbox.exe" in query
    assert "WindowsSandboxClient.exe" in query
    assert "WindowsSandboxRemoteSession.exe" in query
    assert "WindowsSandboxServer.exe" in query
    assert "ProcessId" not in query
    assert "Stop-Process" not in query and "taskkill" not in query.lower()
    assert kwargs["shell"] is False

    monkeypatch.setattr(
        fixture_module.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 10})(),
    )
    with pytest.raises(WorkspaceError, match="active_windows_sandbox_session"):
        fixture_module.ensure_no_active_windows_sandbox_session()


def test_external_fixture_test_requires_environment_and_exact_marker(monkeypatch):
    monkeypatch.delenv(fixture_module.INSTALL_EXTERNAL_OPT_IN, raising=False)
    assert external_fixture.external_opt_in_enabled("external") is False
    monkeypatch.setenv(fixture_module.INSTALL_EXTERNAL_OPT_IN, "1")
    assert external_fixture.external_opt_in_enabled("") is False
    assert external_fixture.external_opt_in_enabled("not external") is False
    assert external_fixture.external_opt_in_enabled("external and not slow") is False
    assert external_fixture.external_opt_in_enabled("external") is True


def test_plain_offline_sources_do_not_auto_launch_external_boundaries():
    package = fixture_source_root().parents[1]
    runner_source = (package / "installation_runner.py").read_text(encoding="utf-8")
    builder_source = (package / "fixture_builder.py").read_text(encoding="utf-8")
    assert "shell=True" not in runner_source + builder_source
    assert "taskkill" not in runner_source.lower()
    assert "WindowsSandbox.exe" not in builder_source
