from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import xml.etree.ElementTree as ET

import pytest

from sandbox_test_lab import capability as capability_module
from sandbox_test_lab.capability import CommandResult, detect_sandbox_capability
from sandbox_test_lab.cli import ExitCode, build_parser, main as cli_main
from sandbox_test_lab.evidence import EvidenceError, validate_completion, validate_guest_evidence
from sandbox_test_lab.models import RunStatus, SandboxCapability, SandboxRunRequest, validate_transition
from sandbox_test_lab.runner import SandboxRunner, launch_sandbox, validate_sandbox_argv
import sandbox_test_lab.runner as runner_module
from sandbox_test_lab.workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file
from sandbox_test_lab.wsb_config import BOOTSTRAP_COMMAND, build_wsb_xml, write_wsb_config
import sandbox_test_lab.workspace as workspace_module
import test_sandbox_test_lab_external as external_smoke


pytestmark = pytest.mark.unit


def _query_payload(**updates):
    payload = {
        "edition": "Professional",
        "build": 22631,
        "feature_state": 1,
        "hypervisor_present": True,
    }
    payload.update(updates)
    return json.dumps(payload)


def _fake_windows_system(tmp_path, *, sandbox=True, powershell=True):
    windows = tmp_path / "Windows"
    system32 = windows / "System32"
    powershell_directory = system32 / "WindowsPowerShell" / "v1.0"
    powershell_directory.mkdir(parents=True)
    if sandbox:
        (system32 / "WindowsSandbox.exe").write_bytes(b"sandbox")
    if powershell:
        (powershell_directory / "powershell.exe").write_bytes(b"powershell")
    return {"SystemRoot": str(windows), "WINDIR": str(windows)}


def _available_capability(executable: str = r"C:\Windows\System32\WindowsSandbox.exe") -> SandboxCapability:
    return SandboxCapability(
        supported_os=True,
        windows_edition="Professional",
        windows_build=22631,
        virtualization_available=True,
        sandbox_feature_state="enabled",
        executable_found=True,
        executable_path=executable,
        powershell_found=True,
        available=True,
    )


def test_capability_non_windows_is_unavailable_without_subprocess():
    called = False

    def runner(_argv, _timeout):
        nonlocal called
        called = True
        raise AssertionError

    result = detect_sandbox_capability(system_name="Linux", command_runner=runner)
    assert result.available is False
    assert result.blockers == ("unsupported_os",)
    assert called is False


def test_capability_missing_windows_sandbox_executable(tmp_path):
    environment = _fake_windows_system(tmp_path, sandbox=False)
    result = detect_sandbox_capability(
        system_name="Windows",
        environ=environment,
        command_runner=lambda _argv, _timeout: CommandResult(0, _query_payload()),
    )
    assert result.available is False
    assert result.executable_found is False
    assert "sandbox_executable_missing" in result.blockers


def test_capability_subprocess_timeout_is_a_blocker(tmp_path):
    environment = _fake_windows_system(tmp_path)
    result = detect_sandbox_capability(
        system_name="Windows",
        environ=environment,
        command_runner=lambda _argv, _timeout: CommandResult(None, timed_out=True),
    )
    assert result.available is False
    assert "capability_query_timeout" in result.blockers


@pytest.mark.parametrize(
    ("updates", "blocker"),
    [
        ({"edition": "Core"}, "unsupported_windows_edition"),
        ({"build": 17763}, "windows_build_too_old"),
        ({"feature_state": 2}, "sandbox_feature_disabled"),
        ({"hypervisor_present": False}, "virtualization_unavailable"),
    ],
)
def test_capability_requires_multiple_independent_signals(tmp_path, updates, blocker):
    environment = _fake_windows_system(tmp_path)
    result = detect_sandbox_capability(
        system_name="Windows",
        environ=environment,
        command_runner=lambda _argv, _timeout: CommandResult(0, _query_payload(**updates)),
    )
    assert result.available is False
    assert blocker in result.blockers


