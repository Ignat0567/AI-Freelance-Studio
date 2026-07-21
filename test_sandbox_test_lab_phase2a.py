from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from sandbox_test_lab.cli import ExitCode, main as cli_main
from sandbox_test_lab.evidence import validate_guest_evidence
from sandbox_test_lab.installer import (
    ApplicationTestRequest,
    GuestOutcome,
    GuestPhase,
    INSTALLATION_SCHEMA_VERSION,
    InstallationPlan,
    InstallationRecipe,
    InstallerKind,
    TRUSTED_MSIEXEC,
    build_guest_request_v2,
    build_initial_evidence_v2,
    plan_installation,
)
from sandbox_test_lab.models import SandboxRunRequest
from sandbox_test_lab.workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file
from sandbox_test_lab.wsb_config import EVIDENCE_DESTINATION, INPUT_DESTINATION, build_wsb_xml


pytestmark = pytest.mark.unit


def _artifact(tmp_path: Path, suffix: str = ".exe", content: bytes = b"installer") -> tuple[Path, str]:
    artifact = tmp_path / f"AI Freelance Studio Setup{suffix}"
    artifact.write_bytes(content)
    return artifact, sha256_file(artifact)


def _request(tmp_path: Path, kind: InstallerKind = InstallerKind.NSIS_EXE, suffix: str | None = None, **recipe_updates) -> ApplicationTestRequest:
    actual_suffix = suffix if suffix is not None else (".msi" if kind is InstallerKind.MSI else ".exe")
    artifact, digest = _artifact(tmp_path, actual_suffix)
    recipe = InstallationRecipe(
        installer_kind=kind,
        artifact_name=artifact.name,
        artifact_sha256=digest,
        **recipe_updates,
    )
    return ApplicationTestRequest(artifact=artifact, expected_sha256=digest, installation_recipe=recipe)


def _phase1_evidence(run_id: str, digest: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "passed",
        "phase": "completed",
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
        "completed_at": "2026-01-01T00:00:01Z",
        "artifact": "artifact.exe",
        "artifact_sha256_host": digest,
        "artifact_sha256_guest": digest,
        "hash_verified": True,
        "guest_system": {"os_version": "Windows", "architecture": "64-bit", "powershell_version": "5.1"},
        "errors": [],
        "warnings": [],
    }


def test_valid_nsis_plan_has_exact_controlled_argv(tmp_path):
    plan = plan_installation(_request(tmp_path, expected_executable="AI Freelance Studio.exe"))
    assert plan.supported is True
    assert plan.installer_kind == "nsis_exe"
    assert plan.controlled_executable == rf"{INPUT_DESTINATION}\artifact.exe"
    assert plan.argument_tokens == ("/S",)
    assert plan.dry_run is True
    assert plan.blockers == ()
    assert plan.expected_paths == (r"<sandbox-user-local-app-data>\Programs\AI Freelance Studio\AI Freelance Studio.exe",)


def test_valid_msi_plan_uses_exact_trusted_msiexec_argv(tmp_path):
    plan = plan_installation(_request(tmp_path, InstallerKind.MSI))
    assert plan.supported is True
    assert plan.controlled_executable == TRUSTED_MSIEXEC
    assert plan.argument_tokens == (
        "/i",
        rf"{INPUT_DESTINATION}\artifact.msi",
        "/qn",
        "/norestart",
        "/L*v",
        rf"{EVIDENCE_DESTINATION}\installer.log",
    )
    assert "/S" not in plan.argument_tokens


def test_portable_plan_is_launch_only_and_never_installs(tmp_path):
    plan = plan_installation(_request(tmp_path, InstallerKind.PORTABLE_EXE))
    assert plan.supported is True
    assert plan.controlled_executable == rf"{INPUT_DESTINATION}\artifact.exe"
    assert plan.argument_tokens == ()
    assert plan.expected_paths == (rf"{INPUT_DESTINATION}\artifact.exe",)
    assert plan.warnings == ("portable_profile_is_launch_only",)


