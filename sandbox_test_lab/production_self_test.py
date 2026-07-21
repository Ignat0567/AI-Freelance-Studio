from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
from types import MappingProxyType
from typing import Any
import uuid

from .fixture_installation import ensure_no_active_windows_sandbox_session
from .models import SandboxRunPaths, validate_run_id
from .workspace import (
    REPARSE_POINT_ATTRIBUTE,
    SandboxWorkspaceManager,
    WorkspaceError,
    atomic_write_json,
    sha256_file,
)


PRODUCTION_SCHEMA_VERSION = 3
PRODUCTION_PROTOCOL = "aifs_production_self_test_v1"
TRUSTED_PROFILE_NAME = "aifs_production_beta_1_self_test"
PRODUCT_NAME = "AI Freelance Studio"
PRODUCT_VERSION = "1.0.0-beta.1"
INSTALLER_FILENAME = "AI Freelance Studio-Setup-1.0.0-beta.1-win.exe"
INSTALLER_RELATIVE_PATH = Path("frontend") / "installers" / INSTALLER_FILENAME
INSTALLER_SIZE = 215224643
INSTALLER_SHA256 = "b72ad863f045f7877e9beb32826c2090d96bafe09f73b68c1892072cd4f1ef1f"
SIDECAR_FILENAME = INSTALLER_FILENAME + ".sha256"
SIDECAR_CONTENT = f"{INSTALLER_SHA256.upper()} *{INSTALLER_FILENAME}\n"
LOGICAL_INSTALL_ROOT = r"sandbox_user_local_app_data\Programs\AI Freelance Studio"
INSTALLED_EXE_RELATIVE = "AI Freelance Studio.exe"
WINDOW_TITLE = "AI Freelance Studio"
BACKEND_RELATIVE = r"resources\backend\freelancerstudio-backend\freelancerstudio-backend.exe"
BACKEND_ENDPOINT = "http://127.0.0.1:8080/health"
INSTALL_TIMEOUT_SECONDS = 600
LAUNCH_TIMEOUT_SECONDS = 60
STABLE_DURATION_SECONDS = 5
BACKEND_READINESS_SECONDS = 30
CLEANUP_TIMEOUT_SECONDS = 15
HOST_TIMEOUT_SECONDS = 590
HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS = 45
GUEST_EVIDENCE_RESERVE_SECONDS = 15
MINIMUM_CLEANUP_RESERVE_SECONDS = 20
PRODUCTION_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PRODUCTION_SELF_TEST"
EXTERNAL_HELPER_ALLOWLIST: frozenset[str] = frozenset({"conhost.exe"})
MINIMUM_FREE_SPACE_BYTES = 2 * 1024 * 1024 * 1024

TRUSTED_PROFILE = MappingProxyType({
    "installer_kind": "nsis_exe",
    "silent_argument": "/S",
    "expected_install_scope": "user",
    "reboot_policy": "forbid",
    "network_policy": "disabled",
    "expected_install_root": LOGICAL_INSTALL_ROOT,
    "installed_executable_relative": INSTALLED_EXE_RELATIVE,
    "exact_window_title": WINDOW_TITLE,
    "backend_executable_relative": BACKEND_RELATIVE,
    "backend_endpoint": BACKEND_ENDPOINT,
    "backend_required": True,
    "install_timeout_seconds": INSTALL_TIMEOUT_SECONDS,
    "launch_timeout_seconds": LAUNCH_TIMEOUT_SECONDS,
    "minimum_stable_duration_seconds": STABLE_DURATION_SECONDS,
    "backend_readiness_seconds": BACKEND_READINESS_SECONDS,
    "cleanup_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
})