def test_capability_available_when_all_checks_pass(tmp_path):
    environment = _fake_windows_system(tmp_path)
    commands = []
    result = detect_sandbox_capability(
        system_name="Windows",
        environ=environment,
        command_runner=lambda argv, _timeout: commands.append(argv) or CommandResult(0, _query_payload()),
    )
    assert result.available is True
    assert result.blockers == ()
    assert result.executable_path == str((Path(environment["SystemRoot"]) / "System32" / "WindowsSandbox.exe").resolve())
    assert commands[0][0] == str((Path(environment["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe").resolve())


def test_capability_never_uses_path_substituted_executables(tmp_path):
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "WindowsSandbox.exe").write_bytes(b"attacker")
    (attacker / "powershell.exe").write_bytes(b"attacker")
    environment = {"SystemRoot": str(tmp_path / "missing-windows"), "PATH": str(attacker)}
    called = False

    def runner(_argv, _timeout):
        nonlocal called
        called = True
        return CommandResult(0, _query_payload())

    result = detect_sandbox_capability(system_name="Windows", environ=environment, command_runner=runner)
    assert result.available is False
    assert result.executable_path is None
    assert result.powershell_found is False
    assert called is False


def _request(artifact: Path, **kwargs) -> SandboxRunRequest:
    return SandboxRunRequest(source_artifact=artifact, **kwargs)


def test_workspace_creation_copies_and_hashes_artifact(tmp_path):
    artifact = tmp_path / "sample.exe"
    artifact.write_bytes(b"sandbox artifact")
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    request = _request(artifact)
    paths, copied, digest = manager.create(request)
    assert paths.run_root.parent == tmp_path / "runtime" / "runs"
    assert copied.read_bytes() == artifact.read_bytes()
    assert digest == sha256_file(artifact) == sha256_file(copied)
    assert paths.input_directory != artifact.parent
    assert paths.evidence_directory.is_dir()
    assert paths.logs_directory.is_dir()


def test_workspace_expected_hash_match_and_mismatch(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"known")
    digest = sha256_file(artifact)
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    manager.create(_request(artifact, expected_sha256=digest))
    with pytest.raises(WorkspaceError, match="does not match"):
        manager.create(_request(artifact, expected_sha256="0" * 64))


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_workspace_rejects_invalid_artifact(tmp_path, kind):
    artifact = tmp_path / "artifact"
    if kind == "directory":
        artifact.mkdir()
    with pytest.raises(WorkspaceError):
        SandboxWorkspaceManager(tmp_path / "runtime").create(_request(artifact))


def test_workspace_rejects_symlink_source_when_supported(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(source)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available")
    with pytest.raises(WorkspaceError, match="symlink or reparse"):
        SandboxWorkspaceManager(tmp_path / "runtime").create(_request(link))


def test_workspace_rejects_duplicate_run_id(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"x")
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    request = _request(artifact)
    manager.create(request)
    with pytest.raises(WorkspaceError, match="already exists"):
        manager.create(request)


def _prepared_paths(tmp_path, directory_name="runtime"):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"content")
    manager = SandboxWorkspaceManager(tmp_path / directory_name)
    request = _request(artifact)
    paths, staged, digest = manager.create(request)
    manager.write_guest_request(request, paths, staged, digest)
    return manager, request, paths, staged, digest


def test_wsb_xml_has_secure_defaults_and_mount_permissions(tmp_path):
    _, _, paths, _, _ = _prepared_paths(tmp_path)
    root = ET.fromstring(build_wsb_xml(paths))
    for name in ("VGpu", "Networking", "ClipboardRedirection", "PrinterRedirection", "AudioInput", "VideoInput"):
        assert root.findtext(name) == "Disable"
    assert root.findtext("ProtectedClient") == "Enable"
    mappings = root.findall("./MappedFolders/MappedFolder")
    assert [mapping.findtext("ReadOnly") for mapping in mappings] == ["true", "true", "false"]
    assert root.findtext("./LogonCommand/Command") == BOOTSTRAP_COMMAND
    assert root.findtext("MemoryInMB") == "4096"


def test_wsb_xml_escapes_host_paths(tmp_path):
    _, _, paths, _, _ = _prepared_paths(tmp_path, "runtime & evidence")
    xml = build_wsb_xml(paths)
    assert "&amp;" in xml
    ET.fromstring(xml)


def test_wsb_never_mounts_project_root_or_secret_paths(tmp_path):
    _, _, paths, _, _ = _prepared_paths(tmp_path)
    xml = build_wsb_xml(paths)
    host_paths = [node.text for node in ET.fromstring(xml).findall("./MappedFolders/MappedFolder/HostFolder")]
    assert set(host_paths) == {str(paths.input_directory), str(paths.guest_directory), str(paths.evidence_directory)}
    lowered = xml.lower()
    for forbidden in ("studio_config", "projects_state", ".env", "auth", "token", "generated_projects"):
        assert forbidden not in lowered


def test_wsb_logon_command_is_static_and_network_cannot_be_enabled(tmp_path):
    _, _, paths, _, _ = _prepared_paths(tmp_path)
    assert "request.json" not in BOOTSTRAP_COMMAND
    assert "C:\\SandboxTestLab\\Guest\\bootstrap.ps1" in BOOTSTRAP_COMMAND
    with pytest.raises(ValueError, match="networking"):
        build_wsb_xml(paths, network_enabled=True)


def test_state_transitions_accept_valid_and_reject_invalid():
    validate_transition(RunStatus.CREATED, RunStatus.LAUNCHING)
    validate_transition(RunStatus.LAUNCHING, RunStatus.RUNNING)
    validate_transition(RunStatus.RUNNING, RunStatus.PASSED)
    with pytest.raises(ValueError):
        validate_transition(RunStatus.CREATED, RunStatus.PASSED)
    with pytest.raises(ValueError):
        validate_transition(RunStatus.PASSED, RunStatus.RUNNING)


def _evidence_payload(run_id: str, digest: str, status="passed"):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "phase": "completed" if status == "passed" else "failed",
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:00:01Z",
        "artifact": "artifact.bin",
        "artifact_sha256_host": digest,
        "artifact_sha256_guest": digest,
        "hash_verified": status == "passed",
        "guest_system": {"os_version": "Windows", "architecture": "True", "powershell_version": "5.1"},
        "errors": [] if status == "passed" else ["guest check failed"],
        "warnings": [],
    }


