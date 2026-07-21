from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from .models import new_run_id, validate_run_id, validate_safe_metadata
from .workspace import sha256_file, validate_source_artifact
from .wsb_config import EVIDENCE_DESTINATION, INPUT_DESTINATION


INSTALLATION_SCHEMA_VERSION = 2
INSTALLATION_PROFILE_VERSION = 1
MIN_INSTALL_TIMEOUT_SECONDS = 10
MAX_INSTALL_TIMEOUT_SECONDS = 1800
MIN_LAUNCH_TIMEOUT_SECONDS = 1
MAX_LAUNCH_TIMEOUT_SECONDS = 300
TRUSTED_MSIEXEC = r"C:\Windows\System32\msiexec.exe"
USER_INSTALL_ROOT = r"<sandbox-user-local-app-data>\Programs\AI Freelance Studio"

_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".vbs", ".js", ".hta"}
_FORBIDDEN_INSTALLER_NAMES = {"cmd.exe", "cscript.exe", "mshta.exe", "powershell.exe", "pwsh.exe", "wscript.exe"}
_SHELL_OR_EXPANSION = re.compile(r"[&|;<>^`$%\r\n]")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]*$")
_SAFE_PROCESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]*$")
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}
_SUCCESS_REQUIREMENTS = {
    "artifact_hash_verified",
    "installer_exit_zero",
    "expected_executable_found",
    "process_started",
    "first_launch_verified",
}
_REQUIRED_EVIDENCE = (
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
)


class InstallerKind(str, Enum):
    NSIS_EXE = "nsis_exe"
    MSI = "msi"
    PORTABLE_EXE = "portable_exe"
    UNSUPPORTED = "unsupported"


class InstallScope(str, Enum):
    USER = "user"
    MACHINE = "machine"


class RebootPolicy(str, Enum):
    FORBID = "forbid"


class NetworkPolicy(str, Enum):
    DISABLED = "disabled"


class GuestPhase(str, Enum):
    ARTIFACT_VERIFIED = "artifact_verified"
    INSTALLATION_PLANNED = "installation_planned"
    INSTALLING = "installing"
    INSTALLED = "installed"
    LAUNCHING = "launching"
    LAUNCHED = "launched"
    VERIFYING = "verifying"
    COMPLETED = "completed"


class GuestOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REBOOT_REQUIRED = "reboot_required"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