def validate_production_deadline_configuration() -> None:
    values = (
        HOST_TIMEOUT_SECONDS, HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
        GUEST_EVIDENCE_RESERVE_SECONDS, MINIMUM_CLEANUP_RESERVE_SECONDS,
        INSTALL_TIMEOUT_SECONDS, LAUNCH_TIMEOUT_SECONDS, STABLE_DURATION_SECONDS,
        BACKEND_READINESS_SECONDS, CLEANUP_TIMEOUT_SECONDS,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 for value in values):
        raise ValueError("production deadline configuration is invalid")
    if HOST_TIMEOUT_SECONDS > 600 or HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS >= HOST_TIMEOUT_SECONDS:
        raise ValueError("production host deadline configuration is invalid")
    if HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS <= GUEST_EVIDENCE_RESERVE_SECONDS:
        raise ValueError("production terminal evidence margins are invalid")
    if INSTALL_TIMEOUT_SECONDS != 600 or CLEANUP_TIMEOUT_SECONDS > MINIMUM_CLEANUP_RESERVE_SECONDS:
        raise ValueError("production phase deadline configuration is invalid")
    expected_profile_deadlines = {
        "install_timeout_seconds": INSTALL_TIMEOUT_SECONDS,
        "launch_timeout_seconds": LAUNCH_TIMEOUT_SECONDS,
        "minimum_stable_duration_seconds": STABLE_DURATION_SECONDS,
        "backend_readiness_seconds": BACKEND_READINESS_SECONDS,
        "cleanup_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
    }
    if any(TRUSTED_PROFILE.get(name) != value for name, value in expected_profile_deadlines.items()):
        raise ValueError("production profile deadline configuration is inconsistent")
    mandatory = (
        LAUNCH_TIMEOUT_SECONDS + STABLE_DURATION_SECONDS + BACKEND_READINESS_SECONDS
        + MINIMUM_CLEANUP_RESERVE_SECONDS + GUEST_EVIDENCE_RESERVE_SECONDS
    )
    if mandatory >= HOST_TIMEOUT_SECONDS - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS:
        raise ValueError("production deadline reserves exhaust the guest budget")


def parse_guest_terminal_deadline_utc(value: str, *, now: datetime | None = None) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("guest terminal deadline must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("guest terminal deadline must be canonical UTC") from exc
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise ValueError("guest terminal deadline must be canonical UTC")
    authority = now or datetime.now(timezone.utc)
    if authority.tzinfo is None:
        raise ValueError("deadline authority must include a timezone")
    if parsed <= authority.astimezone(timezone.utc):
        raise ValueError("guest terminal deadline must be in the future")
    maximum = HOST_TIMEOUT_SECONDS - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS
    if (parsed - authority.astimezone(timezone.utc)).total_seconds() > maximum + 1:
        raise ValueError("guest terminal deadline exceeds the trusted budget")
    return parsed


def effective_phase_budget_seconds(configured_max: float, remaining_seconds: float, future_reserve_seconds: float) -> float:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (configured_max, remaining_seconds, future_reserve_seconds)):
        raise ValueError("phase budget inputs must be numeric")
    return min(configured_max, remaining_seconds - future_reserve_seconds)


def classify_canonical_windows_image_origin(
    image_path: str,
    *,
    install_root: str,
    windows_root: str,
    system32_root: str,
    syswow64_root: str,
) -> str:
    """Classify an absolute Windows image path using path-segment boundaries."""
    if not image_path or not ntpath.isabs(image_path):
        return "unavailable"

    def canonical(value: str) -> str:
        return ntpath.normcase(ntpath.normpath(value)).rstrip("\\")

    def contained(path: str, root: str) -> bool:
        try:
            return ntpath.commonpath((path, root)) == root
        except ValueError:
            return False

    image = canonical(image_path)
    roots = {
        "verified_install_root": canonical(install_root),
        "canonical_system32": canonical(system32_root),
        "canonical_syswow64": canonical(syswow64_root),
    }
    for origin, root in roots.items():
        if root and contained(image, root):
            return origin
    windows = canonical(windows_root)
    return "other_windows_directory" if windows and contained(image, windows) else "outside_untrusted"