def test_evidence_parsing_validates_schema_and_identity(tmp_path):
    run_id = _request(tmp_path / "unused").run_id
    path = tmp_path / "status.json"
    atomic_write_json(path, _evidence_payload(run_id, "a" * 64))
    parsed = validate_guest_evidence(path, run_id)
    assert parsed.status == "passed"
    assert parsed.hash_verified is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schema_version=2), "schema_version"),
        (lambda value: value.update(run_id="wrong"), "run_id"),
    ],
)
def test_evidence_rejects_wrong_schema_or_run_id(tmp_path, mutation, message):
    run_id = _request(tmp_path / "unused").run_id
    payload = _evidence_payload(run_id, "a" * 64)
    mutation(payload)
    path = tmp_path / "status.json"
    atomic_write_json(path, payload)
    with pytest.raises(EvidenceError, match=message):
        validate_guest_evidence(path, run_id)


def test_evidence_rejects_malformed_and_oversized_json(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(EvidenceError, match="UTF-8 JSON"):
        validate_guest_evidence(malformed, "unused")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(EvidenceError, match="size limit"):
        validate_guest_evidence(oversized, "unused")


def test_evidence_accepts_valid_guest_failure_without_guest_hash(tmp_path):
    run_id = _request(tmp_path / "unused").run_id
    payload = _evidence_payload(run_id, "a" * 64, status="failed")
    payload["artifact_sha256_guest"] = ""
    path = tmp_path / "status.json"
    atomic_write_json(path, payload)
    parsed = validate_guest_evidence(path, run_id)
    assert parsed.status == "failed"
    assert parsed.artifact_sha256_guest is None
    assert parsed.hash_verified is False


def test_evidence_rejects_artifact_path_escape_and_duplicate_keys(tmp_path):
    run_id = _request(tmp_path / "unused").run_id
    payload = _evidence_payload(run_id, "a" * 64)
    payload["artifact"] = "..\\outside.bin"
    path = tmp_path / "status.json"
    atomic_write_json(path, payload)
    with pytest.raises(EvidenceError, match="may not contain a path"):
        validate_guest_evidence(path, run_id)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(EvidenceError, match="duplicate JSON keys"):
        validate_guest_evidence(duplicate, run_id)


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = 1234
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


class LifecycleScenario:
    def __init__(self, *, launcher_returncode=None, evidence_at=None, evidence_status="passed", malformed=False, cancellation=None, cancellation_at=None):
        self.clock = FakeClock()
        self.process = FakeProcess(returncode=launcher_returncode)
        self.evidence_at = evidence_at
        self.evidence_status = evidence_status
        self.malformed = malformed
        self.cancellation = cancellation
        self.cancellation_at = cancellation_at
        self.run_root = None
        self.evidence_written = False

    def launch(self, argv):
        self.run_root = Path(argv[1]).parent
        return self.process

    def sleep(self, seconds):
        self.clock.sleep(seconds)
        if self.cancellation is not None and self.cancellation_at is not None and self.clock.value >= self.cancellation_at:
            self.cancellation.set()
        if self.evidence_at is not None and self.clock.value >= self.evidence_at and not self.evidence_written:
            evidence_directory = self.run_root / "evidence"
            if self.malformed:
                (evidence_directory / "completion.json").write_text("{", encoding="utf-8")
            else:
                request = json.loads((self.run_root / "guest" / "request.json").read_text(encoding="utf-8"))
                atomic_write_json(
                    evidence_directory / "status.json",
                    _evidence_payload(request["run_id"], request["artifact_sha256_host"], status=self.evidence_status),
                )
                atomic_write_json(
                    evidence_directory / "completion.json",
                    {
                        "schema_version": 1,
                        "run_id": request["run_id"],
                        "completed": True,
                        "status": self.evidence_status,
                        "completed_at": "2026-01-01T00:00:01Z",
                    },
                )
            self.evidence_written = True


def _run_lifecycle_scenario(tmp_path, scenario, *, timeout=2, cancellation=None):
    artifact = tmp_path / "lifecycle.bin"
    artifact.write_bytes(b"lifecycle")
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    result = SandboxRunner(
        workspace_manager=manager,
        capability_detector=_available_capability,
        launcher=scenario.launch,
        monotonic=scenario.clock.monotonic,
        sleeper=scenario.sleep,
        poll_interval=0.25,
    ).run(_request(artifact, timeout_seconds=timeout), cancellation=cancellation)
    return result, manager


def _evidence_launcher(status="passed"):
    def launch(argv):
        run_root = Path(argv[1]).parent
        request = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        evidence = run_root / "evidence"
        atomic_write_json(evidence / "status.json", _evidence_payload(request["run_id"], request["artifact_sha256_host"], status=status))
        atomic_write_json(
            evidence / "completion.json",
            {
                "schema_version": 1,
                "run_id": request["run_id"],
                "completed": True,
                "status": status,
                "completed_at": "2026-01-01T00:00:01Z",
            },
        )
        return FakeProcess()

    return launch


def test_runner_simulated_passed_run(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"passed")
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    result = SandboxRunner(workspace_manager=manager, capability_detector=_available_capability, launcher=_evidence_launcher()).run(_request(artifact))
    assert result.status == RunStatus.PASSED
    assert result.exit_reason == "guest_completed"
    assert Path(result.evidence_path).is_file()
    assert (manager.paths_for(result.run_id).run_root / "host-result.json").is_file()


def test_runner_simulated_failed_run(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"failed")
    result = SandboxRunner(
        workspace_manager=SandboxWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=_evidence_launcher("failed"),
    ).run(_request(artifact))
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "guest_failed"


def test_runner_launcher_running_then_delayed_passed_evidence(tmp_path):
    scenario = LifecycleScenario(evidence_at=0.5)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario)
    assert result.status == RunStatus.PASSED
    assert result.launcher_return_code is None
    assert scenario.process.terminated is False


