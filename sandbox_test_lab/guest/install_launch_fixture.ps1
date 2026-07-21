$ErrorActionPreference = "Stop"
$InputRoot = "C:\SandboxTestLab\Input"
$GuestRoot = "C:\SandboxTestLab\Guest"
$EvidenceRoot = "C:\SandboxTestLab\Evidence"
$RequestPath = Join-Path $GuestRoot "request.json"
$StatusPath = Join-Path $EvidenceRoot "status.json"
$HeartbeatPath = Join-Path $EvidenceRoot "heartbeat.json"
$GuestSystemPath = Join-Path $EvidenceRoot "guest-system.json"
$CompletionPath = Join-Path $EvidenceRoot "completion.json"
$LifecycleLogPath = Join-Path $EvidenceRoot "lifecycle.log"
$LaunchEvidencePath = Join-Path $EvidenceRoot "launch-evidence.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$SchemaVersion = 2
$Protocol = "controlled_fixture_install_launch_v1"
$FixtureProfile = "aifs_sandbox_fixture_v1"
$LaunchProfileName = "controlled_fixture_gui_v1"
$LogicalInstallRoot = "sandbox_user_local_app_data\Programs\AIFS Sandbox Fixture"
$ActualInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\AIFS Sandbox Fixture"
$ExactTitle = "AIFS Sandbox Fixture"
$GuiName = "AIFS Sandbox Fixture.exe"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class AifsSandboxWindowApi {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll", SetLastError=true)] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr FindOwnedVisibleWindow(int processId, string exactTitle) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner != (uint)processId || !IsWindowVisible(hwnd)) return true;
            StringBuilder title = new StringBuilder(512);
            GetWindowText(hwnd, title, title.Capacity);
            if (String.Equals(title.ToString(), exactTitle, StringComparison.Ordinal)) {
                found = hwnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static bool IsOwnedVisibleWindow(IntPtr hwnd, int processId, string exactTitle) {
        if (hwnd == IntPtr.Zero || !IsWindowVisible(hwnd)) return false;
        uint owner;
        GetWindowThreadProcessId(hwnd, out owner);
        if (owner != (uint)processId) return false;
        StringBuilder title = new StringBuilder(512);
        GetWindowText(hwnd, title, title.Capacity);
        return String.Equals(title.ToString(), exactTitle, StringComparison.Ordinal);
    }

    public static string GetImagePath(int processId) {
        IntPtr process = OpenProcess(0x1000, false, (uint)processId);
        if (process == IntPtr.Zero) return null;
        try {
            uint size = 32768;
            StringBuilder name = new StringBuilder((int)size);
            return QueryFullProcessImageName(process, 0, name, ref size) ? name.ToString() : null;
        } finally {
            CloseHandle(process);
        }
    }

    public static bool CloseOwnedWindow(IntPtr hwnd, int processId, string exactTitle) {
        return IsOwnedVisibleWindow(hwnd, processId, exactTitle) && PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
    }
}
'@

function Get-UtcTimestamp { return [DateTime]::UtcNow.ToString("o") }

function Write-AtomicJson {
    param([string] $Path, [object] $Value)
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [System.IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 10 -Compress), $Utf8NoBom)
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}

function Write-LifecycleLog {
    param([string] $Message)
    [System.IO.File]::AppendAllText($LifecycleLogPath, "$(Get-UtcTimestamp) $Message$([Environment]::NewLine)", $Utf8NoBom)
}

function Assert-ExactProperties {
    param([object] $Value, [string[]] $Expected, [string] $Name)
    $Actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $Wanted = @($Expected | Sort-Object)
    if (($Actual -join "|") -ne ($Wanted -join "|")) { throw "${Name}_contract_invalid" }
}

$Transitions = New-Object System.Collections.Generic.List[object]
function Set-Phase {
    param([string] $Value)
    $script:Phase = $Value
    $At = Get-UtcTimestamp
    $Transitions.Add([ordered]@{ phase = $Value; at = $At })
    Write-AtomicJson $HeartbeatPath ([ordered]@{
        schema_version = $SchemaVersion; protocol = $Protocol; run_id = $script:RunId; phase = $Value; updated_at = $At
    })
}