def external_helper_trust_prerequisites_met(
    image_path: str,
    *,
    install_root: str,
    windows_root: str,
    system32_root: str,
    syswow64_root: str,
    image_regular_file: bool,
    image_reparse_free: bool,
    authenticode_status: str,
    microsoft_signed: bool | None,
    parent_relation_verified: bool,
    creation_after_launch: bool,
    process_role: str,
    eligible_for_gui_verification: bool,
    eligible_for_backend_verification: bool,
    eligible_for_cleanup: bool,
) -> bool:
    """Return whether an owned image is the one narrowly authorized system helper."""
    origin = classify_canonical_windows_image_origin(
        image_path,
        install_root=install_root,
        windows_root=windows_root,
        system32_root=system32_root,
        syswow64_root=syswow64_root,
    )
    basename = ntpath.basename(ntpath.normpath(image_path)).lower()
    exact_system32_helper = ntpath.normcase(ntpath.normpath(image_path)) == ntpath.normcase(
        ntpath.normpath(ntpath.join(system32_root, "conhost.exe"))
    )
    return (
        origin == "canonical_system32"
        and exact_system32_helper
        and basename in EXTERNAL_HELPER_ALLOWLIST
        and image_regular_file
        and image_reparse_free
        and authenticode_status == "valid"
        and microsoft_signed is True
        and parent_relation_verified
        and creation_after_launch
        and process_role == "system_helper"
        and not eligible_for_gui_verification
        and not eligible_for_backend_verification
        and not eligible_for_cleanup
    )


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def canonical_installer_path() -> Path:
    return repository_root() / INSTALLER_RELATIVE_PATH