def test_runner_launcher_exit_zero_then_delayed_passed_evidence(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=0, evidence_at=0.5)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario)
    assert result.status == RunStatus.PASSED
    assert result.launcher_return_code == 0
    assert result.launcher_exited_at
    assert result.launcher_exit_elapsed_seconds == 0.0


def test_runner_launcher_exit_zero_then_failed_evidence(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=0, evidence_at=0.5, evidence_status="failed")
    result, _ = _run_lifecycle_scenario(tmp_path, scenario)
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "guest_failed"
    assert result.launcher_return_code == 0


def test_runner_launcher_exit_nonzero_then_valid_passed_evidence(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=7, evidence_at=0.5)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario)
    assert result.status == RunStatus.PASSED
    assert result.launcher_return_code == 7


def test_runner_launcher_exit_without_evidence_waits_until_total_timeout(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=0)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario, timeout=1)
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "launcher_exited_without_guest_completion"
    assert result.errors == ("launcher_exited_and_guest_completion_was_not_observed",)
    assert result.duration_seconds == 1.0
    assert result.launcher_return_code == 0


def test_runner_malformed_evidence_after_launcher_exit_fails_closed(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=0, evidence_at=0.5, malformed=True)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario)
    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "invalid_evidence"
    assert result.launcher_return_code == 0


