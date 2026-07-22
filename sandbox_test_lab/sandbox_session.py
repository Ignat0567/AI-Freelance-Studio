from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Callable

from .workspace import WorkspaceError, validate_source_artifact


CLIENT_DISCOVERY_TIMEOUT_SECONDS = 10.0
CLIENT_PROCESS_NAME = "WindowsSandboxClient.exe"


def _powershell_path() -> Path:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise WorkspaceError("windows_system_root_unavailable")
    return validate_source_artifact(
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    )


def _client_path() -> Path:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise WorkspaceError("windows_system_root_unavailable")
    return validate_source_artifact(Path(system_root) / "System32" / CLIENT_PROCESS_NAME)


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
            raise WorkspaceError("owned Sandbox client identity contains duplicate JSON keys")
        value[key] = item
    return value


@dataclass(frozen=True, slots=True)
class OwnedSandboxSession:
    launcher_pid: int
    client_pid: int
    client_start_ticks: int
    client_started_at: str
    client_path: str

    def request_close(self) -> None:
        self._control("close")

    def terminate(self) -> None:
        self._control("terminate")

    def _control(self, action: str) -> None:
        if action not in {"close", "terminate"}:
            raise ValueError("owned Sandbox client action is invalid")
        expected_path = self.client_path.replace("'", "''")
        operation = (
            "if (-not $process.CloseMainWindow()) { exit 14 }; exit 0"
            if action == "close"
            else "$process.Kill(); if (-not $process.WaitForExit(5000)) { exit 15 }; exit 0"
        )
        command = (
            f"$process = Get-Process -Id {self.client_pid} -ErrorAction Stop; "
            f"if ($process.StartTime.ToUniversalTime().Ticks -ne {self.client_start_ticks}) {{ exit 11 }}; "
            f"if ([IO.Path]::GetFullPath($process.Path) -ine '{expected_path}') {{ exit 12 }}; "
            f"{operation}"
        )
        result = subprocess.run(
            [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=_controlled_environment(),
        )
        if result.returncode != 0:
            raise WorkspaceError(f"owned_sandbox_client_{action}_failed")


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
    canonical_client = _client_path()
    expected_path = str(canonical_client).replace("'", "''")
    command = (
        f"$items = @(Get-CimInstance -Query \"SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process "
        f"WHERE ParentProcessId={launcher_pid} AND Name='{CLIENT_PROCESS_NAME}'\" -ErrorAction Stop); "
        "if ($items.Count -eq 0) { exit 10 }; if ($items.Count -ne 1) { exit 11 }; "
        "$item = $items[0]; $process = Get-Process -Id ([int]$item.ProcessId) -ErrorAction Stop; "
        f"if ([IO.Path]::GetFullPath($process.Path) -ine '{expected_path}') {{ exit 12 }}; "
        "$start = $process.StartTime.ToUniversalTime(); "
        "[ordered]@{process_id=[int]$item.ProcessId;parent_process_id=[int]$item.ParentProcessId;"
        "start_ticks=[long]$start.Ticks;started_at=$start.ToString('o');path=[string]$process.Path} "
        "| ConvertTo-Json -Compress"
    )
    deadline = monotonic() + CLIENT_DISCOVERY_TIMEOUT_SECONDS
    while True:
        result = subprocess.run(
            [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=_controlled_environment(),
        )
        if result.returncode == 0:
            break
        if result.returncode != 10 or monotonic() >= deadline:
            raise WorkspaceError("owned_sandbox_client_discovery_failed")
        sleeper(min(0.1, max(0.0, deadline - monotonic())))
    try:
        value = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("owned Sandbox client identity is invalid JSON") from exc
    expected_fields = {"process_id", "parent_process_id", "start_ticks", "started_at", "path"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise WorkspaceError("owned Sandbox client identity fields are invalid")
    integers = (value.get("process_id"), value.get("parent_process_id"), value.get("start_ticks"))
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in integers):
        raise WorkspaceError("owned Sandbox client process identity is invalid")
    if value["parent_process_id"] != launcher_pid:
        raise WorkspaceError("owned Sandbox client parent identity is invalid")
    if os.path.normcase(str(value.get("path"))) != os.path.normcase(str(canonical_client)):
        raise WorkspaceError("owned Sandbox client image identity is invalid")
    try:
        started_at = datetime.fromisoformat(str(value.get("started_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkspaceError("owned Sandbox client creation time is invalid") from exc
    launch_utc = launched_at.astimezone(timezone.utc)
    if started_at.tzinfo is None or not launch_utc - timedelta(seconds=2) <= started_at.astimezone(timezone.utc) <= datetime.now(timezone.utc) + timedelta(seconds=2):
        raise WorkspaceError("owned Sandbox client was not created by the current run")
    return OwnedSandboxSession(
        launcher_pid=launcher_pid,
        client_pid=int(value["process_id"]),
        client_start_ticks=int(value["start_ticks"]),
        client_started_at=str(value["started_at"]),
        client_path=str(canonical_client),
    )