def _enum_value(enum_type: type[Enum], value: Any, field_name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is unsupported") from exc


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a SHA-256 string")
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256")
    return normalized


def _validate_relative_windows_path(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValueError(f"{field_name} must be a bounded relative Windows path")
    if value.startswith(("\\\\", "//")) or "://" in value.lower():
        raise ValueError(f"{field_name} may not be a UNC path or URL")
    if _SHELL_OR_EXPANSION.search(value):
        raise ValueError(f"{field_name} may not contain shell metacharacters or environment expansion")
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or path.root:
        raise ValueError(f"{field_name} must be relative")
    if any(
        part in {"", ".", ".."}
        or part.rstrip(" .") != part
        or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        or not _SAFE_COMPONENT.fullmatch(part)
        for part in path.parts
    ):
        raise ValueError(f"{field_name} contains traversal or unsupported characters")
    if path.suffix.lower() in _SCRIPT_EXTENSIONS:
        raise ValueError(f"{field_name} may not reference a script")
    return str(path)


def _validate_process_name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("expected_process_name must be a bounded process name")
    if _SHELL_OR_EXPANSION.search(value) or not _SAFE_PROCESS.fullmatch(value):
        raise ValueError("expected_process_name contains unsupported characters")
    path = PureWindowsPath(value)
    if len(path.parts) != 1 or path.drive or path.root or path.suffix.lower() != ".exe":
        raise ValueError("expected_process_name must be a filename ending in .exe")
    return value


@dataclass(frozen=True, slots=True)
class InstallationRecipe:
    installer_kind: InstallerKind
    artifact_name: str
    artifact_sha256: str
    install_timeout_seconds: int = 300
    launch_timeout_seconds: int = 60
    expected_executable: str | None = None
    expected_process_name: str | None = None
    expected_install_scope: InstallScope = InstallScope.USER
    reboot_policy: RebootPolicy = RebootPolicy.FORBID
    network_policy: NetworkPolicy = NetworkPolicy.DISABLED
    success_requirements: tuple[str, ...] = ("artifact_hash_verified",)
    profile_version: int = INSTALLATION_PROFILE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "installer_kind", _enum_value(InstallerKind, self.installer_kind, "installer_kind"))
        object.__setattr__(self, "expected_install_scope", _enum_value(InstallScope, self.expected_install_scope, "expected_install_scope"))
        object.__setattr__(self, "reboot_policy", _enum_value(RebootPolicy, self.reboot_policy, "reboot_policy"))
        object.__setattr__(self, "network_policy", _enum_value(NetworkPolicy, self.network_policy, "network_policy"))
        if self.profile_version != INSTALLATION_PROFILE_VERSION:
            raise ValueError("profile_version is unsupported")
        if self.expected_install_scope is not InstallScope.USER:
            raise ValueError("machine-scope installation is not supported in Phase 2A")
        if self.reboot_policy is not RebootPolicy.FORBID:
            raise ValueError("reboot must be forbidden")
        if self.network_policy is not NetworkPolicy.DISABLED:
            raise ValueError("network must remain disabled")
        if not isinstance(self.install_timeout_seconds, int) or isinstance(self.install_timeout_seconds, bool) or not MIN_INSTALL_TIMEOUT_SECONDS <= self.install_timeout_seconds <= MAX_INSTALL_TIMEOUT_SECONDS:
            raise ValueError(f"install_timeout_seconds must be between {MIN_INSTALL_TIMEOUT_SECONDS} and {MAX_INSTALL_TIMEOUT_SECONDS}")
        if not isinstance(self.launch_timeout_seconds, int) or isinstance(self.launch_timeout_seconds, bool) or not MIN_LAUNCH_TIMEOUT_SECONDS <= self.launch_timeout_seconds <= MAX_LAUNCH_TIMEOUT_SECONDS:
            raise ValueError(f"launch_timeout_seconds must be between {MIN_LAUNCH_TIMEOUT_SECONDS} and {MAX_LAUNCH_TIMEOUT_SECONDS}")
        artifact_name = _validate_relative_windows_path(self.artifact_name, "artifact_name")
        if len(PureWindowsPath(artifact_name).parts) != 1:
            raise ValueError("artifact_name must be a filename")
        object.__setattr__(self, "artifact_name", artifact_name)
        if artifact_name.casefold() in _FORBIDDEN_INSTALLER_NAMES:
            raise ValueError("command interpreters may not be used as installers")
        object.__setattr__(self, "artifact_sha256", _validate_sha256(self.artifact_sha256, "artifact_sha256"))
        if self.expected_executable is not None:
            object.__setattr__(self, "expected_executable", _validate_relative_windows_path(self.expected_executable, "expected_executable"))
            if PureWindowsPath(self.expected_executable).suffix.lower() != ".exe":
                raise ValueError("expected_executable must end in .exe")
        if self.expected_process_name is not None:
            object.__setattr__(self, "expected_process_name", _validate_process_name(self.expected_process_name))
        requirements = tuple(self.success_requirements)
        if not requirements or len(requirements) > len(_SUCCESS_REQUIREMENTS) or len(set(requirements)) != len(requirements):
            raise ValueError("success_requirements must be a non-empty unique bounded list")
        if set(requirements) - _SUCCESS_REQUIREMENTS:
            raise ValueError("success_requirements contains an unsupported requirement")
        object.__setattr__(self, "success_requirements", requirements)


@dataclass(frozen=True, slots=True)
class ApplicationTestRequest:
    artifact: Path
    expected_sha256: str
    installation_recipe: InstallationRecipe
    run_id: str = field(default_factory=new_run_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        artifact = validate_source_artifact(Path(self.artifact))
        object.__setattr__(self, "artifact", artifact)
        validate_run_id(self.run_id)
        expected_sha256 = _validate_sha256(self.expected_sha256, "expected_sha256")
        object.__setattr__(self, "expected_sha256", expected_sha256)
        if self.installation_recipe.artifact_name != artifact.name:
            raise ValueError("installation recipe artifact_name must match the selected artifact")
        if self.installation_recipe.artifact_sha256 != expected_sha256:
            raise ValueError("installation recipe hash must match expected_sha256")
        if sha256_file(artifact) != expected_sha256:
            raise ValueError("artifact SHA-256 does not match expected_sha256")
        validate_safe_metadata(self.metadata)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class InstallationPlan:
    schema_version: int
    supported: bool
    installer_kind: str
    controlled_executable: str | None
    argument_tokens: tuple[str, ...]
    expected_paths: tuple[str, ...]
    required_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    install_timeout_seconds: int
    launch_timeout_seconds: int
    expected_process_name: str | None
    expected_install_scope: str
    reboot_policy: str
    network_policy: str
    success_requirements: tuple[str, ...]
    profile_version: int
    dry_run: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != INSTALLATION_SCHEMA_VERSION or self.profile_version != INSTALLATION_PROFILE_VERSION:
            raise ValueError("installation plan schema or profile version is unsupported")
        if self.dry_run is not True:
            raise ValueError("Phase 2A plans must be dry-run only")
        kind = _enum_value(InstallerKind, self.installer_kind, "installer_kind")
        if self.expected_install_scope != InstallScope.USER.value:
            raise ValueError("installation plan must use user scope")
        if self.reboot_policy != RebootPolicy.FORBID.value or self.network_policy != NetworkPolicy.DISABLED.value:
            raise ValueError("installation plan must forbid reboot and disable network")
        if isinstance(self.install_timeout_seconds, bool) or not isinstance(self.install_timeout_seconds, int) or not MIN_INSTALL_TIMEOUT_SECONDS <= self.install_timeout_seconds <= MAX_INSTALL_TIMEOUT_SECONDS:
            raise ValueError("installation plan install timeout is invalid")
        if isinstance(self.launch_timeout_seconds, bool) or not isinstance(self.launch_timeout_seconds, int) or not MIN_LAUNCH_TIMEOUT_SECONDS <= self.launch_timeout_seconds <= MAX_LAUNCH_TIMEOUT_SECONDS:
            raise ValueError("installation plan launch timeout is invalid")
        if self.expected_process_name is not None:
            _validate_process_name(self.expected_process_name)
        requirements = tuple(self.success_requirements)
        if not requirements or len(set(requirements)) != len(requirements) or set(requirements) - _SUCCESS_REQUIREMENTS:
            raise ValueError("installation plan success requirements are invalid")
        object.__setattr__(self, "argument_tokens", tuple(self.argument_tokens))
        object.__setattr__(self, "expected_paths", tuple(self.expected_paths))
        object.__setattr__(self, "required_evidence", tuple(self.required_evidence))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "success_requirements", requirements)
        if self.required_evidence != _REQUIRED_EVIDENCE:
            raise ValueError("installation plan evidence contract is invalid")

        mapped_exe = str(PureWindowsPath(INPUT_DESTINATION) / "artifact.exe")
        mapped_msi = str(PureWindowsPath(INPUT_DESTINATION) / "artifact.msi")
        msi_log = str(PureWindowsPath(EVIDENCE_DESTINATION) / "installer.log")
        expected_shape = {
            InstallerKind.NSIS_EXE: (True, mapped_exe, ("/S",), ()),
            InstallerKind.MSI: (True, TRUSTED_MSIEXEC, ("/i", mapped_msi, "/qn", "/norestart", "/L*v", msi_log), ()),
            InstallerKind.PORTABLE_EXE: (True, mapped_exe, (), ("portable_profile_is_launch_only",)),
            InstallerKind.UNSUPPORTED: (False, None, (), ()),
        }[kind]
        if (self.supported, self.controlled_executable, self.argument_tokens, self.warnings) != expected_shape:
            raise ValueError("installation plan does not match its controlled installer profile")
        expected_blockers = ("installer_profile_is_unsupported",) if kind is InstallerKind.UNSUPPORTED else ()
        if self.blockers != expected_blockers:
            raise ValueError("installation plan blockers do not match its installer profile")
        if kind is InstallerKind.PORTABLE_EXE and self.expected_paths != (mapped_exe,):
            raise ValueError("portable plan expected path must be the mapped artifact")
        if kind is InstallerKind.UNSUPPORTED and self.expected_paths:
            raise ValueError("unsupported plan may not contain expected paths")
        if kind in {InstallerKind.NSIS_EXE, InstallerKind.MSI}:
            for expected_path in self.expected_paths:
                prefix = USER_INSTALL_ROOT + "\\"
                if not expected_path.startswith(prefix):
                    raise ValueError("installation plan expected path is outside the controlled user root")
                _validate_relative_windows_path(expected_path[len(prefix):], "expected_path")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_kind_compatibility(kind: InstallerKind, suffix: str) -> None:
    if suffix in _SCRIPT_EXTENSIONS:
        raise ValueError("script artifacts may not be used as installers")
    expected_suffix = {
        InstallerKind.NSIS_EXE: ".exe",
        InstallerKind.MSI: ".msi",
        InstallerKind.PORTABLE_EXE: ".exe",
    }.get(kind)
    if expected_suffix is not None and suffix != expected_suffix:
        raise ValueError(f"artifact extension is incompatible with {kind.value}")


def plan_installation(request: ApplicationTestRequest) -> InstallationPlan:
    recipe = request.installation_recipe
    kind = recipe.installer_kind
    suffix = request.artifact.suffix.lower()
    _validate_kind_compatibility(kind, suffix)
    if kind is InstallerKind.PORTABLE_EXE and recipe.expected_executable is not None:
        raise ValueError("portable_exe uses only the mapped artifact and does not accept expected_executable")
    staged_name = f"artifact{suffix}"
    mapped_artifact = str(PureWindowsPath(INPUT_DESTINATION) / staged_name)
    expected_paths: tuple[str, ...] = ()
    if recipe.expected_executable:
        expected_paths = (str(PureWindowsPath(USER_INSTALL_ROOT) / recipe.expected_executable),)

    common = {
        "schema_version": INSTALLATION_SCHEMA_VERSION,
        "expected_paths": expected_paths,
        "required_evidence": _REQUIRED_EVIDENCE,
        "install_timeout_seconds": recipe.install_timeout_seconds,
        "launch_timeout_seconds": recipe.launch_timeout_seconds,
        "expected_process_name": recipe.expected_process_name,
        "expected_install_scope": recipe.expected_install_scope.value,
        "reboot_policy": recipe.reboot_policy.value,
        "network_policy": recipe.network_policy.value,
        "success_requirements": recipe.success_requirements,
        "profile_version": recipe.profile_version,
    }
    if kind is InstallerKind.NSIS_EXE:
        return InstallationPlan(
            supported=True,
            installer_kind=kind.value,
            controlled_executable=mapped_artifact,
            argument_tokens=("/S",),
            blockers=(),
            warnings=(),
            **common,
        )
    if kind is InstallerKind.MSI:
        install_log = str(PureWindowsPath(EVIDENCE_DESTINATION) / "installer.log")
        return InstallationPlan(
            supported=True,
            installer_kind=kind.value,
            controlled_executable=TRUSTED_MSIEXEC,
            argument_tokens=("/i", mapped_artifact, "/qn", "/norestart", "/L*v", install_log),
            blockers=(),
            warnings=(),
            **common,
        )
    if kind is InstallerKind.PORTABLE_EXE:
        common["expected_paths"] = (mapped_artifact,)
        return InstallationPlan(
            supported=True,
            installer_kind=kind.value,
            controlled_executable=mapped_artifact,
            argument_tokens=(),
            blockers=(),
            warnings=("portable_profile_is_launch_only",),
            **common,
        )
    common["expected_paths"] = ()
    return InstallationPlan(
        supported=False,
        installer_kind=kind.value,
        controlled_executable=None,
        argument_tokens=(),
        blockers=("installer_profile_is_unsupported",),
        warnings=(),
        **common,
    )


def build_guest_request_v2(request: ApplicationTestRequest) -> dict[str, Any]:
    plan = plan_installation(request)
    return {
        "schema_version": INSTALLATION_SCHEMA_VERSION,
        "run_id": request.run_id,
        "artifact_name": f"artifact{request.artifact.suffix.lower()}",
        "artifact_sha256_host": request.expected_sha256,
        "installation_plan": plan.to_dict(),
    }


def build_initial_evidence_v2(request: ApplicationTestRequest) -> dict[str, Any]:
    plan = plan_installation(request)
    return {
        "schema_version": INSTALLATION_SCHEMA_VERSION,
        "run_id": request.run_id,
        "phase": GuestPhase.INSTALLATION_PLANNED.value,
        "outcome": None,
        "installer_kind": plan.installer_kind,
        "artifact_sha256_guest": None,
        "installer_started_at": None,
        "installer_finished_at": None,
        "installer_exit_code": None,
        "reboot_required": None,
        "install_log": None,
        "installed_executable_found": None,
        "launch_started_at": None,
        "launched_process": None,
        "first_launch_verified": None,
        "runtime_state": "not_started",
    }