function Get-ControlledFileHash {
    param([string] $Root, [string] $Name)
    Assert-NonReparsePathChain -Path $Root
    $RootItem = Get-Item -LiteralPath $Root -Force
    if (($RootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or -not $RootItem.PSIsContainer) { throw "install_root_invalid" }
    $Expected = [System.IO.Path]::GetFullPath((Join-Path $Root $Name))
    $Item = Get-Item -LiteralPath $Expected -Force
    if ($Item.PSIsContainer -or ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "installed_file_invalid" }
    if ([System.IO.Path]::GetFullPath($Item.FullName) -cne $Expected) { throw "installed_file_containment_invalid" }
    return (Get-FileHash -LiteralPath $Expected -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NonReparsePathChain {
    param([string] $Path)
    $Current = [System.IO.Path]::GetFullPath($Path)
    while ($null -ne $Current) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "installed_path_ancestor_reparse"
            }
        }
        $Parent = [System.IO.Directory]::GetParent($Current)
        $Current = if ($null -eq $Parent) { $null } else { $Parent.FullName }
    }
}

$StartedAt = Get-UtcTimestamp
$RunId = "unknown"
$Phase = "installed"
$Status = "failed"
$Outcome = "infrastructure_error"
$CompletedAt = $null
$HostHash = ""
$GuestHash = ""
$HashVerified = $false
$InstallerStartedAt = $null
$InstallerFinishedAt = $null
$InstallerExitCode = $null
$InstallDurationSeconds = $null
$InstallationPassed = $false
$MarkerVerified = $false
$PayloadVerified = $false
$GuiVerified = $false
$InstalledExecutableFound = $false
$InstalledGuiHash = ""
$RebootRequired = $false
$NetworkDisabled = $false
$LaunchStartedAt = $null
$ProcessStartedAt = $null
$WindowDetectionStartedAt = $null
$WindowVisibleAt = $null
$StableStartedAt = $null
$StableVerifiedAt = $null
$CleanupStartedAt = $null
$CleanupFinishedAt = $null
$ProcessId = $null
$WindowHandleValue = $null
$ProcessImageName = $null
$WindowTitle = $null
$ProcessImagePathVerified = $false
$ProcessStarted = $false
$WindowOwnedByProcess = $false
$WindowVisible = $false
$WindowHandlePresent = $false
$WindowTitleVerified = $false
$StableDurationSeconds = $null
$FirstLaunchVerified = $false
$CleanupCloseRequested = $false
$GracefulCloseSucceeded = $false
$ForcedOwnedProcessCleanup = $false
$CleanupProcessExited = $false
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
$InstallerProcess = $null
$GuiProcess = $null
$VerifiedWindow = [IntPtr]::Zero
$ExitCode = 1
$InstallTimeoutSeconds = 0
$LaunchTimeoutSeconds = 0
$StableMinimumSeconds = 0
$CleanupTimeoutSeconds = 0
$ExpectedMarkerHash = ""
$ExpectedPayloadHash = ""
$ExpectedGuiHash = ""
$GuestSystem = [ordered]@{
    os_version = [Environment]::OSVersion.VersionString
    architecture = [Environment]::Is64BitOperatingSystem.ToString()
    powershell_version = $PSVersionTable.PSVersion.ToString()
}

