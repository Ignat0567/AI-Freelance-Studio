from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from .fixture_builder import fixture_output_path, fixture_source_root, verify_fixture_provenance
from .installer import (
    ApplicationTestRequest,
    InstallScope,
    InstallerKind,
    NetworkPolicy,
    RebootPolicy,
)
from .models import SandboxRunPaths
from .workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file


INSTALL_EXECUTION_SCHEMA_VERSION = 2
INSTALL_EXECUTION_PROTOCOL = "controlled_fixture_installation_v1"
CONTROLLED_FIXTURE_PROFILE = "aifs_sandbox_fixture_v1"
FIXTURE_INSTALL_ROOT = r"sandbox_user_local_app_data\Programs\AIFS Sandbox Fixture"
FIXTURE_MARKER_NAME = "fixture-manifest.json"
FIXTURE_PAYLOAD_NAME = "payload.txt"
MIN_HOST_TIMEOUT_SECONDS = 60
MAX_HOST_TIMEOUT_SECONDS = 600
INSTALL_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_NSIS_INSTALL_EXTERNAL"


@dataclass(frozen=True, slots=True)
class FixtureInstallationRequest:
    application_request: ApplicationTestRequest
    expected_fixture_profile: str
    timeout_seconds: float = 240.0

    def __post_init__(self) -> None:
        if self.expected_fixture_profile != CONTROLLED_FIXTURE_PROFILE:
            raise ValueError("controlled_fixture_required")
        if not isinstance(self.application_request, ApplicationTestRequest):
            raise ValueError("application_request must be validated")
        recipe = self.application_request.installation_recipe
        if recipe.installer_kind is not InstallerKind.NSIS_EXE:
            raise ValueError("controlled fixture requires installer_kind=nsis_exe")
        if recipe.expected_install_scope is not InstallScope.USER:
            raise ValueError("controlled fixture requires user scope")
        if recipe.network_policy is not NetworkPolicy.DISABLED:
            raise ValueError("controlled fixture requires disabled network")
        if recipe.reboot_policy is not RebootPolicy.FORBID:
            raise ValueError("controlled fixture requires forbidden reboot")
        if recipe.expected_executable is not None or recipe.expected_process_name is not None:
            raise ValueError("controlled fixture does not install or launch an executable")
        if recipe.success_requirements != ("artifact_hash_verified", "installer_exit_zero"):
            raise ValueError("controlled fixture requires exact installation success requirements")
        if not MIN_HOST_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_HOST_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between {MIN_HOST_TIMEOUT_SECONDS} and {MAX_HOST_TIMEOUT_SECONDS}")
        if self.timeout_seconds < recipe.install_timeout_seconds + 30:
            raise ValueError("host timeout must include at least 30 seconds of startup and evidence grace")
        selected = os.path.normcase(str(self.application_request.artifact.resolve(strict=True)))
        controlled = os.path.normcase(str(fixture_output_path().resolve(strict=True)))
        if selected != controlled:
            raise ValueError("controlled_fixture_required")
        verify_fixture_provenance(self.application_request.artifact, self.application_request.expected_sha256)

    @property
    def source_artifact(self) -> Path:
        return self.application_request.artifact

    @property
    def expected_sha256(self) -> str:
        return self.application_request.expected_sha256

    @property
    def run_id(self) -> str:
        return self.application_request.run_id

    @property
    def network_enabled(self) -> bool:
        return False


def installation_external_opt_in_enabled(explicit_external: bool) -> bool:
    return explicit_external and os.environ.get(INSTALL_EXTERNAL_OPT_IN) == "1"


def _fixture_content_hash(name: str) -> str:
    return sha256_file(fixture_source_root() / name)


def build_installation_guest_request(
    request: FixtureInstallationRequest,
    staged_artifact: Path,
    artifact_sha256: str,
) -> dict[str, Any]:
    recipe = request.application_request.installation_recipe
    if staged_artifact.name != "artifact.exe" or artifact_sha256 != request.expected_sha256:
        raise WorkspaceError("controlled fixture staging identity is invalid")
    return {
        "schema_version": INSTALL_EXECUTION_SCHEMA_VERSION,
        "protocol": INSTALL_EXECUTION_PROTOCOL,
        "run_id": request.run_id,
        "artifact_name": "artifact.exe",
        "artifact_sha256_host": artifact_sha256,
        "controlled_fixture_profile": CONTROLLED_FIXTURE_PROFILE,
        "installation_recipe": {
            "installer_kind": recipe.installer_kind.value,
            "artifact_sha256": recipe.artifact_sha256,
            "install_timeout_seconds": recipe.install_timeout_seconds,
            "expected_install_scope": recipe.expected_install_scope.value,
            "reboot_policy": recipe.reboot_policy.value,
            "network_policy": recipe.network_policy.value,
            "success_requirements": list(recipe.success_requirements),
            "profile_version": recipe.profile_version,
        },
        "expected_install_root": FIXTURE_INSTALL_ROOT,
        "expected_marker_name": FIXTURE_MARKER_NAME,
        "expected_marker_sha256": _fixture_content_hash(FIXTURE_MARKER_NAME),
        "expected_payload_name": FIXTURE_PAYLOAD_NAME,
        "expected_payload_sha256": _fixture_content_hash(FIXTURE_PAYLOAD_NAME),
    }


class FixtureInstallationWorkspaceManager(SandboxWorkspaceManager):
    def __init__(self, runtime_root: Path | None = None):
        super().__init__(runtime_root, Path(__file__).with_name("guest") / "install_fixture.ps1")

    def write_guest_request(
        self,
        request: FixtureInstallationRequest,
        paths: SandboxRunPaths,
        staged_artifact: Path,
        artifact_sha256: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(request_path, build_installation_guest_request(request, staged_artifact, artifact_sha256))
        return request_path

    def validate_for_launch(self, request: FixtureInstallationRequest, paths: SandboxRunPaths) -> None:
        super().validate_for_launch(request, paths)
        verify_fixture_provenance(request.source_artifact, request.expected_sha256)
        if tuple(paths.evidence_directory.iterdir()):
            raise WorkspaceError("evidence directory must be empty before fixture installation")
        input_files = tuple(paths.input_directory.iterdir())
        if len(input_files) != 1 or input_files[0].name != "artifact.exe":
            raise WorkspaceError("controlled fixture must be staged as artifact.exe")
        if sha256_file(input_files[0]) != request.expected_sha256:
            raise WorkspaceError("staged controlled fixture hash changed before launch")
        staged_bootstrap = paths.guest_directory / "bootstrap.ps1"
        if sha256_file(staged_bootstrap) != sha256_file(self.bootstrap_source):
            raise WorkspaceError("controlled fixture bootstrap hash does not match the package resource")
        payload = json.loads((paths.guest_directory / "request.json").read_text(encoding="utf-8"))
        if payload.get("schema_version") != INSTALL_EXECUTION_SCHEMA_VERSION or payload.get("protocol") != INSTALL_EXECUTION_PROTOCOL or payload.get("run_id") != request.run_id:
            raise WorkspaceError("controlled fixture guest request identity is invalid")
