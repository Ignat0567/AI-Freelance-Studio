from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Callable, Sequence

from .models import SandboxCapability


MINIMUM_WINDOWS_BUILD = 18362
FEATURE_NAME = "Containers-DisposableClientVM"
FEATURE_STATES = {1: "enabled", 2: "disabled", 3: "absent", 4: "unknown"}


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None


def run_read_only_command(argv: Sequence[str], timeout: float = 10.0) -> CommandResult:
    if not argv or isinstance(argv, (str, bytes)):
        raise ValueError("argv must be a non-empty sequence")
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired:
        return CommandResult(None, timed_out=True, error="command_timeout")
    except (OSError, ValueError) as exc:
        return CommandResult(None, error=f"command_error:{type(exc).__name__}")


_CAPABILITY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$computer = Get-CimInstance -ClassName Win32_ComputerSystem
$feature = Get-CimInstance -ClassName Win32_OptionalFeature -Filter "Name='Containers-DisposableClientVM'" -ErrorAction SilentlyContinue
[ordered]@{
  edition = [string](Get-ItemPropertyValue -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name EditionID)
  build = [int]$os.BuildNumber
  feature_state = if ($null -eq $feature) { 3 } else { [int]$feature.InstallState }
  hypervisor_present = [bool]$computer.HypervisorPresent
} | ConvertTo-Json -Compress
""".strip()


def _supported_edition(edition: str | None) -> bool:
    if not edition:
        return False
    lowered = edition.lower()
    if "core" in lowered or "home" in lowered:
        return False
    return any(name in lowered for name in ("professional", "enterprise", "education", "serverrdsh"))


def _windows_system_directory(environ: dict[str, str], *, use_native_api: bool) -> Path | None:
    if use_native_api and os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
            if 0 < length < len(buffer):
                return Path(buffer.value)
        except (AttributeError, OSError, ValueError):
            pass
        return None
    windows = environ.get("SystemRoot") or environ.get("WINDIR")
    return Path(windows) / "System32" if windows else None


def _trusted_system_executable(system_directory: Path | None, *relative_parts: str) -> str | None:
    if system_directory is None:
        return None
    try:
        trusted_directory = system_directory.resolve(strict=True)
        if not trusted_directory.is_dir() or trusted_directory.is_symlink():
            return None
        candidate = trusted_directory.joinpath(*relative_parts)
        if not candidate.is_file() or candidate.is_symlink():
            return None
        attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
        if attributes & 0x400:
            return None
        resolved = candidate.resolve(strict=True)
        if resolved != candidate.absolute() or not resolved.is_relative_to(trusted_directory):
            return None
        return str(resolved)
    except (OSError, RuntimeError):
        return None


def detect_sandbox_capability(
    *,
    system_name: str | None = None,
    environ: dict[str, str] | None = None,
    command_runner: Callable[[Sequence[str], float], CommandResult] = run_read_only_command,
    command_timeout: float = 10.0,
) -> SandboxCapability:
    current_system = system_name or platform.system()
    if current_system != "Windows":
        return SandboxCapability(
            supported_os=False,
            windows_edition=None,
            windows_build=None,
            virtualization_available=None,
            sandbox_feature_state="unknown",
            executable_found=False,
            available=False,
            blockers=("unsupported_os",),
            warnings=(),
        )

    environment = dict(os.environ if environ is None else environ)
    system_directory = _windows_system_directory(environment, use_native_api=environ is None)
    executable_path = _trusted_system_executable(system_directory, "WindowsSandbox.exe")
    powershell = _trusted_system_executable(
        system_directory,
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    blockers: list[str] = []
    warnings: list[str] = []
    edition: str | None = None
    build: int | None = None
    feature_state = "unknown"
    virtualization: bool | None = None

    if not powershell:
        blockers.append("powershell_unavailable")
    else:
        result = command_runner(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _CAPABILITY_SCRIPT],
            command_timeout,
        )
        if result.timed_out:
            blockers.append("capability_query_timeout")
        elif result.returncode != 0:
            blockers.append("capability_query_failed")
        else:
            try:
                payload = json.loads(result.stdout)
                edition = str(payload.get("edition") or "") or None
                build = int(payload["build"]) if payload.get("build") is not None else None
                feature_state = FEATURE_STATES.get(int(payload.get("feature_state", 4)), "unknown")
                raw_virtualization = payload.get("hypervisor_present")
                virtualization = raw_virtualization if isinstance(raw_virtualization, bool) else None
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                blockers.append("capability_query_invalid")

    if not _supported_edition(edition):
        blockers.append("unsupported_windows_edition" if edition else "windows_edition_unknown")
    if build is None:
        blockers.append("windows_build_unknown")
    elif build < MINIMUM_WINDOWS_BUILD:
        blockers.append("windows_build_too_old")
    if feature_state != "enabled":
        blockers.append(f"sandbox_feature_{feature_state}")
    if virtualization is not True:
        blockers.append("virtualization_unavailable" if virtualization is False else "virtualization_unknown")
    if executable_path is None:
        blockers.append("sandbox_executable_missing")
    if feature_state == "enabled" and executable_path is None:
        warnings.append("sandbox_feature_enabled_without_executable")

    blockers = list(dict.fromkeys(blockers))
    return SandboxCapability(
        supported_os=True,
        windows_edition=edition,
        windows_build=build,
        virtualization_available=virtualization,
        sandbox_feature_state=feature_state,
        executable_found=executable_path is not None,
        executable_path=executable_path,
        powershell_found=powershell is not None,
        available=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