def test_runner_cancellation_after_launcher_exit(tmp_path):
    cancellation = threading.Event()
    scenario = LifecycleScenario(launcher_returncode=0, cancellation=cancellation, cancellation_at=0.5)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario, cancellation=cancellation)
    assert result.status == RunStatus.CANCELLED
    assert result.launcher_return_code == 0


def test_runner_late_passed_evidence_writes_only_final_passed_host_result(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=0, evidence_at=0.5)
    result, manager = _run_lifecycle_scenario(tmp_path, scenario)
    host_result = json.loads((manager.paths_for(result.run_id).run_root / "host-result.json").read_text(encoding="utf-8"))
    assert result.status == RunStatus.PASSED
    assert host_result["status"] == "passed"
    assert host_result["exit_reason"] == "guest_completed"
    assert host_result["launcher_return_code"] == 0


def test_runner_timeout_deadline_does_not_restart_after_launcher_exit(tmp_path):
    scenario = LifecycleScenario(launcher_returncode=5)
    result, _ = _run_lifecycle_scenario(tmp_path, scenario, timeout=1)
    assert result.status == RunStatus.TIMED_OUT
    assert result.duration_seconds == 1.0
    assert scenario.clock.value == 1.0
    assert result.launcher_exit_elapsed_seconds == 0.0


def test_runner_simulated_timeout_terminates_only_owned_process(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"timeout")
    process = FakeProcess()
    clock = FakeClock()
    result = SandboxRunner(
        workspace_manager=SandboxWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=lambda _argv: process,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        poll_interval=0.5,
    ).run(_request(artifact, timeout_seconds=1))
    assert result.status == RunStatus.TIMED_OUT
    assert process.terminated is True


def test_runner_preexisting_cancellation_does_not_launch_sandbox(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"cancel")
    cancellation = threading.Event()
    cancellation.set()
    launched = False

    def launcher(_argv):
        nonlocal launched
        launched = True
        return FakeProcess()

    result = SandboxRunner(
        workspace_manager=SandboxWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_available_capability,
        launcher=launcher,
    ).run(_request(artifact), cancellation=cancellation)
    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled_before_launch"
    assert launched is False
    assert not (tmp_path / "runtime").exists()


def test_runner_deadline_elapsed_during_capability_does_not_launch(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"deadline")
    clock = FakeClock()
    launched = False

    def capability():
        clock.value = 2.0
        return _available_capability()

    def launcher(_argv):
        nonlocal launched
        launched = True
        return FakeProcess()

    result = SandboxRunner(
        workspace_manager=SandboxWorkspaceManager(tmp_path / "runtime"),
        capability_detector=capability,
        launcher=launcher,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).run(_request(artifact, timeout_seconds=1))
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "timeout_before_launch"
    assert launched is False


def test_workspace_revalidates_reparse_points_before_launch(tmp_path, monkeypatch):
    manager, request, paths, _, _ = _prepared_paths(tmp_path)
    write_wsb_config(paths)
    original = workspace_module._is_reparse_point
    monkeypatch.setattr(workspace_module, "_is_reparse_point", lambda path: path == paths.evidence_directory or original(path))
    with pytest.raises(WorkspaceError, match="symlinks or reparse points"):
        manager.validate_for_launch(request, paths)


