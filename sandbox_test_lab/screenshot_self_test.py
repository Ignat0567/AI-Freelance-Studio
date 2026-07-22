from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
from typing import Any, Mapping
import zlib

from . import production_self_test as phase3a
from .models import SandboxRunPaths, validate_run_id
from .production_evidence import ProductionEvidenceError, ValidatedProductionEvidence, validate_production_evidence_directory
from .production_self_test import ProductionSelfTestRequest, ProductionSelfTestWorkspaceManager, build_production_guest_request, validate_trusted_production_artifact
from .workspace import REPARSE_POINT_ATTRIBUTE, SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file


SCREENSHOT_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_SCREENSHOT_EXTERNAL"
SCREENSHOT_SCHEMA_VERSION = 1
SCREENSHOT_PROTOCOL = "aifs_production_window_screenshot_v1"
SCREENSHOT_FILENAME = "production-main-window.png"
SCREENSHOT_MANIFEST_FILENAME = "screenshot-evidence.json"
STARTUP_MARKER_SCHEMA_VERSION = 1
ENTRY_SCRIPT_FILENAME = "production_screenshot_entry.ps1"
PAYLOAD_SCRIPT_FILENAME = "production_screenshot_self_test.ps1"
PAYLOAD_SHA256 = "fcce845efe5e54521a57eee79ac845c88db8339797f9a5ecd6d7381f856b110e"
ENTRY_STARTUP_STAGES = (
    "entry_script_started",
    "request_found",
    "payload_found",
    "payload_hash_verified",
    "payload_process_started",
)
PAYLOAD_STARTUP_STAGES = (
    "payload_interpreter_entered",
    "core_functions_loading_started",
    "core_functions_loading_completed",
    "production_api_add_type_started",
    "production_api_add_type_completed",
    "drawing_assembly_loading_started",
    "drawing_assembly_loading_completed",
    "screenshot_api_add_type_started",
    "screenshot_api_add_type_completed",
    "request_loading_started",
    "request_loading_completed",
    "initialization_completed",
    "production_preflight_started",
    "installation_started",
    "screenshot_started",
    "completed",
)
MAX_SCREENSHOT_BYTES = 32 * 1024 * 1024
MIN_SCREENSHOT_WIDTH = 320
MIN_SCREENSHOT_HEIGHT = 200
MAX_SCREENSHOT_WIDTH = 7680
MAX_SCREENSHOT_HEIGHT = 4320
PROFILE_NAME = phase3a.TRUSTED_PROFILE_NAME

_MANIFEST_FIELDS = {
    "schema_version", "protocol", "run_id", "profile", "captured_at", "source",
    "owned_window_verified", "owner_pid", "hwnd_present", "exact_title_verified", "window_visible",
    "window_bounds", "client_bounds", "dpi_scale", "image_filename", "image_format", "width", "height",
    "file_size", "sha256", "capture_method", "blank_check", "uniformity_check", "image_summary",
    "validation_status", "errors", "warnings",
}
_BOUNDS_FIELDS = {"x", "y", "width", "height"}
_SUMMARY_FIELDS = {
    "blank", "uniform", "transparent", "black", "white", "total_pixels", "visible_pixels",
    "mean_luminance", "min_luminance", "max_luminance", "luminance_range", "luminance_variance",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ATOMIC_TEMPORARY = re.compile(
    r"^(status|heartbeat|completion|guest-system|production-evidence|screenshot-evidence|entry-[a-z_]+|payload-[a-z_]+|payload-load-probe-completion)\.json\.[0-9a-f]{32}\.tmp$"
)
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_]{1,192}$")


class ScreenshotEvidenceError(ValueError):
    pass


_PAYLOAD_PRELUDE = r'''param()
& {
    $MarkerRunId = [string]$env:AIFS_PHASE3B_RUN_ID
    $MarkerRoot = "C:\SandboxTestLab\Evidence"
    $MarkerPath = Join-Path $MarkerRoot "payload-payload_interpreter_entered.json"
    $MarkerTemporary = "$MarkerPath.$([Guid]::NewGuid().ToString('N')).tmp"
    $MarkerUtf8 = New-Object System.Text.UTF8Encoding($false)
    $Marker = [ordered]@{ run_id = $MarkerRunId; stage = "payload_interpreter_entered"; timestamp = [DateTime]::UtcNow.ToString("o"); result = "entered" }
    [IO.File]::WriteAllText($MarkerTemporary, ($Marker | ConvertTo-Json -Compress), $MarkerUtf8)
    Move-Item -LiteralPath $MarkerTemporary -Destination $MarkerPath -Force
}
$PayloadRequestPath = "C:\SandboxTestLab\Guest\request.json"
$PayloadEvidenceRoot = "C:\SandboxTestLab\Evidence"
$PayloadRunId = [string]$env:AIFS_PHASE3B_RUN_ID
$PayloadUtf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-PayloadAtomicJson {
    param([string] $Path, [object] $Value)
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 4 -Compress), $PayloadUtf8NoBom)
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}
function Write-PayloadStartupMarker {
    param([string] $Stage, [string] $MarkerRunId, [string] $Result = "completed")
    $Path = Join-Path $PayloadEvidenceRoot "payload-$Stage.json"
    if (Test-Path -LiteralPath $Path) { throw "payload_stage_already_exists" }
    Write-PayloadAtomicJson $Path ([ordered]@{ run_id = $MarkerRunId; stage = $Stage; timestamp = [DateTime]::UtcNow.ToString("o"); result = $Result })
}
function Convert-PayloadSafeCode {
    param([object] $Value)
    $Code = ([string]$Value -replace "[^A-Za-z0-9_.]", "_")
    if ($Code.Length -gt 128) { return $Code.Substring(0, 128) }
    return $Code
}
function Write-PayloadInitializationFailure {
    param([string] $Stage, [object] $Failure, [string] $Reason, [int] $ExitCode)
    $HResult = $null
    try { $HResult = [int]$Failure.Exception.HResult } catch { }
    $ExceptionType = Convert-PayloadSafeCode ([string]$Failure.Exception.GetType().FullName)
    $ErrorId = Convert-PayloadSafeCode ([string]$Failure.FullyQualifiedErrorId)
    $Category = Convert-PayloadSafeCode ([string]$Failure.CategoryInfo.Category)
    Write-PayloadAtomicJson (Join-Path $PayloadEvidenceRoot "payload-initialization-failure.json") ([ordered]@{
        schema_version = 1
        run_id = $PayloadRunId
        stage = $Stage
        exception_type = $ExceptionType
        fully_qualified_error_id = $ErrorId
        category = $Category
        hresult = $HResult
        exit_code = $ExitCode
        timestamp = [DateTime]::UtcNow.ToString("o")
        reason = $Reason
        normalized_message = $Reason
    })
}
Write-PayloadStartupMarker "core_functions_loading_started" $PayloadRunId "started"
'''


_PAYLOAD_REQUEST_INSERTION = r'''
Write-PayloadStartupMarker "request_loading_started" $PayloadRunId "started"
try {
    $PayloadRequest = Get-Content -LiteralPath $PayloadRequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $PayloadParsedRunId = [Guid]::Empty
    if (-not [Guid]::TryParse($PayloadRunId, [ref]$PayloadParsedRunId) -or $PayloadParsedRunId.ToString() -cne $PayloadRunId -or [string]$PayloadRequest.run_id -cne $PayloadRunId) { throw "request_run_id_invalid" }
    if ($PayloadRequest.schema_version -ne 3 -or [string]$PayloadRequest.protocol -cne "aifs_production_self_test_v1") { throw "request_identity_invalid" }
    if ([string]$PayloadRequest.execution_mode -notin @("production_screenshot", "payload_load_probe")) { throw "execution_mode_invalid" }
    if ([string]$PayloadRequest.payload_sha256 -notmatch "^[0-9a-f]{64}$") { throw "payload_hash_invalid" }
} catch {
    Write-PayloadInitializationFailure "request_loading_started" $_ "request_initialization_failed" 24
    exit 24
}
Write-PayloadStartupMarker "request_loading_completed" $PayloadRunId
Write-PayloadStartupMarker "initialization_completed" $PayloadRunId
if ([string]$PayloadRequest.execution_mode -ceq "payload_load_probe") {
    Write-PayloadStartupMarker "completed" $PayloadRunId
    Write-PayloadAtomicJson (Join-Path $PayloadEvidenceRoot "payload-load-probe-completion.json") ([ordered]@{
        schema_version = 1; run_id = $PayloadRunId; status = "passed"; timestamp = [DateTime]::UtcNow.ToString("o")
    })
    exit 0
}
'''


