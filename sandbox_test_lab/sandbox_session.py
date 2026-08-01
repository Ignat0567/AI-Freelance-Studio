from __future__ import annotations

import base64
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

# Host-side screenshot of the owned Sandbox window: PrintWindow first, CopyFromScreen
# fallback on a blank/degenerate result -- the same technique already proven guest-side in
# Phase 3B, applied here to the window Windows Sandbox already renders on the host. $TargetPid/
# $StartTicks/$ExpectedPath/$MaxWidth are prepended by capture_window_png() via direct string
# interpolation, not CLI arguments -- `powershell.exe -Command <script-with-param()> -Arg value`
# does not reliably bind trailing arguments into a param() block (confirmed empirically
# 2026-07-31: an unbound [int] parameter silently defaults to 0 instead of erroring).
_CAPTURE_WINDOW_PNG_SCRIPT = r"""
$process = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
if (-not $process) { exit 1 }
if ($process.StartTime.ToUniversalTime().Ticks -ne $StartTicks) { exit 1 }
if ([IO.Path]::GetFullPath($process.Path) -ine $ExpectedPath) { exit 1 }
$hwnd = $process.MainWindowHandle
if ($hwnd -eq [IntPtr]::Zero) { exit 1 }

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace AifsCapture {
    public struct Rect { public int Left; public int Top; public int Right; public int Bottom; }

    public static class Win32 {
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out Rect rect);
        [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
    }
}
'@

$rect = New-Object AifsCapture.Rect
[AifsCapture.Win32]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) { exit 1 }

$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$hdc = $graphics.GetHdc()
$captured = [AifsCapture.Win32]::PrintWindow($hwnd, $hdc, 2)
$graphics.ReleaseHdc($hdc)

function Test-Blank($bmp) {
    $w = $bmp.Width; $h = $bmp.Height
    $samples = 0; $blackish = 0
    $stepX = [Math]::Max(1, [int]($w / 20))
    $stepY = [Math]::Max(1, [int]($h / 20))
    for ($x = 0; $x -lt $w; $x += $stepX) {
        for ($y = 0; $y -lt $h; $y += $stepY) {
            $px = $bmp.GetPixel($x, $y)
            $samples++
            if ($px.R -lt 8 -and $px.G -lt 8 -and $px.B -lt 8) { $blackish++ }
        }
    }
    return ($samples -gt 0 -and ($blackish / $samples) -gt 0.98)
}

if (-not $captured -or (Test-Blank $bitmap)) {
    $graphics.Dispose(); $bitmap.Dispose()
    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size($width, $height)))
}

if ($width -gt $MaxWidth) {
    $scale = $MaxWidth / [double]$width
    $newW = $MaxWidth
    $newH = [int]($height * $scale)
    $scaled = New-Object System.Drawing.Bitmap($newW, $newH)
    $g2 = [System.Drawing.Graphics]::FromImage($scaled)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g2.DrawImage($bitmap, 0, 0, $newW, $newH)
    $g2.Dispose()
    $bitmap.Dispose()
    $bitmap = $scaled
}

$ms = New-Object System.IO.MemoryStream
$bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$b64 = [Convert]::ToBase64String($ms.ToArray())
Write-Output "FRAME_B64_START"
Write-Output $b64
Write-Output "FRAME_B64_END"

$graphics.Dispose(); $bitmap.Dispose(); $ms.Dispose()
"""


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

    def is_running(self) -> bool:
        """Read-only, identity-verified liveness check. Never closes or kills anything.

        For remote_session, the server process is checked too: it does not always exit
        promptly when its client closes, so treating only the client/remote-session
        process as authoritative can declare a session over while its server is still
        alive (confirmed empirically 2026-07-31)."""
        if self.model == "legacy_client":
            return self._process_alive(self.client_pid, self.client_start_ticks, self.client_path)
        if self.model == "remote_session":
            if self._process_alive(self.remote_session_pid, self.remote_session_start_ticks, self.remote_session_path):
                return True
            if self.server_pid is not None:
                return self._process_alive(self.server_pid, self.server_start_ticks, self.server_path)
            return False
        return False

    def capture_window_png(self, *, max_width: int = 640, timeout: float = 20.0) -> bytes | None:
        """Best-effort, identity-verified host-side screenshot of the owned Sandbox window.

        Never raises -- returns None on any failure. This captures whatever Windows Sandbox
        is already rendering as a normal, visible host window; no guest script is involved.
        """
        if self.model == "legacy_client":
            pid, start_ticks, expected_path = self.client_pid, self.client_start_ticks, self.client_path
        elif self.model == "remote_session":
            pid, start_ticks, expected_path = (
                self.remote_session_pid, self.remote_session_start_ticks, self.remote_session_path,
            )
        else:
            return None
        if pid is None or start_ticks is None or expected_path is None:
            return None
        if isinstance(max_width, bool) or not isinstance(max_width, int) or not 100 <= max_width <= 3840:
            raise ValueError("max_width is invalid")
        expected_path_escaped = expected_path.replace("'", "''")
        header = (
            f"$TargetPid = {pid}\n"
            f"$StartTicks = {start_ticks}\n"
            f"$ExpectedPath = '{expected_path_escaped}'\n"
            f"$MaxWidth = {max_width}\n"
        )
        try:
            result = subprocess.run(
                [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", header + _CAPTURE_WINDOW_PNG_SCRIPT],
                shell=False, check=False, capture_output=True, text=True, timeout=timeout,
                env=_controlled_environment(),
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0 or "FRAME_B64_START" not in result.stdout:
            return None
        body = result.stdout.split("FRAME_B64_START", 1)[1].split("FRAME_B64_END", 1)[0].strip()
        if not body:
            return None
        try:
            return base64.b64decode(body, validate=True)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _process_alive(pid: int | None, start_ticks: int | None, expected_path: str | None) -> bool:
        if pid is None or start_ticks is None or expected_path is None:
            return False
        expected_path_escaped = expected_path.replace("'", "''")
        command = (
            f"$process = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
            f"if (-not $process) {{ exit 1 }}; "
            f"if ($process.StartTime.ToUniversalTime().Ticks -ne {start_ticks}) {{ exit 1 }}; "
            f"if ([IO.Path]::GetFullPath($process.Path) -ine '{expected_path_escaped}') {{ exit 1 }}; "
            f"exit 0"
        )
        try:
            result = subprocess.run(
                [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                shell=False, check=False, capture_output=True, text=True, timeout=10,
                env=_controlled_environment(),
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

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