def test_unsupported_profile_returns_blocker_without_executable(tmp_path):
    plan = plan_installation(_request(tmp_path, InstallerKind.UNSUPPORTED, suffix=".bin"))
    assert plan.supported is False
    assert plan.controlled_executable is None
    assert plan.argument_tokens == ()
    assert plan.blockers == ("installer_profile_is_unsupported",)


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [(InstallerKind.NSIS_EXE, ".msi"), (InstallerKind.MSI, ".exe"), (InstallerKind.PORTABLE_EXE, ".zip")],
)
def test_installer_kind_must_match_artifact_extension(tmp_path, kind, suffix):
    with pytest.raises(ValueError, match="incompatible"):
        plan_installation(_request(tmp_path, kind, suffix=suffix))


def test_missing_and_directory_artifacts_are_rejected(tmp_path):
    missing = tmp_path / "missing.exe"
    recipe = InstallationRecipe(InstallerKind.NSIS_EXE, missing.name, "a" * 64)
    with pytest.raises(WorkspaceError, match="does not exist"):
        ApplicationTestRequest(missing, "a" * 64, recipe)

    directory = tmp_path / "directory.exe"
    directory.mkdir()
    recipe = InstallationRecipe(InstallerKind.NSIS_EXE, directory.name, "a" * 64)
    with pytest.raises(WorkspaceError, match="regular file"):
        ApplicationTestRequest(directory, "a" * 64, recipe)


def test_artifact_hash_mismatch_is_rejected(tmp_path):
    artifact, _ = _artifact(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        ApplicationTestRequest(artifact, "0" * 64, InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, "0" * 64))


@pytest.mark.parametrize(
    "expected",
    [
        r"..\outside.exe",
        r"folder\..\outside.exe",
        r"\\server\share\app.exe",
        "//server/share/app.exe",
        r"\Windows\app.exe",
        r"C:relative.exe",
        r"CON\app.exe",
        "https://example.test/app.exe",
    ],
)
def test_traversal_unc_and_url_expected_paths_are_rejected(tmp_path, expected):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_executable=expected)


@pytest.mark.parametrize("suffix", [".bat", ".cmd", ".ps1", ".vbs", ".js", ".hta"])
def test_script_artifacts_are_rejected(tmp_path, suffix):
    with pytest.raises(ValueError, match="script"):
        plan_installation(_request(tmp_path, InstallerKind.UNSUPPORTED, suffix=suffix))


@pytest.mark.parametrize("name", ["powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe", "mshta.exe"])
def test_command_interpreters_cannot_be_installer_artifacts(tmp_path, name):
    artifact = tmp_path / name
    artifact.write_bytes(b"interpreter")
    digest = sha256_file(artifact)
    with pytest.raises(ValueError, match="interpreters"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest)


def test_contract_has_no_raw_command_or_arbitrary_arguments(tmp_path):
    recipe_fields = {item.name for item in fields(InstallationRecipe)}
    plan_fields = {item.name for item in fields(InstallationPlan)}
    assert "command" not in recipe_fields | plan_fields
    assert "arguments" not in recipe_fields | plan_fields
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(TypeError):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, command="calc.exe")
    with pytest.raises(TypeError):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, arguments=["/unsafe"])


@pytest.mark.parametrize("value", ["App&calc.exe", "App|other.exe", "App;other.exe", "App^other.exe"])
def test_shell_metacharacters_are_rejected(tmp_path, value):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError, match="metacharacters"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_executable=value)


@pytest.mark.parametrize("value", [r"%TEMP%\app.exe", r"$env:TEMP\app.exe", r"${TEMP}\app.exe"])
def test_environment_expansion_is_rejected(tmp_path, value):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError, match="environment expansion"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_executable=value)


def test_reboot_network_and_machine_scope_requests_are_rejected(tmp_path):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError, match="reboot"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, reboot_policy="allow")
    with pytest.raises(ValueError, match="network"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, network_policy="enabled")
    with pytest.raises(ValueError, match="machine-scope"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_install_scope="machine")


def test_safe_and_unsafe_expected_executables(tmp_path):
    request = _request(tmp_path, expected_executable=r"bin\AI Freelance Studio.exe")
    assert request.installation_recipe.expected_executable == r"bin\AI Freelance Studio.exe"
    with pytest.raises(ValueError, match="relative"):
        InstallationRecipe(
            InstallerKind.NSIS_EXE,
            request.artifact.name,
            request.expected_sha256,
            expected_executable=r"C:\Windows\System32\calc.exe",
        )


