from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Callable

from .sandbox_process_model import (
    LEGACY_CLIENT_NAME,
    REMOTE_SESSION_NAME,
    SERVER_NAME,
    SandboxProcessModel,
    _is_trusted_path,
    build_process_identity,
    is_trusted_system32_path,
    is_trusted_windows_apps_path,
    normalize_windows_path,
)
from .workspace import WorkspaceError, validate_source_artifact


CLIENT_DISCOVERY_TIMEOUT_SECONDS = 10.0
SERVER_DISCOVERY_TIMEOUT_SECONDS = 5.0
CLIENT_PROCESS_NAME = LEGACY_CLIENT_NAME
REMOTE_SESSION_PROCESS_NAME = REMOTE_SESSION_NAME
SERVER_PROCESS_NAME = SERVER_NAME


def _powershell_path() -> Path:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise WorkspaceError("windows_system_root_unavailable")
    return validate_source_artifact(
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    )


def _system32_process_path(name: str) -> Path:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise WorkspaceError("windows_system_root_unavailable")
    return validate_source_artifact(Path(system_root) / "System32" / name)


def _controlled_environment() -> dict[str, str]:
    return {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise WorkspaceError("owned Sandbox process identity contains duplicate JSON keys")
        value[key] = item
    return value


def _parse_process_json(value: dict[str, object], *, expected_parent_pid: int, expected_name: str, launched_at: datetime) -> dict[str, object]:
    expected_fields = {"process_id", "parent_process_id", "start_ticks", "started_at", "path"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise WorkspaceError(f"owned Sandbox {expected_name} identity fields are invalid")
    integers = (value.get("process_id"), value.get("parent_process_id"), value.get("start_ticks"))
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in integers):
        raise WorkspaceError(f"owned Sandbox {expected_name} process identity is invalid")
    if value["parent_process_id"] != expected_parent_pid:
        raise WorkspaceError(f"owned Sandbox {expected_name} parent identity is invalid")
    path_str = str(value.get("path", ""))
    if not _is_trusted_path(path_str, expected_name):
        raise WorkspaceError(f"owned Sandbox {expected_name} image identity is invalid")
    try:
        started_at = datetime.fromisoformat(str(value.get("started_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkspaceError(f"owned Sandbox {expected_name} creation time is invalid") from exc
    launch_utc = launched_at.astimezone(timezone.utc)
    if started_at.tzinfo is None or not launch_utc - timedelta(seconds=2) <= started_at.astimezone(timezone.utc) <= datetime.now(timezone.utc) + timedelta(seconds=2):
        raise WorkspaceError(f"owned Sandbox {expected_name} was not created by the current run")
    return value


def _query_child_process_powershell(
    launcher_pid: int,
    child_name: str,
    *,
    poll: bool = False,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object] | None:
    command = (
        f"$items = @(Get-CimInstance -Query \"SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process "
        f"WHERE Name='{child_name}' AND ParentProcessId={launcher_pid}\" -ErrorAction Stop); "
        f"if ($items.Count -eq 0) {{ exit 10 }}; "
        f"$item = $items[0]; $process = Get-Process -Id ([int]$item.ProcessId) -ErrorAction Stop; "
        "$start = $process.StartTime.ToUniversalTime(); "
        "[ordered]@{process_id=[int]$item.ProcessId;parent_process_id=[int]$item.ParentProcessId;"
        "start_ticks=[long]$start.Ticks;started_at=$start.ToString('o');path=[string]$process.Path} "
        "| ConvertTo-Json -Compress"
    )
    while True:
        try:
            result = subprocess.run(
                [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                shell=False, check=False, capture_output=True, text=True, timeout=15,
                env=_controlled_environment(),
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode == 0:
            try:
                return json.loads(result.stdout, object_pairs_hook=_strict_object)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise WorkspaceError(f"owned Sandbox {child_name} identity is invalid JSON") from exc
        if result.returncode != 10:
            return None
        if not poll or (deadline is not None and monotonic() >= deadline):
            return None
        sleeper(min(0.1, max(0.0, (deadline or 0.0) - monotonic())))


@dataclass(frozen=True, slots=True)
class OwnedSandboxSession:
    model: str
    launcher_pid: int
    client_pid: int | None = None
    client_start_ticks: int | None = None
    client_started_at: str | None = None
    client_path: str | None = None
    remote_session_pid: int | None = None
    remote_session_start_ticks: int | None = None
    remote_session_path: str | None = None
    server_pid: int | None = None
    server_start_ticks: int | None = None
    server_path: str | None = None

    def __post_init__(self) -> None:
        if self.model == "legacy_client":
            if self.client_pid is None or self.client_start_ticks is None or self.client_path is None:
                raise WorkspaceError("owned Sandbox legacy client identity is incomplete")
        elif self.model == "remote_session":
            if self.remote_session_pid is None or self.remote_session_start_ticks is None or self.remote_session_path is None:
                raise WorkspaceError("owned Sandbox remote session identity is incomplete")

    def request_close(self) -> None:
        if self.model == "legacy_client":
            self._control_client("close")
        elif self.model == "remote_session":
            self._control_remote_session("close")

    def terminate(self) -> None:
        if self.model == "legacy_client":
            self._control_client("terminate")
        elif self.model == "remote_session":
            self._control_remote_session("terminate")

    def terminate_server(self) -> None:
        if self.model != "remote_session" or self.server_pid is None or self.server_start_ticks is None or self.server_path is None:
            return
        self._control_process(
            pid=self.server_pid,
            start_ticks=self.server_start_ticks,
            expected_path=self.server_path,
            label="server",
            action="terminate",
        )

    def _control_client(self, action: str) -> None:
        if self.client_pid is None or self.client_start_ticks is None or self.client_path is None:
            raise WorkspaceError("owned_sandbox_client_missing")
        self._control_process(
            pid=self.client_pid,
            start_ticks=self.client_start_ticks,
            expected_path=self.client_path,
            label="client",
            action=action,
        )

    def _control_remote_session(self, action: str) -> None:
        if self.remote_session_pid is None or self.remote_session_start_ticks is None or self.remote_session_path is None:
            raise WorkspaceError("owned_sandbox_remote_session_missing")
        self._control_process(
            pid=self.remote_session_pid,
            start_ticks=self.remote_session_start_ticks,
            expected_path=self.remote_session_path,
            label="remote_session",
            action=action,
        )

    def _control_process(self, pid: int, start_ticks: int, expected_path: str, label: str, action: str) -> None:
        if action not in {"close", "terminate"}:
            raise ValueError(f"owned Sandbox {label} action is invalid")
        expected_path_escaped = expected_path.replace("'", "''")
        operation = (
            "if (-not $process.CloseMainWindow()) { exit 14 }; exit 0"
            if action == "close"
            else "$process.Kill(); if (-not $process.WaitForExit(5000)) { exit 15 }; exit 0"
        )
        command = (
            f"$process = Get-Process -Id {pid} -ErrorAction Stop; "
            f"if ($process.StartTime.ToUniversalTime().Ticks -ne {start_ticks}) {{ exit 11 }}; "
            f"if ([IO.Path]::GetFullPath($process.Path) -ine '{expected_path_escaped}') {{ exit 12 }}; "
            f"{operation}"
        )
        try:
            result = subprocess.run(
                [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                shell=False, check=False, capture_output=True, text=True, timeout=10,
                env=_controlled_environment(),
            )
        except subprocess.TimeoutExpired:
            raise WorkspaceError(f"owned_sandbox_{label}_{action}_timeout")
        if result.returncode in (11, 12):
            raise WorkspaceError(f"owned_sandbox_{label}_identity_rejected")
        if result.returncode not in (0,):
            if action == "close" and result.returncode == 14:
                pass
            elif action == "terminate" and result.returncode == 15:
                raise WorkspaceError(f"owned_sandbox_{label}_terminate_wait_timeout")
            else:
                return


def capture_owned_sandbox_session(
    launcher_pid: int,
    launched_at: datetime,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> OwnedSandboxSession:
    if isinstance(launcher_pid, bool) or not isinstance(launcher_pid, int) or launcher_pid <= 0:
        raise WorkspaceError("owned Sandbox launcher PID is invalid")
    if launched_at.tzinfo is None:
        raise WorkspaceError("owned Sandbox launch time must include a timezone")

    deadline = monotonic() + CLIENT_DISCOVERY_TIMEOUT_SECONDS

    while True:
        client_data = _query_child_process_powershell(
            launcher_pid, CLIENT_PROCESS_NAME,
            poll=True, deadline=deadline, monotonic=monotonic, sleeper=sleeper,
        )
        if client_data is not None:
            parsed = _parse_process_json(
                client_data, expected_parent_pid=launcher_pid,
                expected_name=CLIENT_PROCESS_NAME, launched_at=launched_at,
            )
            return OwnedSandboxSession(
                model="legacy_client",
                launcher_pid=launcher_pid,
                client_pid=int(parsed["process_id"]),
                client_start_ticks=int(parsed["start_ticks"]),
                client_started_at=str(parsed["started_at"]),
                client_path=str(parsed["path"]),
            )

        remote_data = _query_child_process_powershell(
            launcher_pid, REMOTE_SESSION_PROCESS_NAME,
            poll=True, deadline=deadline, monotonic=monotonic, sleeper=sleeper,
        )
        if remote_data is not None:
            parsed_rs = _parse_process_json(
                remote_data, expected_parent_pid=launcher_pid,
                expected_name=REMOTE_SESSION_PROCESS_NAME, launched_at=launched_at,
            )
            rs_pid = int(parsed_rs["process_id"])

            server_deadline = monotonic() + SERVER_DISCOVERY_TIMEOUT_SECONDS
            server_data = _query_child_process_powershell(
                rs_pid, SERVER_PROCESS_NAME,
                poll=True, deadline=server_deadline, monotonic=monotonic, sleeper=sleeper,
            )
            if server_data is not None:
                parsed_sv = _parse_process_json(
                    server_data, expected_parent_pid=rs_pid,
                    expected_name=SERVER_PROCESS_NAME, launched_at=launched_at,
                )
                return OwnedSandboxSession(
                    model="remote_session",
                    launcher_pid=launcher_pid,
                    remote_session_pid=rs_pid,
                    remote_session_start_ticks=int(parsed_rs["start_ticks"]),
                    remote_session_path=str(parsed_rs["path"]),
                    server_pid=int(parsed_sv["process_id"]),
                    server_start_ticks=int(parsed_sv["start_ticks"]),
                    server_path=str(parsed_sv["path"]),
                )
            return OwnedSandboxSession(
                model="remote_session",
                launcher_pid=launcher_pid,
                remote_session_pid=rs_pid,
                remote_session_start_ticks=int(parsed_rs["start_ticks"]),
                remote_session_path=str(parsed_rs["path"]),
            )

        if monotonic() >= deadline:
            raise WorkspaceError("owned_sandbox_client_discovery_failed")
        sleeper(min(0.1, max(0.0, deadline - monotonic())))