def test_runner_revalidates_workspace_immediately_before_launcher(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"validate")

    class RecordingManager(SandboxWorkspaceManager):
        validated = False

        def validate_for_launch(self, request, paths):
            super().validate_for_launch(request, paths)
            self.validated = True

    manager = RecordingManager(tmp_path / "runtime")
    evidence_launcher = _evidence_launcher()

    def launcher(argv):
        assert manager.validated is True
        return evidence_launcher(argv)

    result = SandboxRunner(workspace_manager=manager, capability_detector=_available_capability, launcher=launcher).run(_request(artifact))
    assert result.status == RunStatus.PASSED


def test_runner_unavailable_does_not_create_workspace(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"x")
    unavailable = lambda: SandboxCapability(False, None, None, None, "unknown", False, False, ("unsupported_os",))
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    result = SandboxRunner(workspace_manager=manager, capability_detector=unavailable).run(_request(artifact))
    assert result.status == RunStatus.UNAVAILABLE
    assert not (tmp_path / "runtime").exists()


def test_cli_help_and_capability(monkeypatch, capsys):
    with pytest.raises(SystemExit) as help_exit:
        build_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    capsys.readouterr()
    monkeypatch.setattr("sandbox_test_lab.cli.detect_sandbox_capability", lambda: SandboxCapability(False, None, None, None, "unknown", False, False, ("unsupported_os",)))
    assert cli_main(["capability", "--json"]) == ExitCode.UNAVAILABLE
    assert json.loads(capsys.readouterr().out)["available"] is False


def test_cli_prepare_creates_workspace_without_launcher(tmp_path, capsys):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"prepare")
    runtime = tmp_path / "runtime"
    assert cli_main(["prepare", "--artifact", str(artifact), "--runtime-root", str(runtime), "--json"]) == ExitCode.SUCCESS
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "created"
    assert (Path(output["run_root"]) / "sandbox.wsb").is_file()


def test_metadata_rejects_secret_like_keys(tmp_path):
    with pytest.raises(ValueError, match="secret-like"):
        _request(tmp_path / "unused", metadata={"api_key": "not-allowed"})


def test_arbitrary_metadata_is_host_only_and_never_written_to_guest(tmp_path):
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"metadata")
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    request = _request(artifact, metadata={"note": "sensitive-value-must-stay-host-only"})
    paths, staged, digest = manager.create(request)
    guest_request = manager.write_guest_request(request, paths, staged, digest).read_text(encoding="utf-8")
    assert "metadata" not in guest_request
    assert "sensitive-value-must-stay-host-only" not in guest_request


def test_external_smoke_requires_environment_and_marker_opt_in(monkeypatch):
    monkeypatch.delenv(external_smoke.EXTERNAL_OPT_IN, raising=False)
    assert external_smoke.external_opt_in_enabled("external") is False
    monkeypatch.setenv(external_smoke.EXTERNAL_OPT_IN, "1")
    assert external_smoke.external_opt_in_enabled("") is False
    assert external_smoke.external_opt_in_enabled("not external") is False
    assert external_smoke.external_opt_in_enabled("external") is True


def test_subprocess_policy_never_uses_shell_true():
    package = Path(capability_module.__file__).parent
    python_sources = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "shell=True" not in python_sources


@pytest.mark.parametrize(
    "executable",
    ["taskkill", "TASKKILL.EXE", r"C:\Windows\System32\taskkill", r"C:\Windows\System32\taskkill.exe"],
)
def test_sandbox_argv_guard_rejects_taskkill_before_popen(monkeypatch, executable):
    called = False

    def popen(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Popen must not be called")

    monkeypatch.setattr(runner_module.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="taskkill"):
        launch_sandbox([executable, "/PID", "1"])
    assert called is False

    with pytest.raises(ValueError, match="taskkill"):
        launch_sandbox([r"C:\Windows\System32\WindowsSandbox.exe", executable])
    assert called is False


def test_sandbox_argv_guard_allows_evidence_name_and_windows_sandbox(monkeypatch):
    expected = [r"C:\Windows\System32\WindowsSandbox.exe", "production_taskkill_observed.wsb"]
    process = object()
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda argv, **_kwargs: process if argv == expected else None)
    assert validate_sandbox_argv(expected) == expected
    assert launch_sandbox(expected) is process