@pytest.mark.parametrize("name", [r"folder\app.exe", "app&calc.exe", "powershell", "app.cmd"])
def test_unsafe_process_names_are_rejected(tmp_path, name):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError, match="expected_process_name"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_process_name=name)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("install_timeout_seconds", 9), ("install_timeout_seconds", 1801), ("launch_timeout_seconds", 0), ("launch_timeout_seconds", 301)],
)
def test_timeout_bounds_are_enforced(tmp_path, field_name, value):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError, match="must be between"):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, **{field_name: value})


def test_timeout_boundary_values_are_accepted(tmp_path):
    artifact, digest = _artifact(tmp_path)
    recipe = InstallationRecipe(
        InstallerKind.NSIS_EXE,
        artifact.name,
        digest,
        install_timeout_seconds=10,
        launch_timeout_seconds=300,
    )
    assert recipe.install_timeout_seconds == 10
    assert recipe.launch_timeout_seconds == 300


def test_plan_is_versioned_and_json_serializable(tmp_path):
    payload = plan_installation(_request(tmp_path)).to_dict()
    assert payload["schema_version"] == INSTALLATION_SCHEMA_VERSION == 2
    assert payload["dry_run"] is True
    assert json.loads(json.dumps(payload))["argument_tokens"] == ["/S"]


def test_v2_guest_protocol_is_not_started_and_omits_host_metadata(tmp_path):
    base = _request(tmp_path)
    request = ApplicationTestRequest(
        base.artifact,
        base.expected_sha256,
        base.installation_recipe,
        run_id=base.run_id,
        metadata={"ticket": "host-only-value"},
    )
    plan = plan_installation(request)
    guest_request = build_guest_request_v2(request)
    evidence = build_initial_evidence_v2(request)
    assert guest_request["schema_version"] == 2
    assert "metadata" not in guest_request
    assert "host-only-value" not in json.dumps(guest_request)
    assert evidence["phase"] == GuestPhase.INSTALLATION_PLANNED.value
    assert evidence["outcome"] is None
    assert evidence["runtime_state"] == "not_started"
    for field_name in (
        "artifact_sha256_guest",
        "installer_started_at",
        "installer_finished_at",
        "installer_exit_code",
        "reboot_required",
        "install_log",
        "installed_executable_found",
        "launch_started_at",
        "launched_process",
        "first_launch_verified",
    ):
        assert evidence[field_name] is None
    assert {item.value for item in GuestOutcome} == {
        "passed", "failed", "timed_out", "reboot_required", "unsupported", "infrastructure_error"
    }


def test_phase1_request_and_evidence_remain_schema_v1_compatible(tmp_path):
    artifact, digest = _artifact(tmp_path)
    request = SandboxRunRequest(artifact, expected_sha256=digest)
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    paths, staged, copied_digest = manager.create(request)
    guest_request = json.loads(manager.write_guest_request(request, paths, staged, copied_digest).read_text(encoding="utf-8"))
    assert guest_request == {
        "schema_version": 1,
        "run_id": request.run_id,
        "artifact_name": "artifact.exe",
        "artifact_sha256_host": digest,
        "expected_sha256": digest,
    }
    evidence_path = tmp_path / "status.json"
    atomic_write_json(evidence_path, _phase1_evidence(request.run_id, digest))
    assert validate_guest_evidence(evidence_path, request.run_id).status == "passed"