@dataclass(frozen=True, slots=True)
class ValidatedScreenshotEvidence:
    production: ValidatedProductionEvidence
    screenshot: dict[str, Any]
    host_image_summary: dict[str, Any]
    png_sha256: str
    png_size: int
    width: int
    height: int


class ScreenshotSelfTestRequest(ProductionSelfTestRequest):
    """Phase 3B deliberately exposes only the immutable Phase 3A request surface."""


def production_capture_prerequisites_met(evidence: Mapping[str, Any]) -> bool:
    """Independently require every production condition needed before capture."""
    diagnostics = evidence.get("owned_descendant_diagnostics")
    owner_pid = evidence.get("window_owner_pid")
    return (
        evidence.get("installation_passed") is True
        and evidence.get("installed_exe_found") is True
        and evidence.get("installed_exe_regular") is True
        and evidence.get("installed_exe_non_reparse") is True
        and isinstance(evidence.get("installed_exe_sha256"), str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", evidence.get("installed_exe_sha256", "")))
        and evidence.get("install_root_verified") is True
        and isinstance(evidence.get("owned_tree_count"), int)
        and not isinstance(evidence.get("owned_tree_count"), bool)
        and evidence.get("owned_tree_count", 0) > 0
        and evidence.get("all_images_contained") is True
        and evidence.get("root_process_retained") is True
        and evidence.get("window_owner_in_owned_tree") is True
        and isinstance(owner_pid, int) and not isinstance(owner_pid, bool) and owner_pid > 0
        and evidence.get("window_title") == phase3a.WINDOW_TITLE
        and evidence.get("window_visible") is True
        and isinstance(evidence.get("stable_duration_seconds"), (int, float))
        and not isinstance(evidence.get("stable_duration_seconds"), bool)
        and evidence.get("stable_duration_seconds", 0) >= phase3a.STABLE_DURATION_SECONDS
        and evidence.get("first_launch_verified") is True
        and evidence.get("gui_passed") is True
        and evidence.get("backend_status") == "ready"
        and evidence.get("backend_process_tracked") is True
        and evidence.get("backend_image_contained") is True
        and evidence.get("backend_endpoint_verified") is True
        and evidence.get("backend_pid_ownership_verified") is True
        and evidence.get("backend_image_verified") is True
        and isinstance(diagnostics, list) and len(diagnostics) == evidence.get("owned_tree_count")
        and any(item.get("eligible_for_gui_verification") is True for item in diagnostics if isinstance(item, dict))
    )


def window_capture_state_is_valid(state: Mapping[str, Any]) -> bool:
    """Pure helper for mocked tests of the immediate window capture gate."""
    bounds = state.get("window_bounds")
    client = state.get("client_bounds")
    virtual = state.get("virtual_bounds")
    if not all(isinstance(value, Mapping) for value in (bounds, client, virtual)):
        return False
    if any(set(value) != _BOUNDS_FIELDS for value in (bounds, client, virtual)):
        return False
    if any(isinstance(value[key], bool) or not isinstance(value[key], int) for value in (bounds, client, virtual) for key in _BOUNDS_FIELDS):
        return False
    positive_reasonable = (
        MIN_SCREENSHOT_WIDTH <= bounds["width"] <= MAX_SCREENSHOT_WIDTH
        and MIN_SCREENSHOT_HEIGHT <= bounds["height"] <= MAX_SCREENSHOT_HEIGHT
        and 0 < client["width"] <= bounds["width"]
        and 0 < client["height"] <= bounds["height"]
    )
    contained = (
        bounds["x"] >= virtual["x"] and bounds["y"] >= virtual["y"]
        and bounds["x"] + bounds["width"] <= virtual["x"] + virtual["width"]
        and bounds["y"] + bounds["height"] <= virtual["y"] + virtual["height"]
    )
    return (
        state.get("hwnd_exists") is True
        and state.get("owner_in_owned_tree") is True
        and state.get("owner_creation_verified") is True
        and state.get("owner_gui_eligible") is True
        and state.get("visible") is True
        and state.get("minimized") is False
        and state.get("exact_title") is True
        and state.get("owner_image_verified") is True
        and state.get("owner_image_contained") is True
        and state.get("owner_image_basename") == phase3a.INSTALLED_EXE_RELATIVE
        and positive_reasonable and contained
    )


_API_INSERTION = r'''

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;

public sealed class AifsCaptureResult {
    public string Method;
    public int[] WindowBounds;
    public int[] ClientBounds;
    public double DpiScale;
    public bool Blank;
    public bool Uniform;
    public bool Transparent;
    public bool Black;
    public bool White;
    public long TotalPixels;
    public long VisiblePixels;
    public double Mean;
    public double Minimum;
    public double Maximum;
    public double Range;
    public double Variance;
}

public static class AifsScreenshotApi {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr hwnd, ref POINT point);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll", SetLastError=true)] private static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr hwnd);

    private static int[] Bounds(RECT value) { return new int[] { value.Left, value.Top, value.Right - value.Left, value.Bottom - value.Top }; }
    private static bool Same(int[] a, int[] b) {
        return a != null && b != null && a.Length == 4 && b.Length == 4 && a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
    }
    public static int[] GetVirtualBounds() { return new int[] { GetSystemMetrics(76), GetSystemMetrics(77), GetSystemMetrics(78), GetSystemMetrics(79) }; }

    public static bool ValidateWindow(IntPtr hwnd, int ownerPid, string exactTitle, int[] expectedWindow, int[] expectedClient, bool requireForeground, out int[] windowBounds, out int[] clientBounds, out double dpiScale) {
        windowBounds = null; clientBounds = null; dpiScale = 0.0;
        try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch { }
        if (hwnd == IntPtr.Zero || !IsWindow(hwnd) || !IsWindowVisible(hwnd) || IsIconic(hwnd)) return false;
        uint actualOwner; GetWindowThreadProcessId(hwnd, out actualOwner);
        if (actualOwner != (uint)ownerPid) return false;
        StringBuilder title = new StringBuilder(512); GetWindowText(hwnd, title, title.Capacity);
        if (!String.Equals(title.ToString(), exactTitle, StringComparison.Ordinal)) return false;
        RECT windowRect, clientRect; POINT clientOrigin = new POINT();
        if (!GetWindowRect(hwnd, out windowRect) || !GetClientRect(hwnd, out clientRect) || !ClientToScreen(hwnd, ref clientOrigin)) return false;
        windowBounds = Bounds(windowRect);
        clientBounds = new int[] { clientOrigin.X, clientOrigin.Y, clientRect.Right - clientRect.Left, clientRect.Bottom - clientRect.Top };
        if (windowBounds[2] < 320 || windowBounds[3] < 200 || windowBounds[2] > 7680 || windowBounds[3] > 4320 || clientBounds[2] <= 0 || clientBounds[3] <= 0 || clientBounds[2] > windowBounds[2] || clientBounds[3] > windowBounds[3]) return false;
        int[] virtualBounds = GetVirtualBounds();
        if (windowBounds[0] < virtualBounds[0] || windowBounds[1] < virtualBounds[1] || windowBounds[0] + windowBounds[2] > virtualBounds[0] + virtualBounds[2] || windowBounds[1] + windowBounds[3] > virtualBounds[1] + virtualBounds[3]) return false;
        if (expectedWindow != null && (!Same(windowBounds, expectedWindow) || !Same(clientBounds, expectedClient))) return false;
        uint dpi = 96; try { dpi = GetDpiForWindow(hwnd); } catch { } if (dpi == 0) dpi = 96;
        dpiScale = Math.Round(dpi / 96.0, 3);
        return !requireForeground || GetForegroundWindow() == hwnd;
    }

    public static bool ForegroundVerifiedWindow(IntPtr hwnd) { return SetForegroundWindow(hwnd) && GetForegroundWindow() == hwnd; }

    private static AifsCaptureResult Summarize(Bitmap bitmap, string method, int[] windowBounds, int[] clientBounds, double dpiScale) {
        long total = (long)bitmap.Width * bitmap.Height, visible = 0;
        double sum = 0.0, square = 0.0, minimum = 255.0, maximum = 0.0;
        int first = 0; bool firstSet = false, uniform = true;
        for (int y = 0; y < bitmap.Height; y++) for (int x = 0; x < bitmap.Width; x++) {
            Color color = bitmap.GetPixel(x, y); if (color.A == 0) continue;
            visible++; int packed = color.ToArgb(); if (!firstSet) { first = packed; firstSet = true; } else if (packed != first) uniform = false;
            double luminance = (299.0 * color.R + 587.0 * color.G + 114.0 * color.B) / 1000.0;
            sum += luminance; square += luminance * luminance; minimum = Math.Min(minimum, luminance); maximum = Math.Max(maximum, luminance);
        }
        double mean = visible == 0 ? 0.0 : sum / visible;
        double variance = visible == 0 ? 0.0 : square / visible - mean * mean;
        bool transparent = visible < total / 10, black = visible == 0 || maximum <= 5.0, white = visible > 0 && minimum >= 250.0;
        bool blank = transparent || black || white;
        return new AifsCaptureResult { Method=method, WindowBounds=windowBounds, ClientBounds=clientBounds, DpiScale=dpiScale,
            Blank=blank, Uniform=uniform, Transparent=transparent, Black=black, White=white, TotalPixels=total, VisiblePixels=visible,
            Mean=Math.Round(mean,3), Minimum=Math.Round(visible == 0 ? 0.0 : minimum,3), Maximum=Math.Round(maximum,3),
            Range=Math.Round(visible == 0 ? 0.0 : maximum-minimum,3), Variance=Math.Round(Math.Max(0.0,variance),3) };
    }
    private static bool Healthy(AifsCaptureResult value) { return !value.Blank && !value.Uniform && value.VisiblePixels >= value.TotalPixels / 10 && value.Range >= 20.0 && value.Variance >= 25.0; }

    public static AifsCaptureResult Capture(IntPtr hwnd, int ownerPid, string exactTitle, string path, int[] expectedWindow, int[] expectedClient, double expectedDpi) {
        int[] windowBounds = expectedWindow, clientBounds = expectedClient; double dpiScale = expectedDpi;
        using (Bitmap bitmap = new Bitmap(windowBounds[2], windowBounds[3], PixelFormat.Format32bppArgb)) {
            bool printed; using (Graphics graphics = Graphics.FromImage(bitmap)) { IntPtr hdc = graphics.GetHdc(); try { printed = PrintWindow(hwnd, hdc, 2); } finally { graphics.ReleaseHdc(hdc); } }
            AifsCaptureResult result = Summarize(bitmap, "print_window", windowBounds, clientBounds, dpiScale);
            if (!printed || !Healthy(result)) {
                int[] fallbackWindow, fallbackClient; double fallbackDpi;
                if (!ValidateWindow(hwnd, ownerPid, exactTitle, expectedWindow, expectedClient, true, out fallbackWindow, out fallbackClient, out fallbackDpi))
                    throw new InvalidOperationException("screenshot_fallback_revalidation_failed");
                using (Graphics graphics = Graphics.FromImage(bitmap)) graphics.CopyFromScreen(windowBounds[0], windowBounds[1], 0, 0, new Size(windowBounds[2], windowBounds[3]), CopyPixelOperation.SourceCopy);
                result = Summarize(bitmap, "copy_from_screen_exact_bounds", windowBounds, clientBounds, dpiScale);
            }
            if (!Healthy(result)) throw new InvalidOperationException("screenshot_pixel_sanity_failed");
            bitmap.Save(path, ImageFormat.Png); return result;
        }
    }
}
'@
'''

_CAPTURE_INSERTION = r'''
    Write-PayloadStartupMarker "screenshot_started" $RunId "started"
    $ScreenshotCapturedAt = Get-UtcTimestamp
    $ScreenshotValidationStatus = "failed"; $ScreenshotErrors = New-Object System.Collections.Generic.List[string]
    $ScreenshotWarnings = New-Object System.Collections.Generic.List[string]; $ScreenshotMethod = "none"
    $ScreenshotWindowBounds = [ordered]@{ x = 0; y = 0; width = 0; height = 0 }; $ScreenshotClientBounds = [ordered]@{ x = 0; y = 0; width = 0; height = 0 }
    $ScreenshotDpiScale = 0.0; $ScreenshotWidth = 0; $ScreenshotHeight = 0; $ScreenshotSize = 0; $ScreenshotHash = ""
    $ScreenshotSummary = [ordered]@{ blank = $true; uniform = $true; transparent = $true; black = $true; white = $false; total_pixels = 0; visible_pixels = 0; mean_luminance = 0.0; min_luminance = 0.0; max_luminance = 0.0; luminance_range = 0.0; luminance_variance = 0.0 }
    try {
        Update-OwnedTree; Assert-OwnedClassification
        if (-not $InstallationPassed -or -not $InstalledExeFound -or -not $InstalledExeRegular -or -not $InstalledExeNonReparse -or [String]::IsNullOrWhiteSpace($InstalledExeHash) -or -not $InstallRootVerified -or $Owned.Count -le 0 -or -not $AllImagesContained -or -not $RootProcessRetained -or -not $WindowOwnerInTree -or $WindowTitle -cne $ExactTitle -or -not $WindowVisible -or $StableDuration -lt 5 -or -not $FirstLaunchVerified -or -not $GuiPassed -or $BackendStatus -cne "ready" -or -not $BackendTracked -or -not $BackendImageContained -or -not $BackendEndpointVerified -or -not $BackendPidOwnershipVerified -or -not $BackendImageVerified) { throw "screenshot_readiness_prerequisite_failed" }
        if ($VerifiedWindow -eq [IntPtr]::Zero -or $null -eq $WindowOwnerPid -or -not $Owned.ContainsKey($WindowOwnerPid) -or -not $Owned[$WindowOwnerPid].EligibleForGui -or -not (Test-OwnedCreation $Owned[$WindowOwnerPid])) { throw "screenshot_owned_window_invalid" }
        $OwnerImage = [AifsProductionWindowApi]::GetImagePath($WindowOwnerPid)
        if ([String]::IsNullOrWhiteSpace($OwnerImage) -or -not (Test-ImageContained $OwnerImage) -or [IO.Path]::GetFileName($OwnerImage) -cne $InstalledExeName) { throw "screenshot_owner_image_invalid" }
        $InitialWindow = $null; $InitialClient = $null; $InitialDpi = 0.0
        if (-not [AifsScreenshotApi]::ValidateWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle, $null, $null, $false, [ref]$InitialWindow, [ref]$InitialClient, [ref]$InitialDpi)) { throw "screenshot_window_geometry_invalid" }
        if (-not [AifsScreenshotApi]::ForegroundVerifiedWindow($VerifiedWindow)) { throw "screenshot_foreground_failed" }
        Start-Sleep -Milliseconds 250
        $FinalWindow = $null; $FinalClient = $null; $FinalDpi = 0.0
        if (-not [AifsScreenshotApi]::ValidateWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle, $InitialWindow, $InitialClient, $true, [ref]$FinalWindow, [ref]$FinalClient, [ref]$FinalDpi) -or -not (Test-OwnedCreation $Owned[$WindowOwnerPid])) { throw "screenshot_final_revalidation_failed" }
        $OwnerImage = [AifsProductionWindowApi]::GetImagePath($WindowOwnerPid)
        if ([String]::IsNullOrWhiteSpace($OwnerImage) -or -not (Test-ImageContained $OwnerImage) -or [IO.Path]::GetFileName($OwnerImage) -cne $InstalledExeName) { throw "screenshot_owner_image_changed" }
        $ScreenshotTemporary = Join-Path $EvidenceRoot "production-main-window.png.tmp"
        $Capture = [AifsScreenshotApi]::Capture($VerifiedWindow, $WindowOwnerPid, $ExactTitle, $ScreenshotTemporary, $FinalWindow, $FinalClient, $FinalDpi)
        $PostWindow = $null; $PostClient = $null; $PostDpi = 0.0
        if (-not [AifsScreenshotApi]::ValidateWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle, $FinalWindow, $FinalClient, $true, [ref]$PostWindow, [ref]$PostClient, [ref]$PostDpi) -or -not (Test-OwnedCreation $Owned[$WindowOwnerPid])) { Remove-Item -LiteralPath $ScreenshotTemporary -Force -ErrorAction SilentlyContinue; throw "screenshot_window_changed_during_capture" }
        Move-Item -LiteralPath $ScreenshotTemporary -Destination $ScreenshotPath
        $ScreenshotItem = Get-Item -LiteralPath $ScreenshotPath -Force
        if ($ScreenshotItem.PSIsContainer -or ($ScreenshotItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $ScreenshotItem.Length -le 0 -or $ScreenshotItem.Length -gt 32MB) { throw "screenshot_file_invalid" }
        $ScreenshotMethod = $Capture.Method; $ScreenshotDpiScale = $Capture.DpiScale
        $ScreenshotWindowBounds = [ordered]@{ x = $Capture.WindowBounds[0]; y = $Capture.WindowBounds[1]; width = $Capture.WindowBounds[2]; height = $Capture.WindowBounds[3] }
        $ScreenshotClientBounds = [ordered]@{ x = $Capture.ClientBounds[0]; y = $Capture.ClientBounds[1]; width = $Capture.ClientBounds[2]; height = $Capture.ClientBounds[3] }
        $ScreenshotWidth = $Capture.WindowBounds[2]; $ScreenshotHeight = $Capture.WindowBounds[3]; $ScreenshotSize = $ScreenshotItem.Length
        $ScreenshotHash = (Get-FileHash -LiteralPath $ScreenshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $ScreenshotSummary = [ordered]@{ blank = $Capture.Blank; uniform = $Capture.Uniform; transparent = $Capture.Transparent; black = $Capture.Black; white = $Capture.White; total_pixels = $Capture.TotalPixels; visible_pixels = $Capture.VisiblePixels; mean_luminance = $Capture.Mean; min_luminance = $Capture.Minimum; max_luminance = $Capture.Maximum; luminance_range = $Capture.Range; luminance_variance = $Capture.Variance }
        $ScreenshotValidationStatus = "passed"
    } catch {
        $ScreenshotErrors.Add(([string]$_.Exception.Message -replace "[^A-Za-z0-9_]", "_"))
        Remove-Item -LiteralPath (Join-Path $EvidenceRoot "production-main-window.png.tmp") -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $ScreenshotPath -Force -ErrorAction SilentlyContinue
    }
    $ScreenshotCapturedAt = Get-UtcTimestamp
    Write-AtomicJson $ScreenshotManifestPath ([ordered]@{
        schema_version = 1; protocol = "aifs_production_window_screenshot_v1"; run_id = $RunId; profile = $ProfileName; captured_at = $ScreenshotCapturedAt; source = "owned_window"
        owned_window_verified = [bool]($ScreenshotValidationStatus -ceq "passed"); owner_pid = $WindowOwnerPid; hwnd_present = [bool]($VerifiedWindow -ne [IntPtr]::Zero)
        exact_title_verified = [bool]($WindowTitle -ceq $ExactTitle); window_visible = [bool]$WindowVisible; window_bounds = $ScreenshotWindowBounds; client_bounds = $ScreenshotClientBounds
        dpi_scale = $ScreenshotDpiScale; image_filename = "production-main-window.png"; image_format = "png"; width = $ScreenshotWidth; height = $ScreenshotHeight
        file_size = $ScreenshotSize; sha256 = $ScreenshotHash; capture_method = $ScreenshotMethod; blank_check = [bool](-not $ScreenshotSummary.blank); uniformity_check = [bool](-not $ScreenshotSummary.uniform)
        image_summary = $ScreenshotSummary; validation_status = $ScreenshotValidationStatus; errors = @($ScreenshotErrors); warnings = @($ScreenshotWarnings)
    })
'''


def derive_screenshot_payload(source: str) -> str:
    """Build the tracked, byte-stable Phase 3B payload from pinned Phase 3A source."""
    capture_marker = '    $Status = "passed"; $Outcome = "passed"; $ExitCode = 0'
    path_marker = '$ProductionEvidencePath = Join-Path $EvidenceRoot "production-evidence.json"'
    production_api_start = source.find("Add-Type -TypeDefinition @'")
    production_api_close = source.find("\n'@", production_api_start)
    core_completed_marker = "$StartedAt = Get-UtcTimestamp"
    if (
        production_api_start < 0
        or production_api_close < 0
        or source.count(capture_marker) != 1
        or source.count(path_marker) != 1
        or source.count(core_completed_marker) != 1
    ):
        raise WorkspaceError("Phase 3A bootstrap markers are not exact")
    production_api_end = production_api_close + len("\n'@")
    production_api = source[production_api_start:production_api_end]
    derived = source[:production_api_start] + source[production_api_end:]
    screenshot_api = _API_INSERTION.strip().replace("Add-Type -AssemblyName System.Drawing\n", "", 1)
    screenshot_api = screenshot_api.replace(
        "Add-Type -TypeDefinition @'",
        "Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'",
        1,
    )
    initialization = (
        'Write-PayloadStartupMarker "core_functions_loading_completed" $PayloadRunId\n'
        'Write-PayloadStartupMarker "production_api_add_type_started" $PayloadRunId "started"\n'
        'try {\n' + production_api + '\n} catch {\n'
        '    Write-PayloadInitializationFailure "production_api_add_type_started" $_ "production_api_initialization_failed" 21\n'
        '    exit 21\n}\n'
        'Write-PayloadStartupMarker "production_api_add_type_completed" $PayloadRunId\n'
        'Write-PayloadStartupMarker "drawing_assembly_loading_started" $PayloadRunId "started"\n'
        'try { Add-Type -AssemblyName System.Drawing } catch {\n'
        '    Write-PayloadInitializationFailure "drawing_assembly_loading_started" $_ "drawing_assembly_initialization_failed" 22\n'
        '    exit 22\n}\n'
        'Write-PayloadStartupMarker "drawing_assembly_loading_completed" $PayloadRunId\n'
        'Write-PayloadStartupMarker "screenshot_api_add_type_started" $PayloadRunId "started"\n'
        'try {\n' + screenshot_api + '\n} catch {\n'
        '    Write-PayloadInitializationFailure "screenshot_api_add_type_started" $_ "screenshot_api_initialization_failed" 23\n'
        '    exit 23\n}\n'
        'Write-PayloadStartupMarker "screenshot_api_add_type_completed" $PayloadRunId\n'
        + _PAYLOAD_REQUEST_INSERTION.strip() + '\n\n'
    )
    derived = derived.replace(
        path_marker,
        path_marker + '\n$ScreenshotPath = Join-Path $EvidenceRoot "production-main-window.png"\n'
        '$ScreenshotManifestPath = Join-Path $EvidenceRoot "screenshot-evidence.json"',
        1,
    )
    derived = _PAYLOAD_PRELUDE + derived
    derived = derived.replace(core_completed_marker, initialization + core_completed_marker, 1)
    derived = derived.replace(capture_marker, _CAPTURE_INSERTION + "\n" + capture_marker, 1)
    derived = derived.replace(
        '    Assert-ExactProperties $Request @("schema_version", "protocol", "run_id", "profile_name", "product", "version", "artifact_name", "artifact_size", "artifact_sha256_host", "guest_terminal_deadline_utc", "profile") "request"',
        '    Assert-ExactProperties $Request @("schema_version", "protocol", "run_id", "profile_name", "product", "version", "artifact_name", "artifact_size", "artifact_sha256_host", "guest_terminal_deadline_utc", "profile", "execution_mode", "payload_sha256") "request"',
        1,
    )
    derived = derived.replace(
        '    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId) { throw "request_run_id_invalid" }',
        '    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId -or $RunId -cne $PayloadRunId) { throw "request_run_id_invalid" }\n'
        '    if ([string]$Request.execution_mode -cne "production_screenshot") { throw "execution_mode_invalid" }',
        1,
    )
    derived = derived.replace(
        "    $NetworkDisabled = -not [Net.NetworkInformation.NetworkInterface]::GetIsNetworkAvailable()",
        '    Write-PayloadStartupMarker "production_preflight_started" $RunId "started"\n'
        "    $NetworkDisabled = -not [Net.NetworkInformation.NetworkInterface]::GetIsNetworkAvailable()",
        1,
    )
    derived = derived.replace(
        '    Set-Phase "installing"',
        '    Write-PayloadStartupMarker "installation_started" $RunId "started"\n    Set-Phase "installing"',
        1,
    )
    derived = derived.replace(
        "    Write-AtomicJson $CompletionPath ([ordered]@{ schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; completed = $true; status = $Status; outcome = $Outcome; overall_readiness = $OverallReadiness; completion_created_at = $CompletionCreatedAt })",
        '    Write-PayloadStartupMarker "completed" $RunId\n'
        "    Write-AtomicJson $CompletionPath ([ordered]@{ schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; completed = $true; status = $Status; outcome = $Outcome; overall_readiness = $OverallReadiness; completion_created_at = $CompletionCreatedAt })",
        1,
    )
    return derived


def derive_screenshot_bootstrap(source: str, run_id: str | None = None) -> str:
    """Compatibility alias for build-time drift checks; runtime never calls it."""
    if run_id is not None:
        validate_run_id(run_id)
    return derive_screenshot_payload(source)


def screenshot_external_opt_in_enabled(explicit_external: bool, mark_expression: str) -> bool:
    return explicit_external and os.environ.get(SCREENSHOT_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == "external"


def build_screenshot_guest_request(
    request: ScreenshotSelfTestRequest,
    staged_artifact: Path,
    artifact_sha256: str,
    *,
    guest_terminal_deadline_utc: str,
) -> dict[str, Any]:
    payload = build_production_guest_request(
        request,
        staged_artifact,
        artifact_sha256,
        guest_terminal_deadline_utc=guest_terminal_deadline_utc,
    )
    payload["execution_mode"] = "production_screenshot"
    payload["payload_sha256"] = PAYLOAD_SHA256
    return payload


class ScreenshotSelfTestWorkspaceManager(ProductionSelfTestWorkspaceManager):
    def __init__(self, runtime_root: Path | None = None):
        guest_root = Path(__file__).with_name("guest")
        SandboxWorkspaceManager.__init__(
            self,
            runtime_root,
            guest_root / ENTRY_SCRIPT_FILENAME,
            ENTRY_SCRIPT_FILENAME,
        )
        self.payload_source = guest_root / PAYLOAD_SCRIPT_FILENAME

    def create(self, request: ScreenshotSelfTestRequest):
        paths, staged, digest = super().create(request)
        if not self.payload_source.is_file() or sha256_file(self.payload_source) != PAYLOAD_SHA256:
            raise WorkspaceError("tracked Phase 3B payload hash mismatch")
        shutil.copyfile(
            self.payload_source,
            paths.guest_directory / PAYLOAD_SCRIPT_FILENAME,
            follow_symlinks=False,
        )
        return paths, staged, digest

    def write_guest_request(
        self,
        request: ScreenshotSelfTestRequest,
        paths: SandboxRunPaths,
        staged_artifact: Path,
        artifact_sha256: str,
        *,
        guest_terminal_deadline_utc: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(request_path, build_screenshot_guest_request(
            request,
            staged_artifact,
            artifact_sha256,
            guest_terminal_deadline_utc=guest_terminal_deadline_utc,
        ))
        return request_path

    def validate_for_launch(self, request: ScreenshotSelfTestRequest, paths: SandboxRunPaths, *, guest_terminal_deadline_utc: str) -> None:
        SandboxWorkspaceManager.validate_for_launch(self, request, paths)
        validate_trusted_production_artifact()
        if tuple(paths.evidence_directory.iterdir()):
            raise WorkspaceError("screenshot evidence directory must be empty before launch")
        inputs = tuple(paths.input_directory.iterdir())
        if len(inputs) != 1 or inputs[0].name != "artifact.exe":
            raise WorkspaceError("production input must contain only artifact.exe")
        if inputs[0].stat().st_size != phase3a.INSTALLER_SIZE or sha256_file(inputs[0]) != phase3a.INSTALLER_SHA256:
            raise WorkspaceError("staged production installer changed before launch")
        entry_script = paths.guest_directory / ENTRY_SCRIPT_FILENAME
        payload_script = paths.guest_directory / PAYLOAD_SCRIPT_FILENAME
        if not entry_script.is_file() or not payload_script.is_file():
            raise WorkspaceError("required workspace file is missing")
        if sha256_file(entry_script) != sha256_file(self.bootstrap_source):
            raise WorkspaceError("Phase 3B entry script changed before launch")
        if sha256_file(payload_script) != PAYLOAD_SHA256:
            raise WorkspaceError("Phase 3B payload changed before launch")
        try:
            payload = json.loads((paths.guest_directory / "request.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("production guest request is invalid") from exc
        if payload != build_screenshot_guest_request(
            request,
            inputs[0],
            phase3a.INSTALLER_SHA256,
            guest_terminal_deadline_utc=guest_terminal_deadline_utc,
        ):
            raise WorkspaceError("production guest request changed before launch")
        phase3a.ensure_no_active_windows_sandbox_session()


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _contained_regular_file(path: Path, directory: Path, *, maximum: int, allow_empty: bool = False) -> int:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ScreenshotEvidenceError("required screenshot evidence is missing") from exc
    if path.parent != directory or path.resolve(strict=True).parent != directory.resolve(strict=True):
        raise ScreenshotEvidenceError("screenshot evidence path escapes its directory")
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise ScreenshotEvidenceError("screenshot evidence must be regular and non-reparse")
    if info.st_size < (0 if allow_empty else 1) or info.st_size > maximum:
        raise ScreenshotEvidenceError("screenshot evidence size is invalid")
    return info.st_size


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScreenshotEvidenceError("screenshot manifest contains duplicate JSON keys")
        result[key] = value
    return result


def startup_marker_filename(prefix: str, stage: str) -> str:
    if prefix not in {"entry", "payload"} or stage not in {*ENTRY_STARTUP_STAGES, *PAYLOAD_STARTUP_STAGES}:
        raise ValueError("startup marker identity is invalid")
    return f"{prefix}-{stage}.json"


def required_startup_marker_names() -> set[str]:
    return {
        *(startup_marker_filename("entry", stage) for stage in ENTRY_STARTUP_STAGES),
        *(startup_marker_filename("payload", stage) for stage in PAYLOAD_STARTUP_STAGES),
    }


def read_startup_marker(path: Path, run_id: str, *, allow_pending_run_id: bool = False) -> dict[str, Any]:
    _contained_regular_file(path, path.parent, maximum=4096)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScreenshotEvidenceError("startup marker is not strict UTF-8 JSON") from exc
    is_payload = path.name.startswith("payload-")
    expected_fields = {"run_id", "stage", "timestamp", "result"} if is_payload else {"schema_version", "run_id", "stage", "timestamp"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ScreenshotEvidenceError("startup marker fields do not match the exact contract")
    accepted_run_ids = {run_id, ""} if allow_pending_run_id else {run_id}
    if (not is_payload and value.get("schema_version") != STARTUP_MARKER_SCHEMA_VERSION) or value.get("run_id") not in accepted_run_ids:
        raise ScreenshotEvidenceError("startup marker identity is invalid")
    if value.get("stage") not in {*ENTRY_STARTUP_STAGES, *PAYLOAD_STARTUP_STAGES}:
        raise ScreenshotEvidenceError("startup marker stage is invalid")
    prefix = "payload-" if is_payload else "entry-"
    expected_stage = path.name[len(prefix):-len(".json")]
    if value.get("stage") != expected_stage:
        raise ScreenshotEvidenceError("startup marker stage does not match its filename")
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z", timestamp):
        raise ScreenshotEvidenceError("startup marker timestamp is invalid")
    if is_payload and value.get("result") not in {"entered", "started", "completed"}:
        raise ScreenshotEvidenceError("startup marker result is invalid")
    return value


def read_entry_payload_result(path: Path, run_id: str) -> dict[str, Any]:
    _contained_regular_file(path, path.parent, maximum=4096)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except ScreenshotEvidenceError as exc:
        raise ScreenshotEvidenceError("entry payload result contains duplicate JSON keys") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScreenshotEvidenceError("entry payload result is not strict UTF-8 JSON") from exc
    expected = {"schema_version", "run_id", "stage", "timestamp", "exit_code", "status"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ScreenshotEvidenceError("entry payload result fields do not match the exact contract")
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1
        or value.get("run_id") != run_id or value.get("stage") != "payload_process_exited"
    ):
        raise ScreenshotEvidenceError("entry payload result identity is invalid")
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}Z", timestamp):
        raise ScreenshotEvidenceError("entry payload result timestamp is invalid")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScreenshotEvidenceError("entry payload result timestamp is invalid") from exc
    exit_code = value.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not -(2**31) <= exit_code < 2**31:
        raise ScreenshotEvidenceError("entry payload result exit code is invalid")
    expected_status = "passed" if exit_code == 0 else "failed"
    if value.get("status") != expected_status:
        raise ScreenshotEvidenceError("entry payload result status contradicts its exit code")
    return value


def entry_payload_result_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ScreenshotEvidenceError("entry payload result path cannot be inspected") from exc
    return True


def read_payload_initialization_failure(path: Path, run_id: str) -> dict[str, Any]:
    _contained_regular_file(path, path.parent, maximum=4096)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScreenshotEvidenceError("payload initialization failure is invalid JSON") from exc
    expected = {
        "schema_version", "run_id", "stage", "exception_type", "fully_qualified_error_id",
        "category", "hresult", "exit_code", "timestamp", "reason", "normalized_message",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("schema_version") != 1 or value.get("run_id") != run_id:
        raise ScreenshotEvidenceError("payload initialization failure contract is invalid")
    if value.get("stage") not in {
        "production_api_add_type_started", "drawing_assembly_loading_started",
        "screenshot_api_add_type_started", "request_loading_started",
    }:
        raise ScreenshotEvidenceError("payload initialization failure stage is invalid")
    for name in ("exception_type", "fully_qualified_error_id", "category", "reason", "normalized_message"):
        item = value.get(name)
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.]{0,128}", item):
            raise ScreenshotEvidenceError("payload initialization failure contains unsafe text")
    if value.get("hresult") is not None and (isinstance(value["hresult"], bool) or not isinstance(value["hresult"], int)):
        raise ScreenshotEvidenceError("payload initialization HRESULT is invalid")
    if isinstance(value.get("exit_code"), bool) or not isinstance(value.get("exit_code"), int):
        raise ScreenshotEvidenceError("payload initialization exit code is invalid")
    if not isinstance(value.get("timestamp"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z", value["timestamp"]):
        raise ScreenshotEvidenceError("payload initialization failure timestamp is invalid")
    return value


def _safe_codes(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 20 or any(not isinstance(item, str) or not _SAFE_CODE.fullmatch(item) for item in value):
        raise ScreenshotEvidenceError(f"screenshot {name} must be bounded safe codes")
    return value


def _load_manifest(path: Path, directory: Path) -> dict[str, Any]:
    _contained_regular_file(path, directory, maximum=32 * 1024)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScreenshotEvidenceError("screenshot manifest is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise ScreenshotEvidenceError("screenshot manifest fields do not match the exact contract")
    return value


def _bounds(value: Any, name: str, *, positive: bool) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != _BOUNDS_FIELDS:
        raise ScreenshotEvidenceError(f"screenshot {name} fields are invalid")
    if any(isinstance(value[key], bool) or not isinstance(value[key], int) for key in _BOUNDS_FIELDS):
        raise ScreenshotEvidenceError(f"screenshot {name} types are invalid")
    if positive and (value["width"] <= 0 or value["height"] <= 0):
        raise ScreenshotEvidenceError(f"screenshot {name} dimensions are invalid")
    return value


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def validate_png_and_summarize(data: bytes, expected_width: int, expected_height: int) -> dict[str, Any]:
    """Fully decode one strict PNG and return independent host pixel evidence."""
    if data.startswith(b"MZ"):
        raise ScreenshotEvidenceError("screenshot is an executable disguised as PNG")
    if not data.startswith(_PNG_SIGNATURE):
        raise ScreenshotEvidenceError("screenshot PNG signature is invalid")
    offset = len(_PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ScreenshotEvidenceError("screenshot PNG chunk is truncated")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if length > MAX_SCREENSHOT_BYTES or end > len(data):
            raise ScreenshotEvidenceError("screenshot PNG chunk length is invalid")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ScreenshotEvidenceError("screenshot PNG chunk CRC is invalid")
        chunks.append((kind, payload)); offset = end
        if kind == b"IEND":
            break
    kinds = [kind for kind, _ in chunks]
    if offset != len(data) or not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND":
        raise ScreenshotEvidenceError("screenshot PNG framing is invalid")
    if kinds.count(b"IHDR") != 1 or kinds.count(b"IEND") != 1 or not kinds.count(b"IDAT"):
        raise ScreenshotEvidenceError("screenshot PNG is not one complete image")
    if chunks[-1][1]:
        raise ScreenshotEvidenceError("screenshot PNG end marker is invalid")
    if any(kind not in {b"IHDR", b"IDAT", b"IEND", b"pHYs", b"sRGB", b"gAMA"} for kind in kinds):
        raise ScreenshotEvidenceError("screenshot PNG contains forbidden text or metadata")
    if kinds.count(b"pHYs") > 1:
        raise ScreenshotEvidenceError("screenshot PNG physical metadata is invalid")
    if kinds.count(b"sRGB") > 1 or kinds.count(b"gAMA") > 1:
        raise ScreenshotEvidenceError("screenshot PNG color metadata is duplicated")
    first_idat = kinds.index(b"IDAT")
    last_idat = len(kinds) - 1 - kinds[::-1].index(b"IDAT")
    if any(kind != b"IDAT" for kind in kinds[first_idat:last_idat + 1]):
        raise ScreenshotEvidenceError("screenshot PNG image data chunks are not contiguous")
    for index, (kind, payload) in enumerate(chunks):
        if kind == b"pHYs":
            if len(payload) != 9 or not 0 < index < first_idat:
                raise ScreenshotEvidenceError("screenshot PNG physical metadata is invalid")
            x_density, y_density, unit = struct.unpack(">IIB", payload)
            if not x_density or not y_density or unit not in {0, 1}:
                raise ScreenshotEvidenceError("screenshot PNG physical metadata is invalid")
        elif kind == b"sRGB":
            if len(payload) != 1 or payload[0] not in range(4) or not 0 < index < first_idat:
                raise ScreenshotEvidenceError("screenshot PNG sRGB metadata is invalid")
        elif kind == b"gAMA":
            if len(payload) != 4 or struct.unpack(">I", payload)[0] <= 0 or not 0 < index < first_idat:
                raise ScreenshotEvidenceError("screenshot PNG gamma metadata is invalid")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise ScreenshotEvidenceError("screenshot PNG header is invalid")
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
    if (width, height) != (expected_width, expected_height) or depth != 8 or color_type not in {2, 6}:
        raise ScreenshotEvidenceError("screenshot PNG dimensions or pixel format are invalid")
    if compression or filtering or interlace:
        raise ScreenshotEvidenceError("screenshot PNG encoding is unsupported")
    channels = 4 if color_type == 6 else 3
    row_size = width * channels
    expected_decoded = height * (row_size + 1)
    inflater = zlib.decompressobj()
    try:
        decoded = inflater.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"), expected_decoded + 1)
        if inflater.unconsumed_tail:
            raise ScreenshotEvidenceError("screenshot PNG decoded size is invalid")
        decoded += inflater.flush()
    except zlib.error as exc:
        raise ScreenshotEvidenceError("screenshot PNG image data cannot be decoded") from exc
    if not inflater.eof or inflater.unused_data or len(decoded) != expected_decoded:
        raise ScreenshotEvidenceError("screenshot PNG decoded size is invalid")

    previous = bytearray(row_size)
    first_pixel: tuple[int, int, int, int] | None = None
    uniform = True
    total = width * height
    visible = 0
    luminance_sum = luminance_square_sum = 0.0
    minimum, maximum = 255.0, 0.0
    cursor = 0
    for _ in range(height):
        filter_type = decoded[cursor]; cursor += 1
        if filter_type > 4:
            raise ScreenshotEvidenceError("screenshot PNG row filter is invalid")
        raw = decoded[cursor:cursor + row_size]; cursor += row_size
        row = bytearray(row_size)
        for index, value in enumerate(raw):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            predictor = (0, left, up, (left + up) // 2, _paeth(left, up, upper_left))[filter_type]
            row[index] = (value + predictor) & 0xFF
        for index in range(0, row_size, channels):
            red, green, blue = row[index:index + 3]
            alpha = row[index + 3] if channels == 4 else 255
            pixel = (red, green, blue, alpha)
            if first_pixel is None: first_pixel = pixel
            elif pixel != first_pixel: uniform = False
            if alpha:
                visible += 1
                luminance = (299 * red + 587 * green + 114 * blue) / 1000.0
                luminance_sum += luminance; luminance_square_sum += luminance * luminance
                minimum, maximum = min(minimum, luminance), max(maximum, luminance)
        previous = row
    mean = luminance_sum / visible if visible else 0.0
    variance = max(0.0, luminance_square_sum / visible - mean * mean) if visible else 0.0
    transparent = visible < total // 10
    black = visible == 0 or maximum <= 5.0
    white = visible > 0 and minimum >= 250.0
    summary = {
        "blank": transparent or black or white,
        "uniform": uniform,
        "transparent": transparent,
        "black": black,
        "white": white,
        "total_pixels": total,
        "visible_pixels": visible,
        "mean_luminance": round(mean, 3),
        "min_luminance": round(0.0 if visible == 0 else minimum, 3),
        "max_luminance": round(maximum, 3),
        "luminance_range": round(0.0 if visible == 0 else maximum - minimum, 3),
        "luminance_variance": round(variance, 3),
    }
    return summary


def image_summary_passes(summary: Mapping[str, Any]) -> bool:
    return (
        summary.get("blank") is False and summary.get("uniform") is False
        and summary.get("transparent") is False and summary.get("black") is False and summary.get("white") is False
        and isinstance(summary.get("total_pixels"), int) and summary.get("total_pixels", 0) > 0
        and isinstance(summary.get("visible_pixels"), int) and summary.get("visible_pixels", 0) >= summary.get("total_pixels", 0) // 10
        and isinstance(summary.get("luminance_range"), (int, float)) and summary.get("luminance_range", 0) >= 20.0
        and isinstance(summary.get("luminance_variance"), (int, float)) and summary.get("luminance_variance", 0) >= 25.0
    )


def _validate_manifest_identity(manifest: dict[str, Any], run_id: str, run_started_at: datetime) -> datetime:
    if manifest.get("schema_version") != SCREENSHOT_SCHEMA_VERSION or manifest.get("protocol") != SCREENSHOT_PROTOCOL:
        raise ScreenshotEvidenceError("screenshot contract identity is invalid")
    if manifest.get("run_id") != run_id or manifest.get("profile") != PROFILE_NAME or manifest.get("source") != "owned_window":
        raise ScreenshotEvidenceError("screenshot run, profile, or source is invalid")
    if manifest.get("image_filename") != SCREENSHOT_FILENAME or manifest.get("image_format") != "png":
        raise ScreenshotEvidenceError("screenshot filename or format is invalid")
    captured_text = manifest.get("captured_at")
    if not isinstance(captured_text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z", captured_text):
        raise ScreenshotEvidenceError("screenshot capture time is invalid")
    try:
        captured_at = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScreenshotEvidenceError("screenshot capture time is invalid") from exc
    if run_started_at.tzinfo is None:
        raise ScreenshotEvidenceError("host run start must include timezone")
    if captured_at < run_started_at.astimezone(timezone.utc) or captured_at > datetime.now(timezone.utc).replace(microsecond=999999):
        raise ScreenshotEvidenceError("screenshot was not created during the current run")
    _safe_codes(manifest.get("errors"), "errors"); _safe_codes(manifest.get("warnings"), "warnings")
    return captured_at


def _validate_failure_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("validation_status") != "failed" or not manifest.get("errors"):
        raise ScreenshotEvidenceError("failed screenshot contract lacks structured diagnostics")
    if manifest.get("capture_method") not in {"none", "print_window", "copy_from_screen_exact_bounds"}:
        raise ScreenshotEvidenceError("failed screenshot capture method is invalid")
    if any(manifest.get(name) not in {False, None} for name in ("owned_window_verified",)):
        raise ScreenshotEvidenceError("failed screenshot cannot claim owned-window verification")
    if manifest.get("file_size") != 0 or manifest.get("sha256") != "" or manifest.get("width") != 0 or manifest.get("height") != 0:
        raise ScreenshotEvidenceError("failed screenshot cannot claim an image")
    if manifest.get("blank_check") is not False or manifest.get("uniformity_check") is not False:
        raise ScreenshotEvidenceError("failed screenshot sanity claims are invalid")
    if any(not isinstance(manifest.get(name), bool) for name in ("hwnd_present", "exact_title_verified", "window_visible")):
        raise ScreenshotEvidenceError("failed screenshot window diagnostics have invalid types")
    owner_pid = manifest.get("owner_pid")
    if owner_pid is not None and (isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0):
        raise ScreenshotEvidenceError("failed screenshot owner diagnostic is invalid")
    dpi_scale = manifest.get("dpi_scale")
    if isinstance(dpi_scale, bool) or not isinstance(dpi_scale, (int, float)) or not 0 <= dpi_scale <= 8.0:
        raise ScreenshotEvidenceError("failed screenshot DPI diagnostic is invalid")
    _bounds(manifest.get("window_bounds"), "window bounds", positive=False)
    _bounds(manifest.get("client_bounds"), "client bounds", positive=False)
    summary = manifest.get("image_summary")
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_FIELDS:
        raise ScreenshotEvidenceError("failed screenshot image summary is invalid")


def _validate_production_subset(
    directory: Path,
    expected_names: set[str],
    run_id: str,
    expected_guest_terminal_deadline_utc: str,
) -> ValidatedProductionEvidence:
    temporary = directory.parent / (directory.name + ".phase3b-production-validation")
    if temporary.exists():
        raise ScreenshotEvidenceError("production validation workspace already exists")
    temporary.mkdir()
    try:
        for name in expected_names:
            if name in {SCREENSHOT_MANIFEST_FILENAME, SCREENSHOT_FILENAME} or name.startswith(("entry-", "payload-")):
                continue
            shutil.copyfile(directory / name, temporary / name, follow_symlinks=False)
        try:
            return validate_production_evidence_directory(
                temporary,
                run_id,
                expected_guest_terminal_deadline_utc=expected_guest_terminal_deadline_utc,
            )
        except ProductionEvidenceError as exc:
            raise ScreenshotEvidenceError(str(exc)) from exc
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def validate_screenshot_evidence_directory(
    directory: Path,
    run_id: str,
    *,
    run_started_at: datetime,
    expected_guest_terminal_deadline_utc: str,
) -> ValidatedScreenshotEvidence:
    try:
        directory_info = directory.lstat()
    except OSError as exc:
        raise ScreenshotEvidenceError("screenshot evidence directory is missing") from exc
    if directory.is_symlink() or _is_reparse(directory) or not stat.S_ISDIR(directory_info.st_mode) or directory.resolve(strict=True) != directory.absolute():
        raise ScreenshotEvidenceError("screenshot evidence directory must be direct, regular, and non-reparse")
    production_names = {"status.json", "heartbeat.json", "completion.json", "guest-system.json", "lifecycle.log", "production-evidence.json"}
    startup_names = required_startup_marker_names()
    payload_result_name = "entry-payload-result.json"
    entries = tuple(directory.iterdir())
    names = {entry.name for entry in entries}
    for stage in ENTRY_STARTUP_STAGES:
        if startup_marker_filename("entry", stage) not in names:
            raise ScreenshotEvidenceError(f"required startup marker missing: {stage}")
    for stage in PAYLOAD_STARTUP_STAGES:
        if startup_marker_filename("payload", stage) not in names:
            raise ScreenshotEvidenceError(f"required startup marker missing: {stage}")
    manifest_names = {*production_names, *startup_names, SCREENSHOT_MANIFEST_FILENAME}
    allowed_names = (
        manifest_names,
        {*manifest_names, SCREENSHOT_FILENAME},
        {*manifest_names, payload_result_name},
        {*manifest_names, SCREENSHOT_FILENAME, payload_result_name},
    )
    if len(entries) != len(names) or names not in allowed_names:
        raise ScreenshotEvidenceError("screenshot evidence contains unexpected files or multiple images")
    if any(entry.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"} and entry.name != SCREENSHOT_FILENAME for entry in entries):
        raise ScreenshotEvidenceError("screenshot evidence contains an unexpected image")
    for stage in ENTRY_STARTUP_STAGES:
        read_startup_marker(
            directory / startup_marker_filename("entry", stage),
            run_id,
            allow_pending_run_id=stage in {"entry_script_started", "request_found"},
        )
    for stage in PAYLOAD_STARTUP_STAGES:
        read_startup_marker(directory / startup_marker_filename("payload", stage), run_id)
    if payload_result_name in names:
        payload_result = read_entry_payload_result(directory / payload_result_name, run_id)
        if payload_result["exit_code"] != 0:
            raise ScreenshotEvidenceError("successful terminal evidence has a failed payload result")
    manifest = _load_manifest(directory / SCREENSHOT_MANIFEST_FILENAME, directory)
    captured_at = _validate_manifest_identity(manifest, run_id, run_started_at)
    manifest_modified_at = datetime.fromtimestamp((directory / SCREENSHOT_MANIFEST_FILENAME).stat().st_mtime, timezone.utc)
    if manifest_modified_at < run_started_at.astimezone(timezone.utc) or manifest_modified_at > datetime.now(timezone.utc).replace(microsecond=999999):
        raise ScreenshotEvidenceError("screenshot manifest was not created during the current run")
    production = _validate_production_subset(
        directory,
        names,
        run_id,
        expected_guest_terminal_deadline_utc,
    )
    if manifest.get("validation_status") == "failed":
        if SCREENSHOT_FILENAME in names:
            raise ScreenshotEvidenceError("failed screenshot contract must not contain a PNG")
        _validate_failure_manifest(manifest)
        raise ScreenshotEvidenceError("guest screenshot capture failed: " + manifest["errors"][0])
    if manifest.get("validation_status") != "passed" or manifest.get("errors") != []:
        raise ScreenshotEvidenceError("successful screenshot contract has an invalid status")
    successful_names = {*manifest_names, SCREENSHOT_FILENAME}
    if names not in (successful_names, {*successful_names, payload_result_name}):
        raise ScreenshotEvidenceError("successful screenshot contract lacks its exact PNG")
    if any(manifest.get(name) is not True for name in ("owned_window_verified", "hwnd_present", "exact_title_verified", "window_visible", "blank_check", "uniformity_check")):
        raise ScreenshotEvidenceError("successful screenshot lacks owned visible window or sanity proof")
    owner_pid = manifest.get("owner_pid")
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ScreenshotEvidenceError("screenshot owner PID is invalid")
    dpi_scale = manifest.get("dpi_scale")
    if isinstance(dpi_scale, bool) or not isinstance(dpi_scale, (int, float)) or not 0.5 <= dpi_scale <= 8.0:
        raise ScreenshotEvidenceError("screenshot DPI scale is invalid")
    window_bounds = _bounds(manifest.get("window_bounds"), "window bounds", positive=True)
    client_bounds = _bounds(manifest.get("client_bounds"), "client bounds", positive=True)
    width, height = manifest.get("width"), manifest.get("height")
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise ScreenshotEvidenceError("screenshot declared dimensions are invalid")
    if (width, height) != (window_bounds["width"], window_bounds["height"]) or not MIN_SCREENSHOT_WIDTH <= width <= MAX_SCREENSHOT_WIDTH or not MIN_SCREENSHOT_HEIGHT <= height <= MAX_SCREENSHOT_HEIGHT:
        raise ScreenshotEvidenceError("screenshot declared and window dimensions differ")
    if client_bounds["width"] > width or client_bounds["height"] > height:
        raise ScreenshotEvidenceError("screenshot client bounds exceed the window")
    if manifest.get("capture_method") not in {"print_window", "copy_from_screen_exact_bounds"}:
        raise ScreenshotEvidenceError("screenshot capture method is invalid")
    png_path = directory / SCREENSHOT_FILENAME
    png_size = _contained_regular_file(png_path, directory, maximum=MAX_SCREENSHOT_BYTES)
    data = png_path.read_bytes()
    if data.startswith(b"MZ"):
        raise ScreenshotEvidenceError("screenshot is an executable disguised as PNG")
    digest = hashlib.sha256(data).hexdigest()
    if manifest.get("file_size") != png_size or manifest.get("sha256") != digest:
        raise ScreenshotEvidenceError("screenshot size or SHA-256 does not match")
    modified_at = datetime.fromtimestamp(png_path.stat().st_mtime, timezone.utc)
    if modified_at < run_started_at.astimezone(timezone.utc) or modified_at > datetime.now(timezone.utc).replace(microsecond=999999):
        raise ScreenshotEvidenceError("screenshot file was not created during the current run")
    host_summary = validate_png_and_summarize(data, width, height)
    if not image_summary_passes(host_summary):
        raise ScreenshotEvidenceError("screenshot fails independent host pixel sanity")
    guest_summary = manifest.get("image_summary")
    if not isinstance(guest_summary, dict) or set(guest_summary) != _SUMMARY_FIELDS:
        raise ScreenshotEvidenceError("guest image summary fields are invalid")
    if guest_summary != host_summary:
        raise ScreenshotEvidenceError("guest image summary contradicts independent host pixels")
    raw = production.raw
    if production.status != "passed" or not production.fully_ready or not raw.get("cleanup_complete"):
        raise ScreenshotEvidenceError("screenshot run lacks passed production cleanup")
    if not production_capture_prerequisites_met(raw):
        raise ScreenshotEvidenceError("production GUI evidence lacks screenshot prerequisites")
    if owner_pid != raw.get("window_owner_pid"):
        raise ScreenshotEvidenceError("screenshot owner contradicts production evidence")
    cleanup_started = datetime.fromisoformat(raw["cleanup_started_at"].replace("Z", "+00:00"))
    stable_verified = datetime.fromisoformat(raw["stable_verified_at"].replace("Z", "+00:00"))
    backend_ready = datetime.fromisoformat(raw["backend_ready_at"].replace("Z", "+00:00"))
    if not max(stable_verified, backend_ready, run_started_at.astimezone(timezone.utc)) <= captured_at <= cleanup_started:
        raise ScreenshotEvidenceError("screenshot was not captured after readiness and before cleanup")
    return ValidatedScreenshotEvidence(production, manifest, host_summary, digest, png_size, width, height)


def validate_partial_screenshot_evidence_directory(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink() or _is_reparse(directory):
        raise ScreenshotEvidenceError("screenshot evidence directory must be regular and non-reparse")
    allowed = {
        "status.json", "heartbeat.json", "completion.json", "guest-system.json", "lifecycle.log", "production-evidence.json",
        SCREENSHOT_MANIFEST_FILENAME, SCREENSHOT_FILENAME, "production-main-window.png.tmp",
        "entry-failure.json", "entry-payload-result.json", "payload-initialization-failure.json",
        *required_startup_marker_names(),
    }
    total = 0
    for entry in directory.iterdir():
        if entry.name not in allowed and not _ATOMIC_TEMPORARY.fullmatch(entry.name):
            raise ScreenshotEvidenceError("screenshot evidence contains an unexpected file")
        maximum = MAX_SCREENSHOT_BYTES if entry.name in {SCREENSHOT_FILENAME, "production-main-window.png.tmp"} else 128 * 1024
        total += _contained_regular_file(entry, directory, maximum=maximum)
    if total > MAX_SCREENSHOT_BYTES + 576 * 1024:
        raise ScreenshotEvidenceError("screenshot evidence exceeds the total size limit")


def write_validated_screenshot_snapshot(path: Path, evidence: ValidatedScreenshotEvidence) -> None:
    snapshot = dict(evidence.screenshot)
    snapshot["validation_status"] = "passed"
    snapshot["host_image_summary"] = evidence.host_image_summary
    snapshot["provenance"] = {
        "phase3a_profile": PROFILE_NAME,
        "production_fully_ready": True,
        "production_cleanup_complete": True,
        "png_sha256_host": evidence.png_sha256,
        "png_size_host": evidence.png_size,
    }
    atomic_write_json(path, snapshot)