def canonical_sidecar_path() -> Path:
    return canonical_installer_path().with_name(SIDECAR_FILENAME)


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _regular_non_reparse(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkspaceError(f"trusted production {label} is missing") from exc
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise WorkspaceError(f"trusted production {label} must be a regular non-reparse file")


def validate_trusted_production_artifact() -> Path:
    root = repository_root().resolve(strict=True)
    artifact = canonical_installer_path().absolute()
    sidecar = canonical_sidecar_path().absolute()
    if artifact.name != INSTALLER_FILENAME or sidecar.name != SIDECAR_FILENAME:
        raise WorkspaceError("trusted production artifact filename is invalid")
    for candidate, label in ((artifact, "installer"), (sidecar, "checksum sidecar")):
        _regular_non_reparse(candidate, label)
        if root not in candidate.parents or candidate.resolve(strict=True) != candidate:
            raise WorkspaceError(f"trusted production {label} escapes the repository or uses path indirection")
        cursor = candidate.parent
        while cursor != root:
            if cursor.is_symlink() or _is_reparse(cursor) or not cursor.is_dir():
                raise WorkspaceError(f"trusted production {label} path contains indirection")
            cursor = cursor.parent
    if artifact.stat().st_size != INSTALLER_SIZE:
        raise WorkspaceError("trusted production installer size mismatch")
    if sha256_file(artifact) != INSTALLER_SHA256:
        raise WorkspaceError("trusted production installer SHA-256 mismatch")
    try:
        sidecar_content = sidecar.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise WorkspaceError("trusted production checksum sidecar is not exact ASCII") from exc
    if sidecar_content != SIDECAR_CONTENT:
        raise WorkspaceError("trusted production checksum sidecar content mismatch")
    if shutil.disk_usage(artifact.parent).free < MINIMUM_FREE_SPACE_BYTES:
        raise WorkspaceError("production self-test requires at least 2 GiB free space")
    return artifact


@dataclass(frozen=True, slots=True)
class ProductionSelfTestRequest:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_seconds: float = HOST_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        validate_production_deadline_configuration()
        validate_run_id(self.run_id)
        if self.timeout_seconds != HOST_TIMEOUT_SECONDS:
            raise ValueError("production self-test host timeout is fixed")
        validate_trusted_production_artifact()

    @property
    def source_artifact(self) -> Path:
        return canonical_installer_path()

    @property
    def expected_sha256(self) -> str:
        return INSTALLER_SHA256

    @property
    def network_enabled(self) -> bool:
        return False


def production_external_opt_in_enabled(explicit_external: bool, mark_expression: str) -> bool:
    return (
        explicit_external
        and os.environ.get(PRODUCTION_EXTERNAL_OPT_IN) == "1"
        and mark_expression.strip() == "external"
    )


def build_production_guest_request(
    request: ProductionSelfTestRequest,
    staged_artifact: Path,
    artifact_sha256: str,
    *,
    guest_terminal_deadline_utc: str,
) -> dict[str, Any]:
    validate_production_deadline_configuration()
    parse_guest_terminal_deadline_utc(guest_terminal_deadline_utc)
    if staged_artifact.name != "artifact.exe" or artifact_sha256 != INSTALLER_SHA256:
        raise WorkspaceError("production staging identity is invalid")
    return {
        "schema_version": PRODUCTION_SCHEMA_VERSION,
        "protocol": PRODUCTION_PROTOCOL,
        "run_id": request.run_id,
        "profile_name": TRUSTED_PROFILE_NAME,
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "artifact_name": "artifact.exe",
        "artifact_size": INSTALLER_SIZE,
        "artifact_sha256_host": INSTALLER_SHA256,
        "guest_terminal_deadline_utc": guest_terminal_deadline_utc,
        "profile": dict(TRUSTED_PROFILE),
    }


class ProductionSelfTestWorkspaceManager(SandboxWorkspaceManager):
    def __init__(self, runtime_root: Path | None = None):
        super().__init__(runtime_root, Path(__file__).with_name("guest") / "production_self_test.ps1")

    def create(self, request: ProductionSelfTestRequest):
        validate_trusted_production_artifact()
        runtime_parent = self.runtime_root.parent
        if not runtime_parent.is_dir() or shutil.disk_usage(runtime_parent).free < MINIMUM_FREE_SPACE_BYTES:
            raise WorkspaceError("production self-test runtime requires at least 2 GiB free space")
        paths, staged, digest = super().create(request)
        if staged.name != "artifact.exe" or staged.stat().st_size != INSTALLER_SIZE or digest != INSTALLER_SHA256:
            raise WorkspaceError("staged production installer identity mismatch after copy")
        return paths, staged, digest

    def write_guest_request(
        self,
        request: ProductionSelfTestRequest,
        paths: SandboxRunPaths,
        staged_artifact: Path,
        artifact_sha256: str,
        *,
        guest_terminal_deadline_utc: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(request_path, build_production_guest_request(
            request, staged_artifact, artifact_sha256,
            guest_terminal_deadline_utc=guest_terminal_deadline_utc,
        ))
        return request_path

    def validate_for_launch(
        self,
        request: ProductionSelfTestRequest,
        paths: SandboxRunPaths,
        *,
        guest_terminal_deadline_utc: str,
    ) -> None:
        super().validate_for_launch(request, paths)
        validate_trusted_production_artifact()
        if tuple(paths.evidence_directory.iterdir()):
            raise WorkspaceError("production evidence directory must be empty before launch")
        inputs = tuple(paths.input_directory.iterdir())
        if len(inputs) != 1 or inputs[0].name != "artifact.exe":
            raise WorkspaceError("production input must contain only artifact.exe")
        if inputs[0].stat().st_size != INSTALLER_SIZE or sha256_file(inputs[0]) != INSTALLER_SHA256:
            raise WorkspaceError("staged production installer changed before launch")
        if sha256_file(paths.guest_directory / "bootstrap.ps1") != sha256_file(self.bootstrap_source):
            raise WorkspaceError("production guest script changed before launch")
        try:
            payload = json.loads((paths.guest_directory / "request.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("production guest request is invalid") from exc
        if payload != build_production_guest_request(
            request, inputs[0], INSTALLER_SHA256,
            guest_terminal_deadline_utc=guest_terminal_deadline_utc,
        ):
            raise WorkspaceError("production guest request changed before launch")
        ensure_no_active_windows_sandbox_session()