def test_cli_plan_install_is_dry_run_json_and_does_not_use_runner(tmp_path, monkeypatch, capsys):
    artifact, digest = _artifact(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("execution and workspace boundaries must not be called")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("sandbox_test_lab.cli.SandboxRunner", forbidden)
    monkeypatch.setattr("sandbox_test_lab.cli.SandboxWorkspaceManager", forbidden)
    monkeypatch.setattr("sandbox_test_lab.cli.detect_sandbox_capability", forbidden)
    monkeypatch.setattr("sandbox_test_lab.cli.write_wsb_config", forbidden)
    code = cli_main([
        "plan-install",
        "--artifact", str(artifact),
        "--sha256", digest,
        "--installer-kind", "nsis_exe",
        "--expected-executable", "AI Freelance Studio.exe",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.SUCCESS
    assert payload["dry_run"] is True
    assert payload["controlled_executable"] == rf"{INPUT_DESTINATION}\artifact.exe"
    assert payload["argument_tokens"] == ["/S"]
    assert not (tmp_path / "runtime").exists()


def test_cli_unsupported_plan_prints_blockers(tmp_path, capsys):
    artifact, digest = _artifact(tmp_path, ".bin")
    code = cli_main(["plan-install", "--artifact", str(artifact), "--sha256", digest, "--installer-kind", "unsupported", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.INVALID_INPUT
    assert payload["supported"] is False
    assert payload["blockers"] == ["installer_profile_is_unsupported"]


def test_secret_like_metadata_is_rejected_and_never_reaches_plan(tmp_path):
    base = _request(tmp_path)
    with pytest.raises(ValueError, match="secret-like"):
        ApplicationTestRequest(base.artifact, base.expected_sha256, base.installation_recipe, metadata={"auth_token": "value"})
    assert "metadata" not in plan_installation(base).to_dict()


def test_installer_mount_is_read_only_and_evidence_is_only_writable_mount(tmp_path):
    artifact, digest = _artifact(tmp_path)
    manager = SandboxWorkspaceManager(tmp_path / "runtime")
    request = SandboxRunRequest(artifact, expected_sha256=digest)
    paths, staged, copied_digest = manager.create(request)
    manager.write_guest_request(request, paths, staged, copied_digest)
    mappings = ET.fromstring(build_wsb_xml(paths)).findall("./MappedFolders/MappedFolder")
    permissions = {item.findtext("SandboxFolder"): item.findtext("ReadOnly") for item in mappings}
    assert permissions[INPUT_DESTINATION] == "true"
    assert permissions[EVIDENCE_DESTINATION] == "false"
    assert list(permissions.values()).count("false") == 1


def test_planner_source_has_no_execution_or_shell_contract():
    source = Path(__file__).parent / "sandbox_test_lab" / "installer.py"
    text = source.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "shell=True" not in text
    assert "Popen" not in text
    assert "powershell.exe -" not in text.lower()
    assert "command:" not in text


def test_installation_plan_rejects_forged_executable_and_arguments(tmp_path):
    valid = plan_installation(_request(tmp_path))
    payload = valid.to_dict()
    payload["controlled_executable"] = r"C:\Windows\System32\cmd.exe"
    payload["argument_tokens"] = ("/c", "calc.exe")
    with pytest.raises(ValueError, match="controlled installer profile"):
        InstallationPlan(**payload)


def test_v2_guest_plan_preserves_enforced_recipe_policies(tmp_path):
    request = _request(
        tmp_path,
        expected_process_name="AI Freelance Studio.exe",
        install_timeout_seconds=120,
        launch_timeout_seconds=30,
        success_requirements=("artifact_hash_verified", "installer_exit_zero", "process_started"),
    )
    plan = build_guest_request_v2(request)["installation_plan"]
    assert plan["install_timeout_seconds"] == 120
    assert plan["launch_timeout_seconds"] == 30
    assert plan["expected_process_name"] == "AI Freelance Studio.exe"
    assert plan["expected_install_scope"] == "user"
    assert plan["reboot_policy"] == "forbid"
    assert plan["network_policy"] == "disabled"
    assert plan["success_requirements"] == ("artifact_hash_verified", "installer_exit_zero", "process_started")


@pytest.mark.parametrize("name", ["CON.exe", "nul.exe", "COM1.exe", "app.exe.", "app.exe "])
def test_windows_reserved_or_ambiguous_names_are_rejected(tmp_path, name):
    artifact, digest = _artifact(tmp_path)
    with pytest.raises(ValueError):
        InstallationRecipe(InstallerKind.NSIS_EXE, artifact.name, digest, expected_executable=name)


def test_portable_profile_rejects_installed_expected_path(tmp_path):
    with pytest.raises(ValueError, match="does not accept expected_executable"):
        plan_installation(_request(tmp_path, InstallerKind.PORTABLE_EXE, expected_executable="installed.exe"))