try {
    New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
    [System.IO.File]::WriteAllText($LifecycleLogPath, "", $Utf8NoBom)
    Write-LifecycleLog "controlled_install_launch_started"
    $Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-ExactProperties $Request @(
        "schema_version", "protocol", "run_id", "artifact_name", "artifact_sha256_host", "controlled_fixture_profile",
        "installation_recipe", "expected_install_root", "expected_marker_name", "expected_marker_sha256",
        "expected_payload_name", "expected_payload_sha256", "launch_profile_name", "launch_profile"
    ) "request"
    if ($Request.schema_version -ne $SchemaVersion -or $Request.protocol -ne $Protocol) { throw "request_schema_invalid" }
    $RunId = [string]$Request.run_id
    $ParsedRunId = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId) { throw "request_run_id_invalid" }
    if ($Request.artifact_name -ne "artifact.exe" -or $Request.controlled_fixture_profile -ne $FixtureProfile) { throw "fixture_identity_invalid" }
    if ($Request.expected_install_root -ne $LogicalInstallRoot -or $Request.expected_marker_name -ne "fixture-manifest.json" -or $Request.expected_payload_name -ne "payload.txt") { throw "installed_identity_invalid" }
    $HostHash = ([string]$Request.artifact_sha256_host).ToLowerInvariant()
    $ExpectedMarkerHash = ([string]$Request.expected_marker_sha256).ToLowerInvariant()
    $ExpectedPayloadHash = ([string]$Request.expected_payload_sha256).ToLowerInvariant()
    if ($HostHash -notmatch "^[0-9a-f]{64}$" -or $ExpectedMarkerHash -notmatch "^[0-9a-f]{64}$" -or $ExpectedPayloadHash -notmatch "^[0-9a-f]{64}$") { throw "request_hash_invalid" }

    $Recipe = $Request.installation_recipe
    Assert-ExactProperties $Recipe @("installer_kind", "install_timeout_seconds", "expected_install_scope", "reboot_policy", "network_policy") "installation_recipe"
    if ($Recipe.installer_kind -ne "nsis_exe" -or $Recipe.expected_install_scope -ne "user" -or $Recipe.reboot_policy -ne "forbid" -or $Recipe.network_policy -ne "disabled") { throw "installation_policy_invalid" }
    $InstallTimeoutSeconds = [int]$Recipe.install_timeout_seconds
    if ($InstallTimeoutSeconds -lt 10 -or $InstallTimeoutSeconds -gt 180) { throw "install_timeout_invalid" }
    $NetworkDisabled = -not [System.Net.NetworkInformation.NetworkInterface]::GetIsNetworkAvailable()
    if (-not $NetworkDisabled) { throw "network_not_disabled" }

    if ($Request.launch_profile_name -ne $LaunchProfileName) { throw "launch_profile_invalid" }
    $LaunchProfile = $Request.launch_profile
    Assert-ExactProperties $LaunchProfile @(
        "logical_executable_name", "expected_installed_exe_sha256", "process_image_name", "exact_window_title",
        "launch_timeout_seconds", "minimum_stable_duration_seconds", "cleanup_timeout_seconds"
    ) "launch_profile"
    $ExpectedGuiHash = ([string]$LaunchProfile.expected_installed_exe_sha256).ToLowerInvariant()
    $LaunchTimeoutSeconds = [int]$LaunchProfile.launch_timeout_seconds
    $StableMinimumSeconds = [int]$LaunchProfile.minimum_stable_duration_seconds
    $CleanupTimeoutSeconds = [int]$LaunchProfile.cleanup_timeout_seconds
    if ($LaunchProfile.logical_executable_name -cne $GuiName -or $LaunchProfile.process_image_name -cne $GuiName -or $LaunchProfile.exact_window_title -cne $ExactTitle -or $ExpectedGuiHash -notmatch "^[0-9a-f]{64}$" -or $LaunchTimeoutSeconds -ne 30 -or $StableMinimumSeconds -ne 3 -or $CleanupTimeoutSeconds -ne 10) { throw "launch_profile_values_invalid" }

    Write-AtomicJson $GuestSystemPath $GuestSystem
    $ArtifactPath = Join-Path $InputRoot "artifact.exe"
    $GuestHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashVerified = $GuestHash -eq $HostHash
    if (-not $HashVerified) { $Outcome = "failed"; throw "artifact_hash_mismatch" }

    $MarkerPath = Join-Path $ActualInstallRoot "fixture-manifest.json"
    $PayloadPath = Join-Path $ActualInstallRoot "payload.txt"
    $GuiPath = Join-Path $ActualInstallRoot $GuiName
    $CanonicalLocalRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $CanonicalInstallRoot = [System.IO.Path]::GetFullPath((Join-Path $CanonicalLocalRoot "Programs\AIFS Sandbox Fixture"))
    if ([System.IO.Path]::GetFullPath($ActualInstallRoot) -cne $CanonicalInstallRoot) { throw "controlled_install_root_invalid" }
    Assert-NonReparsePathChain -Path $ActualInstallRoot
    if ((Test-Path -LiteralPath $MarkerPath) -or (Test-Path -LiteralPath $PayloadPath) -or (Test-Path -LiteralPath $GuiPath)) { throw "fixture_install_root_not_clean" }

    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $ArtifactPath
    $StartInfo.Arguments = "/S"
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $InstallerProcess = New-Object System.Diagnostics.Process
    $InstallerProcess.StartInfo = $StartInfo
    $InstallerStartedAt = Get-UtcTimestamp
    $InstallTimer = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $InstallerProcess.Start()) { throw "installer_start_failed" }
    while (-not $InstallerProcess.WaitForExit(200)) {
        if ($InstallTimer.Elapsed.TotalSeconds -ge $InstallTimeoutSeconds) {
            try { $InstallerProcess.Kill(); $InstallerProcess.WaitForExit(5000) | Out-Null } catch { $Warnings.Add("owned_installer_cleanup_failed") }
            $Outcome = "timed_out"
            throw "installer_timed_out"
        }
    }
    $InstallTimer.Stop()
    $InstallerFinishedAt = Get-UtcTimestamp
    $InstallDurationSeconds = [Math]::Round($InstallTimer.Elapsed.TotalSeconds, 3)
    $InstallerExitCode = $InstallerProcess.ExitCode
    if ($InstallerExitCode -eq 1641 -or $InstallerExitCode -eq 3010) { $RebootRequired = $true; $Outcome = "reboot_required"; throw "reboot_required" }
    if ($InstallerExitCode -ne 0) { $Outcome = "failed"; throw "installer_exit_nonzero" }

    $MarkerVerified = (Get-ControlledFileHash $ActualInstallRoot "fixture-manifest.json") -eq $ExpectedMarkerHash
    $PayloadVerified = (Get-ControlledFileHash $ActualInstallRoot "payload.txt") -eq $ExpectedPayloadHash
    $InstalledGuiHash = Get-ControlledFileHash $ActualInstallRoot $GuiName
    $InstalledExecutableFound = $true
    $GuiVerified = $InstalledGuiHash -eq $ExpectedGuiHash
    if (-not $MarkerVerified -or -not $PayloadVerified -or -not $GuiVerified) { $Outcome = "failed"; throw "installed_files_invalid" }
    $InstallationPassed = $true
    Set-Phase "installed"

    Set-Phase "launching"
    $GuiStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $GuiStartInfo.FileName = $GuiPath
    $GuiStartInfo.UseShellExecute = $false
    $GuiStartInfo.CreateNoWindow = $false
    $GuiProcess = New-Object System.Diagnostics.Process
    $GuiProcess.StartInfo = $GuiStartInfo
    $LaunchStartedAt = Get-UtcTimestamp
    $LaunchTimer = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $GuiProcess.Start()) { $Outcome = "failed"; throw "gui_start_failed" }
    $ProcessId = $GuiProcess.Id
    $ProcessStarted = $true
    $ProcessStartedAt = Get-UtcTimestamp
    Set-Phase "process_started"

    $ExpectedImagePath = [System.IO.Path]::GetFullPath($GuiPath)
    $ActualImagePath = [AifsSandboxWindowApi]::GetImagePath($ProcessId)
    $ProcessImagePathVerified = $null -ne $ActualImagePath -and [System.IO.Path]::GetFullPath($ActualImagePath) -ceq $ExpectedImagePath
    $ProcessImageName = [System.IO.Path]::GetFileName($ActualImagePath)
    if (-not $ProcessImagePathVerified -or $ProcessImageName -cne $GuiName) { $Outcome = "failed"; throw "process_image_invalid" }

    $WindowDetectionStartedAt = Get-UtcTimestamp
    Set-Phase "window_detecting"
    while ($LaunchTimer.Elapsed.TotalSeconds -lt $LaunchTimeoutSeconds) {
        if ($GuiProcess.HasExited) { $Outcome = "failed"; throw "gui_exited_before_window" }
        $VerifiedWindow = [AifsSandboxWindowApi]::FindOwnedVisibleWindow($ProcessId, $ExactTitle)
        if ($VerifiedWindow -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 200
    }
    if ($VerifiedWindow -eq [IntPtr]::Zero) { $Outcome = "timed_out"; throw "window_timeout" }
    $WindowHandleValue = $VerifiedWindow.ToInt64()
    $WindowHandlePresent = $WindowHandleValue -ne 0
    $WindowTitle = $ExactTitle
    $WindowTitleVerified = $true
    $WindowOwnedByProcess = $true
    $WindowVisible = $true
    $WindowVisibleAt = Get-UtcTimestamp
    Set-Phase "window_visible"

    $StableStartedAt = Get-UtcTimestamp
    $StableTimer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($StableTimer.Elapsed.TotalSeconds -lt $StableMinimumSeconds -and $LaunchTimer.Elapsed.TotalSeconds -lt $LaunchTimeoutSeconds) {
        if ($GuiProcess.HasExited -or -not [AifsSandboxWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $ProcessId, $ExactTitle)) { $Outcome = "failed"; throw "window_not_stable" }
        if ([AifsSandboxWindowApi]::GetImagePath($ProcessId) -cne $ExpectedImagePath) { $Outcome = "failed"; throw "process_image_changed" }
        Start-Sleep -Milliseconds 200
    }
    if ($StableTimer.Elapsed.TotalSeconds -lt $StableMinimumSeconds) { $Outcome = "timed_out"; throw "stable_window_timeout" }
    if ($GuiProcess.HasExited -or -not [AifsSandboxWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $ProcessId, $ExactTitle)) { $Outcome = "failed"; throw "window_not_stable" }
    if ([AifsSandboxWindowApi]::GetImagePath($ProcessId) -cne $ExpectedImagePath) { $Outcome = "failed"; throw "process_image_changed" }
    $StableTimer.Stop()
    $StableDurationSeconds = [Math]::Round($StableTimer.Elapsed.TotalSeconds, 3)
    $StableVerifiedAt = Get-UtcTimestamp
    $FirstLaunchVerified = $true
    Set-Phase "first_launch_verified"

    Set-Phase "cleanup"
    $CleanupStartedAt = Get-UtcTimestamp
    if (-not $GuiProcess.HasExited -and [AifsSandboxWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $ProcessId, $ExactTitle)) {
        $CleanupCloseRequested = $GuiProcess.CloseMainWindow()
        if (-not $CleanupCloseRequested) { $CleanupCloseRequested = [AifsSandboxWindowApi]::CloseOwnedWindow($VerifiedWindow, $ProcessId, $ExactTitle) }
    }
    $GracefulCloseSucceeded = $GuiProcess.WaitForExit($CleanupTimeoutSeconds * 1000)
    if (-not $GracefulCloseSucceeded) {
        $ForcedOwnedProcessCleanup = $true
        $Warnings.Add("owned_gui_fallback_kill_used")
        $GuiProcess.Kill()
        $GuiProcess.WaitForExit(5000) | Out-Null
    }
    $CleanupProcessExited = $GuiProcess.HasExited
    $CleanupFinishedAt = Get-UtcTimestamp
    if (-not $CleanupProcessExited) { $Outcome = "failed"; throw "owned_gui_cleanup_failed" }

    $Status = "passed"
    $Outcome = "passed"
    $ExitCode = 0
    Set-Phase "completed"
    Write-LifecycleLog "controlled_install_launch_passed"
}
catch {
    if ($Errors.Count -eq 0) { $Errors.Add(([string]$_.Exception.Message -replace "[^A-Za-z0-9_]", "_")) }
    if ($Outcome -eq "infrastructure_error" -and $HashVerified) { $Outcome = "failed" }
    if ($GuiProcess -and -not $GuiProcess.HasExited) {
        try {
            if ($Phase -ne "cleanup") { Set-Phase "cleanup"; $CleanupStartedAt = Get-UtcTimestamp }
            if ($VerifiedWindow -ne [IntPtr]::Zero -and [AifsSandboxWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $GuiProcess.Id, $ExactTitle)) {
                $CleanupCloseRequested = $GuiProcess.CloseMainWindow()
                if (-not $CleanupCloseRequested) { $CleanupCloseRequested = [AifsSandboxWindowApi]::CloseOwnedWindow($VerifiedWindow, $GuiProcess.Id, $ExactTitle) }
            }
            $GracefulCloseSucceeded = $GuiProcess.WaitForExit($CleanupTimeoutSeconds * 1000)
            if (-not $GracefulCloseSucceeded) {
                $ForcedOwnedProcessCleanup = $true
                $Warnings.Add("owned_gui_fallback_kill_used")
                $GuiProcess.Kill()
                $GuiProcess.WaitForExit(5000) | Out-Null
            }
            $CleanupProcessExited = $GuiProcess.HasExited
            $CleanupFinishedAt = Get-UtcTimestamp
        } catch { $Warnings.Add("owned_gui_cleanup_failed") }
    }
    Set-Phase "completed"
    Write-LifecycleLog "controlled_install_launch_failed"
}
finally {
    $CompletedAt = Get-UtcTimestamp
    if (-not (Test-Path -LiteralPath $GuestSystemPath -PathType Leaf)) { Write-AtomicJson $GuestSystemPath $GuestSystem }
    $FinalEvidence = [ordered]@{
        schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; launch_profile = $LaunchProfileName
        status = $Status; phase = "completed"; outcome = $Outcome; started_at = $StartedAt; updated_at = $CompletedAt; completed_at = $CompletedAt
        artifact = "artifact.exe"; installer_kind = "nsis_exe"; installer_started_at = $InstallerStartedAt; installer_finished_at = $InstallerFinishedAt
        installer_exit_code = $InstallerExitCode; install_duration_seconds = $InstallDurationSeconds; reboot_required = $RebootRequired; network_disabled = $NetworkDisabled
        artifact_sha256_host = $HostHash; artifact_sha256_guest = $GuestHash; hash_verified = $HashVerified; installation_passed = $InstallationPassed
        installed_marker_verified = $MarkerVerified; installed_payload_verified = $PayloadVerified
        installed_executable_found = $InstalledExecutableFound; installed_executable_sha256 = $InstalledGuiHash; installed_executable_hash_verified = $GuiVerified
        launch_started_at = $LaunchStartedAt; process_started_at = $ProcessStartedAt; window_detection_started_at = $WindowDetectionStartedAt
        window_detected_at = $WindowVisibleAt; stable_started_at = $StableStartedAt; stable_verified_at = $StableVerifiedAt
        cleanup_started_at = $CleanupStartedAt; cleanup_finished_at = $CleanupFinishedAt; process_started = $ProcessStarted; owned_process_id = $ProcessId; process_image_name = $ProcessImageName
        process_image_verified = $ProcessImagePathVerified; window_handle = $WindowHandleValue; window_handle_present = $WindowHandlePresent
        window_title = $WindowTitle; window_title_verified = $WindowTitleVerified; window_owned_by_process = $WindowOwnedByProcess; window_visible = $WindowVisible
        stable_window_duration_seconds = $StableDurationSeconds; first_launch_verified = $FirstLaunchVerified
        graceful_close_succeeded = $GracefulCloseSucceeded; forced_owned_process_cleanup = $ForcedOwnedProcessCleanup
        cleanup_process_exited = $CleanupProcessExited; phase_transitions = [object[]]$Transitions; errors = @($Errors); warnings = @($Warnings)
    }
    Write-AtomicJson $StatusPath $FinalEvidence
    Write-AtomicJson $LaunchEvidencePath $FinalEvidence
    Write-AtomicJson $CompletionPath ([ordered]@{
        schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; completed = $true
        status = $Status; outcome = $Outcome; phase = "completed"; completed_at = $CompletedAt
    })
}

exit $ExitCode
