from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from .fixture_builder import (
    FIXTURE_GUI_EXECUTABLE_NAME,
    TRUSTED_FIXTURE_GUI_SHA256,
    fixture_output_path,
    fixture_source_root,
    verify_fixture_provenance,
)
from .fixture_installation import (
    FIXTURE_INSTALL_ROOT,
    FIXTURE_MARKER_NAME,
    FIXTURE_PAYLOAD_NAME,
    ensure_no_active_windows_sandbox_session,
)
from .installer import ApplicationTestRequest, InstallScope, InstallerKind, NetworkPolicy, RebootPolicy
from .models import SandboxRunPaths
from .workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file


INSTALL_LAUNCH_SCHEMA_VERSION = 2
INSTALL_LAUNCH_PROTOCOL = "controlled_fixture_install_launch_v1"
CONTROLLED_LAUNCH_PROFILE = "controlled_fixture_gui_v1"
LAUNCH_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_GUI_EXTERNAL"
FIXTURE_WINDOW_TITLE = "AIFS Sandbox Fixture"
LAUNCH_TIMEOUT_SECONDS = 30
MINIMUM_STABLE_DURATION_SECONDS = 3
CLEANUP_TIMEOUT_SECONDS = 10
MIN_HOST_TIMEOUT_SECONDS = 90
MAX_HOST_TIMEOUT_SECONDS = 600

LAUNCH_PROFILE = {
    "logical_executable_name": FIXTURE_GUI_EXECUTABLE_NAME,
    "expected_installed_exe_sha256": TRUSTED_FIXTURE_GUI_SHA256,
    "process_image_name": FIXTURE_GUI_EXECUTABLE_NAME,
    "exact_window_title": FIXTURE_WINDOW_TITLE,
    "launch_timeout_seconds": LAUNCH_TIMEOUT_SECONDS,
    "minimum_stable_duration_seconds": MINIMUM_STABLE_DURATION_SECONDS,
    "cleanup_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
}


@dataclass(frozen=True, slots=True)
class FixtureLaunchRequest:
    application_request: ApplicationTestRequest
    launch_profile: str
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.launch_profile != CONTROLLED_LAUNCH_PROFILE:
            raise ValueError("controlled_fixture_gui_profile_required")
        if not isinstance(self.application_request, ApplicationTestRequest):
            raise ValueError("application_request must be validated")
        recipe = self.application_request.installation_recipe
        if recipe.installer_kind is not InstallerKind.NSIS_EXE:
            raise ValueError("controlled fixture requires installer_kind=nsis_exe")
        if recipe.expected_install_scope is not InstallScope.USER:
            raise ValueError("controlled fixture requires user scope")
        if recipe.network_policy is not NetworkPolicy.DISABLED or recipe.reboot_policy is not RebootPolicy.FORBID:
            raise ValueError("controlled fixture requires disabled network and forbidden reboot")
        if recipe.expected_executable != FIXTURE_GUI_EXECUTABLE_NAME or recipe.expected_process_name != FIXTURE_GUI_EXECUTABLE_NAME:
            raise ValueError("controlled fixture GUI identity is fixed")
        if recipe.launch_timeout_seconds != LAUNCH_TIMEOUT_SECONDS:
            raise ValueError("controlled fixture GUI launch timeout is fixed")
        if recipe.success_requirements != (
            "artifact_hash_verified",
            "installer_exit_zero",
            "expected_executable_found",
            "process_started",
            "first_launch_verified",
        ):
            raise ValueError("controlled fixture GUI success requirements are fixed")
        minimum = recipe.install_timeout_seconds + LAUNCH_TIMEOUT_SECONDS + MINIMUM_STABLE_DURATION_SECONDS + CLEANUP_TIMEOUT_SECONDS + 30
        if not MIN_HOST_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_HOST_TIMEOUT_SECONDS or self.timeout_seconds < minimum:
            raise ValueError("host timeout does not cover the fixed install-launch lifecycle")
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


def launch_external_opt_in_enabled(explicit_external: bool) -> bool:
    return explicit_external and os.environ.get(LAUNCH_EXTERNAL_OPT_IN) == "1"


def _source_hash(name: str) -> str:
    return sha256_file(fixture_source_root() / name)


def build_launch_guest_request(
    request: FixtureLaunchRequest,
    staged_artifact: Path,
    artifact_sha256: str,
) -> dict[str, Any]:
    recipe = request.application_request.installation_recipe
    if staged_artifact.name != "artifact.exe" or artifact_sha256 != request.expected_sha256:
        raise WorkspaceError("controlled fixture staging identity is invalid")
    return {
        "schema_version": INSTALL_LAUNCH_SCHEMA_VERSION,
        "protocol": INSTALL_LAUNCH_PROTOCOL,
        "run_id": request.run_id,
        "artifact_name": "artifact.exe",
        "artifact_sha256_host": artifact_sha256,
        "controlled_fixture_profile": "aifs_sandbox_fixture_v1",
        "installation_recipe": {
            "installer_kind": "nsis_exe",
            "install_timeout_seconds": recipe.install_timeout_seconds,
            "expected_install_scope": "user",
            "reboot_policy": "forbid",
            "network_policy": "disabled",
        },
        "expected_install_root": FIXTURE_INSTALL_ROOT,
        "expected_marker_name": FIXTURE_MARKER_NAME,
        "expected_marker_sha256": _source_hash(FIXTURE_MARKER_NAME),
        "expected_payload_name": FIXTURE_PAYLOAD_NAME,
        "expected_payload_sha256": _source_hash(FIXTURE_PAYLOAD_NAME),
        "launch_profile_name": CONTROLLED_LAUNCH_PROFILE,
        "launch_profile": dict(LAUNCH_PROFILE),
    }


class FixtureLaunchWorkspaceManager(SandboxWorkspaceManager):
    def __init__(self, runtime_root: Path | None = None):
        super().__init__(runtime_root, Path(__file__).with_name("guest") / "install_launch_fixture.ps1")

    def write_guest_request(
        self,
        request: FixtureLaunchRequest,
        paths: SandboxRunPaths,
        staged_artifact: Path,
        artifact_sha256: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(request_path, build_launch_guest_request(request, staged_artifact, artifact_sha256))
        return request_path

    def validate_for_launch(self, request: FixtureLaunchRequest, paths: SandboxRunPaths) -> None:
        super().validate_for_launch(request, paths)
        verify_fixture_provenance(request.source_artifact, request.expected_sha256)
        if tuple(paths.evidence_directory.iterdir()):
            raise WorkspaceError("evidence directory must be empty before fixture install-launch")
        input_files = tuple(paths.input_directory.iterdir())
        if len(input_files) != 1 or input_files[0].name != "artifact.exe":
            raise WorkspaceError("controlled fixture must be staged as artifact.exe")
        if sha256_file(input_files[0]) != request.expected_sha256:
            raise WorkspaceError("staged controlled fixture hash changed before launch")
        staged_bootstrap = paths.guest_directory / "bootstrap.ps1"
        if sha256_file(staged_bootstrap) != sha256_file(self.bootstrap_source):
            raise WorkspaceError("controlled install-launch bootstrap hash changed")
        payload = json.loads((paths.guest_directory / "request.json").read_text(encoding="utf-8"))
        if payload != build_launch_guest_request(request, input_files[0], request.expected_sha256):
            raise WorkspaceError("controlled install-launch guest request changed before launch")
        ensure_no_active_windows_sandbox_session()
