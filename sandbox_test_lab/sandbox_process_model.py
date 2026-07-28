from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import ntpath
import os
from pathlib import Path


SANDBOX_LAUNCHER_NAME = "WindowsSandbox.exe"
LEGACY_CLIENT_NAME = "WindowsSandboxClient.exe"
REMOTE_SESSION_NAME = "WindowsSandboxRemoteSession.exe"
SERVER_NAME = "WindowsSandboxServer.exe"

ALL_SANDBOX_PROCESS_NAMES = frozenset({
    SANDBOX_LAUNCHER_NAME,
    LEGACY_CLIENT_NAME,
    REMOTE_SESSION_NAME,
    SERVER_NAME,
})


class SandboxProcessModel(str, Enum):
    LEGACY_CLIENT = "legacy_client"
    REMOTE_SESSION = "remote_session"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"

    def sanitized(self) -> str:
        return self.value

    @classmethod
    def _missing_(cls, value: object) -> SandboxProcessModel:
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Immutable identity for a single sandbox process.

    Authoritative: pid + start_ticks + trusted_path
    Supporting: parent_pid, started_at
    Informational: process_name, session_id
    Unreliable: command_line alone, creation_date alone
    """
    pid: int
    parent_pid: int
    start_ticks: int
    started_at: str
    path: str
    process_name: str
    trusted: bool

    def __post_init__(self) -> None:
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if isinstance(self.parent_pid, bool) or not isinstance(self.parent_pid, int) or self.parent_pid <= 0:
            raise ValueError("parent_pid must be a positive integer")
        if isinstance(self.start_ticks, bool) or not isinstance(self.start_ticks, int) or self.start_ticks <= 0:
            raise ValueError("start_ticks must be a positive integer")
        if not self.started_at or not isinstance(self.started_at, str):
            raise ValueError("started_at must be a non-empty string")
        if not self.path or not isinstance(self.path, str):
            raise ValueError("path must be a non-empty string")
        if not self.process_name or not isinstance(self.process_name, str):
            raise ValueError("process_name must be a non-empty string")
        if type(self.trusted) is not bool:
            raise ValueError("trusted must be boolean")

    @property
    def parsed_started_at(self) -> datetime:
        return datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))


def normalize_windows_path(path: str) -> str:
    if not path or not isinstance(path, str):
        return ""
    return ntpath.normcase(ntpath.normpath(path)).rstrip("\\")


def is_trusted_system32_path(path: str, process_name: str) -> bool:
    """Validate that path is a regular file in a trusted system32 location."""
    normalized = normalize_windows_path(path)
    if not normalized:
        return False
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return False
    canonical_system32 = normalize_windows_path(ntpath.join(system_root, "System32"))
    canonical_syswow64 = normalize_windows_path(ntpath.join(system_root, "SysWOW64"))
    basename = ntpath.basename(normalized).lower()
    expected_name = process_name.lower()
    if basename != expected_name:
        return False
    try:
        common_system32 = ntpath.commonpath((normalized, canonical_system32))
        if common_system32 == canonical_system32:
            return True
    except ValueError:
        pass
    try:
        common_syswow64 = ntpath.commonpath((normalized, canonical_syswow64))
        if common_syswow64 == canonical_syswow64:
            return True
    except ValueError:
        pass
    return False


def is_trusted_windows_apps_path(path: str, process_name: str) -> bool:
    """Validate that path is a known sandbox process under WindowsApps.

    Only RemoteSession and Server may be accepted from WindowsApps;
    LegacyClient is always expected in System32.
    """
    normalized = normalize_windows_path(path)
    if not normalized:
        return False
    if process_name.lower() not in (REMOTE_SESSION_NAME.lower(), SERVER_NAME.lower()):
        return False
    program_files = os.environ.get("ProgramFiles") or os.environ.get("ProgramW6432")
    if not program_files:
        return False
    windows_apps = normalize_windows_path(ntpath.join(program_files, "WindowsApps"))
    basename = ntpath.basename(normalized).lower()
    expected_name = process_name.lower()
    if basename != expected_name:
        return False
    if not normalized.lower().startswith(windows_apps.lower()):
        return False
    parent = normalize_windows_path(ntpath.dirname(normalized))
    if "microsoftwindows.windowssandbox_" not in parent.lower():
        return False
    return True


def validate_launched_after(identity: ProcessIdentity, launched_at: datetime, *, margin_seconds: float = 2.0) -> bool:
    """Check that a process was created after the launch boundary."""
    started = identity.parsed_started_at
    if started.tzinfo is None:
        return False
    launch_utc = launched_at.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    lower_bound = launch_utc - timedelta(seconds=margin_seconds)
    upper_bound = now_utc + timedelta(seconds=margin_seconds)
    started_utc = started.astimezone(timezone.utc)
    return lower_bound <= started_utc <= upper_bound


def validate_parent_chain(identity: ProcessIdentity, expected_parent_pid: int) -> bool:
    """Verify that the process has the expected direct parent PID."""
    return identity.parent_pid == expected_parent_pid


def validate_identity_stable(
    identity: ProcessIdentity,
    pid: int,
    start_ticks: int,
    path: str,
) -> bool:
    """Detect PID reuse by checking the identity tuple."""
    if identity.pid != pid:
        return False
    if identity.start_ticks != start_ticks:
        return False
    if normalize_windows_path(identity.path) != normalize_windows_path(path):
        return False
    return True


def _is_trusted_path(path: str, process_name: str) -> bool:
    if is_trusted_system32_path(path, process_name):
        return True
    if process_name.lower() in (REMOTE_SESSION_NAME.lower(), SERVER_NAME.lower()):
        if is_trusted_windows_apps_path(path, process_name):
            return True
    return False


def build_process_identity(
    pid: int,
    parent_pid: int,
    start_ticks: int,
    started_at: str,
    path: str,
    process_name: str,
    *,
    launched_at: datetime | None = None,
    expected_parent_pid: int | None = None,
) -> ProcessIdentity:
    trusted = _is_trusted_path(path, process_name)
    identity = ProcessIdentity(
        pid=pid,
        parent_pid=parent_pid,
        start_ticks=start_ticks,
        started_at=started_at,
        path=path,
        process_name=process_name,
        trusted=trusted,
    )
    if launched_at is not None and not validate_launched_after(identity, launched_at):
        raise ValueError("process was not created by the current run")
    if expected_parent_pid is not None and not validate_parent_chain(identity, expected_parent_pid):
        raise ValueError("process parent identity is invalid")
    if not trusted:
        raise ValueError("process executable path is untrusted")
    return identity
