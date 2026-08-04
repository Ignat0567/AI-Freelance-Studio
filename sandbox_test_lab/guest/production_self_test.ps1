$ErrorActionPreference = "Stop"
$InputRoot = "C:\SandboxTestLab\Input"
$GuestRoot = "C:\SandboxTestLab\Guest"
$EvidenceRoot = "C:\SandboxTestLab\Evidence"
$RequestPath = Join-Path $GuestRoot "request.json"
$StatusPath = Join-Path $EvidenceRoot "status.json"
$HeartbeatPath = Join-Path $EvidenceRoot "heartbeat.json"
$CompletionPath = Join-Path $EvidenceRoot "completion.json"
$GuestSystemPath = Join-Path $EvidenceRoot "guest-system.json"
$LifecyclePath = Join-Path $EvidenceRoot "lifecycle.log"
$ProductionEvidencePath = Join-Path $EvidenceRoot "production-evidence.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$SchemaVersion = 3
$Protocol = "aifs_production_self_test_v1"
$ProfileName = "aifs_production_beta_1_self_test"
$Product = "AI Freelance Studio"
$Version = "1.0.0-beta.1"
$ExpectedHash = "b72ad863f045f7877e9beb32826c2090d96bafe09f73b68c1892072cd4f1ef1f"
$ExpectedSize = 215224643
$ExactTitle = "AI Freelance Studio"
$InstalledExeName = "AI Freelance Studio.exe"
$BackendRelative = "resources\backend\freelancerstudio-backend\freelancerstudio-backend.exe"
$HealthEndpoint = "http://127.0.0.1:8080/health"
$ActualInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\AI Freelance Studio"
$WindowsRoot = [IO.Path]::GetFullPath($env:WINDIR).TrimEnd('\')
$CanonicalSystem32 = [IO.Path]::GetFullPath([Environment]::SystemDirectory).TrimEnd('\')
$CanonicalSysWOW64 = [IO.Path]::GetFullPath((Join-Path $WindowsRoot "SysWOW64")).TrimEnd('\')
$MaxOwnedDiagnostics = 64
$MinimumFreeSpaceBytes = 2GB
$GuestEvidenceReserveSeconds = 15
$MinimumCleanupReserveSeconds = 20

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class AifsProductionWindowApi {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll", SetLastError=true)] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr FindOwnedVisibleWindow(int[] processIds, string exactTitle, out int ownerPid) {
        HashSet<int> owners = new HashSet<int>(processIds);
        IntPtr found = IntPtr.Zero;
        int foundOwner = 0;
        EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (!owners.Contains((int)owner) || !IsWindowVisible(hwnd)) return true;
            StringBuilder title = new StringBuilder(512);
            GetWindowText(hwnd, title, title.Capacity);
            if (String.Equals(title.ToString(), exactTitle, StringComparison.Ordinal)) {
                found = hwnd; foundOwner = (int)owner; return false;
            }
            return true;
        }, IntPtr.Zero);
        ownerPid = foundOwner;
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
        } finally { CloseHandle(process); }
    }

    public static bool CloseOwnedWindow(IntPtr hwnd, int processId, string exactTitle) {
        return IsOwnedVisibleWindow(hwnd, processId, exactTitle) && PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
    }
}
'@

function Get-UtcTimestamp { [DateTime]::UtcNow.ToString("o") }
function Get-RemainingExecutionBudgetSeconds {
    if ($null -eq $script:GuestTerminalDeadlineUtc) { return 0.0 }
    return ($script:GuestTerminalDeadlineUtc - [DateTime]::UtcNow).TotalSeconds
}
function Get-EffectivePhaseDeadlineUtc {
    param([double] $ConfiguredMaximumSeconds, [double] $MandatoryFutureReserveSeconds)
    $Now = [DateTime]::UtcNow
    $Remaining = ($script:GuestTerminalDeadlineUtc - $Now).TotalSeconds
    $EffectiveSeconds = [Math]::Min($ConfiguredMaximumSeconds, $Remaining - $MandatoryFutureReserveSeconds)
    if ($EffectiveSeconds -le 0) { throw "insufficient_execution_budget" }
    return $Now.AddSeconds($EffectiveSeconds)
}
function Write-AtomicJson {
    param([string] $Path, [object] $Value)
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 10 -Compress), $Utf8NoBom)
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}
function Write-Lifecycle {
    param([string] $Message)
    $Timestamp = Get-UtcTimestamp
    [IO.File]::AppendAllText($LifecyclePath, "$Timestamp $Message$([Environment]::NewLine)", $Utf8NoBom)
    return $Timestamp
}
function Set-Phase {
    param([string] $Value)
    $script:Phase = $Value
    Write-AtomicJson $HeartbeatPath ([ordered]@{ schema_version = $SchemaVersion; protocol = $Protocol; run_id = $script:RunId; phase = $Value; updated_at = Get-UtcTimestamp })
}
function Assert-ExactProperties {
    param([object] $Value, [string[]] $Expected, [string] $Name)
    if ((@($Value.PSObject.Properties.Name | Sort-Object) -join "|") -cne (@($Expected | Sort-Object) -join "|")) { throw "${Name}_contract_invalid" }
}
function Convert-ExitCodeToHex {
    param([int] $Code)
    return [BitConverter]::ToUInt32([BitConverter]::GetBytes($Code), 0).ToString("x8")
}
function Convert-SafeEventHex {
    param([string] $Value, [int] $MaximumDigits, [bool] $PadToEight = $false)
    if ([String]::IsNullOrWhiteSpace($Value)) { return "" }
    $Normalized = $Value.Trim().ToLowerInvariant() -replace '^0x', ''
    if ($Normalized -notmatch "^[0-9a-f]{1,$MaximumDigits}$") { return "" }
    if ($PadToEight) { return $Normalized.PadLeft(8, '0') }
    return $Normalized
}
function Get-InstallerCrashDiagnostic {
    param([DateTime] $StartTimeUtc, [DateTime] $EndTimeUtc)
    $Result = [ordered]@{
        crash_event_found = $false; faulting_application_basename = "unavailable"; faulting_module_basename = "unavailable"
        exception_code = ""; fault_offset = ""; wer_event_type = "unavailable"; installer_diagnostic_source = "none"
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    if ($null -ne $script:GuestTerminalDeadlineUtc) {
        $EvidenceDeadline = $script:GuestTerminalDeadlineUtc.AddSeconds(-$GuestEvidenceReserveSeconds)
        if ($EvidenceDeadline -lt $Deadline) { $Deadline = $EvidenceDeadline }
    }
    do {
        $Events = @()
        foreach ($Query in @(
            @{ LogName = "Application"; ProviderName = "Application Error"; Id = 1000; StartTime = $StartTimeUtc; EndTime = $EndTimeUtc },
            @{ LogName = "Application"; ProviderName = "Windows Error Reporting"; Id = 1001; StartTime = $StartTimeUtc; EndTime = $EndTimeUtc }
        )) {
            try { $Events += @(Get-WinEvent -FilterHashtable $Query -ErrorAction SilentlyContinue) } catch { }
        }
        foreach ($Event in @($Events | Sort-Object TimeCreated -Descending)) {
            try {
                [xml]$Xml = $Event.ToXml()
                $Data = @{}
                foreach ($Node in @($Xml.Event.EventData.Data)) {
                    $Name = [string]$Node.Name
                    if (-not [String]::IsNullOrWhiteSpace($Name)) { $Data[$Name] = [string]$Node.'#text' }
                }
                $ApplicationValue = ""
                foreach ($Name in @("AppName", "FaultingApplicationName", "P1")) {
                    if ($Data.ContainsKey($Name) -and -not [String]::IsNullOrWhiteSpace($Data[$Name])) { $ApplicationValue = $Data[$Name]; break }
                }
                $ApplicationBasename = [IO.Path]::GetFileName($ApplicationValue)
                if ($ApplicationBasename -cne "artifact.exe") { continue }
                $Result.crash_event_found = $true
                $Result.faulting_application_basename = "artifact.exe"
                if ([int]$Event.Id -eq 1000) {
                    $Result.installer_diagnostic_source = "application_error_1000"
                    $ModuleValue = ""
                    foreach ($Name in @("ModuleName", "FaultingModuleName")) { if ($Data.ContainsKey($Name)) { $ModuleValue = $Data[$Name]; break } }
                    $ModuleBasename = [IO.Path]::GetFileName($ModuleValue)
                    if ($ModuleBasename -match '^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,127}$') { $Result.faulting_module_basename = $ModuleBasename }
                    foreach ($Name in @("ExceptionCode", "Exception code")) { if ($Data.ContainsKey($Name)) { $Result.exception_code = Convert-SafeEventHex $Data[$Name] 8 $true; break } }
                    foreach ($Name in @("FaultingOffset", "Fault offset")) { if ($Data.ContainsKey($Name)) { $Result.fault_offset = Convert-SafeEventHex $Data[$Name] 16; break } }
                } else {
                    $Result.installer_diagnostic_source = "windows_error_reporting_1001"
                    $EventType = if ($Data.ContainsKey("EventType")) { $Data["EventType"].ToLowerInvariant() } else { "" }
                    if ($EventType -in @("appcrash", "bex", "bex64")) { $Result.wer_event_type = $EventType }
                }
                return [pscustomobject]$Result
            } catch { }
        }
        if ([DateTime]::UtcNow -lt $Deadline) { Start-Sleep -Milliseconds 250 }
        $EndTimeUtc = [DateTime]::UtcNow
    } while ([DateTime]::UtcNow -lt $Deadline)
    return [pscustomobject]$Result
}
function Assert-NonReparsePathChain {
    param([string] $Path)
    $Current = [IO.Path]::GetFullPath($Path)
    while ($null -ne $Current) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "installed_path_ancestor_reparse" }
        }
        $Parent = [IO.Directory]::GetParent($Current)
        $Current = if ($null -eq $Parent) { $null } else { $Parent.FullName }
    }
}
function Assert-ContainedRegularFile {
    param([string] $Root, [string] $Relative)
    Assert-NonReparsePathChain $Root
    $CanonicalRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $Candidate = [IO.Path]::GetFullPath((Join-Path $CanonicalRoot $Relative))
    if (-not $Candidate.StartsWith("$CanonicalRoot\", [StringComparison]::OrdinalIgnoreCase)) { throw "installed_file_containment_invalid" }
    $Item = Get-Item -LiteralPath $Candidate -Force
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or [IO.Path]::GetFullPath($Item.FullName) -cne $Candidate) { throw "installed_file_not_regular" }
    return $Candidate
}
function Test-ImageContained {
    param([string] $Image)
    if ([String]::IsNullOrWhiteSpace($Image)) { return $false }
    try {
        $Root = [IO.Path]::GetFullPath($ActualInstallRoot).TrimEnd('\')
        $Full = [IO.Path]::GetFullPath($Image)
        if (-not $Full.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) { return $false }
        Assert-NonReparsePathChain $Full
        $Item = Get-Item -LiteralPath $Full -Force
        return -not $Item.PSIsContainer -and ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
    } catch { return $false }
}

function Test-CanonicalPathContained {
    param([string] $Path, [string] $Root)
    try {
        $Full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
        $CanonicalRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
        return $Full.Equals($CanonicalRoot, [StringComparison]::OrdinalIgnoreCase) -or $Full.StartsWith("$CanonicalRoot\", [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}
function Get-ImageSafety {
    param([string] $Image)
    $Regular = $false; $ReparseFree = $false
    if ([String]::IsNullOrWhiteSpace($Image)) { return [pscustomobject]@{ Regular = $false; ReparseFree = $false } }
    try {
        $Full = [IO.Path]::GetFullPath($Image)
        $Item = Get-Item -LiteralPath $Full -Force
        $Regular = -not $Item.PSIsContainer -and ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        if ($Regular) { Assert-NonReparsePathChain $Full; $ReparseFree = $true }
    } catch { $Regular = $false; $ReparseFree = $false }
    return [pscustomobject]@{ Regular = $Regular; ReparseFree = $ReparseFree }
}
function Get-SafeAuthenticodeDiagnostic {
    param([string] $Image, [bool] $SafelyAvailable)
    if (-not $SafelyAvailable) { return [pscustomobject]@{ Status = "unavailable"; MicrosoftSigned = $null } }
    try {
        $Signature = Get-AuthenticodeSignature -LiteralPath $Image -ErrorAction Stop
        $Status = switch ($Signature.Status.ToString()) {
            "Valid" { "valid" }
            "NotSigned" { "not_signed" }
            "HashMismatch" { "hash_mismatch" }
            "NotTrusted" { "not_trusted" }
            default { "unknown_error" }
        }
        $MicrosoftSigned = $false
        if ($Status -ceq "valid" -and $null -ne $Signature.SignerCertificate) {
            $Subject = $Signature.SignerCertificate.SubjectName.Decode([Security.Cryptography.X509Certificates.X500DistinguishedNameFlags]::UseCommas)
            $MicrosoftSigned = $Subject -match '(?:^|,\s*)O\s*=\s*Microsoft Corporation(?:\s*,|$)'
        }
        return [pscustomobject]@{ Status = $Status; MicrosoftSigned = [bool]$MicrosoftSigned }
    } catch { return [pscustomobject]@{ Status = "unknown_error"; MicrosoftSigned = $null } }
}
function Get-OwnedDiagnostic {
    param([string] $Image, [int] $Depth, [DateTime] $ActualStart)
    $Basename = if ([String]::IsNullOrWhiteSpace($Image)) { "unavailable" } else { [IO.Path]::GetFileName($Image) }
    if ([String]::IsNullOrWhiteSpace($Basename) -or $Basename -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,127}$') { $Basename = "unavailable" }
    $Safety = Get-ImageSafety $Image
    $Safe = $Safety.Regular -and $Safety.ReparseFree
    if ([String]::IsNullOrWhiteSpace($Image)) { $Origin = "unavailable" }
    elseif ((Test-CanonicalPathContained $Image $ActualInstallRoot) -and $Safe) { $Origin = "verified_install_root" }
    elseif (Test-CanonicalPathContained $Image $CanonicalSystem32) { $Origin = "canonical_system32" }
    elseif (Test-CanonicalPathContained $Image $CanonicalSysWOW64) { $Origin = "canonical_syswow64" }
    elseif (Test-CanonicalPathContained $Image $WindowsRoot) { $Origin = "other_windows_directory" }
    else { $Origin = "outside_untrusted" }
    if ($Depth -eq 0) { $Role = "root" }
    elseif ($Origin -ceq "verified_install_root" -and $Basename -ceq "freelancerstudio-backend.exe") { $Role = "backend_candidate" }
    elseif ($Origin -ceq "verified_install_root" -and $Basename -ceq $InstalledExeName) { $Role = "electron_child" }
    elseif ($Origin -in @("canonical_system32", "canonical_syswow64") -and $Safety.Regular -and $Safety.ReparseFree) { $Role = "system_helper" }
    else { $Role = "unknown" }
    $Signature = Get-SafeAuthenticodeDiagnostic $Image $Safe
    $Hash = ""
    if ($Safe -and $Origin -cne "verified_install_root") { try { $Hash = (Get-FileHash -LiteralPath $Image -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant() } catch { $Hash = "" } }
    $InstallImage = $Origin -ceq "verified_install_root"
    return [ordered]@{
        process_role = $Role; image_basename = $Basename; image_origin = $Origin
        image_regular_file = [bool]$Safety.Regular; image_reparse_free = [bool]$Safety.ReparseFree; image_hash = $Hash
        authenticode_status = $Signature.Status; microsoft_signed = $Signature.MicrosoftSigned
        parent_relation_verified = $true; creation_after_launch = [bool]($ActualStart -ge $script:LaunchProcessStart)
        eligible_for_gui_verification = [bool]($InstallImage -and $Role -in @("root", "electron_child"))
        eligible_for_backend_verification = [bool]($InstallImage -and $Role -ceq "backend_candidate")
        eligible_for_cleanup = [bool]$InstallImage
    }
}
function Test-TrustedAuxiliaryDiagnostic {
    param([string] $Image, [object] $Diagnostic)
    try {
        $ExactImage = [IO.Path]::GetFullPath((Join-Path $CanonicalSystem32 "conhost.exe"))
        $FullImage = [IO.Path]::GetFullPath($Image)
    } catch { return $false }
    return $FullImage.Equals($ExactImage, [StringComparison]::OrdinalIgnoreCase) -and
        ([IO.Path]::GetFileName($FullImage)).Equals("conhost.exe", [StringComparison]::OrdinalIgnoreCase) -and
        $Diagnostic.image_origin -ceq "canonical_system32" -and $Diagnostic.image_regular_file -and
        $Diagnostic.image_reparse_free -and $Diagnostic.authenticode_status -ceq "valid" -and
        $Diagnostic.microsoft_signed -eq $true -and $Diagnostic.parent_relation_verified -and
        $Diagnostic.creation_after_launch -and $Diagnostic.process_role -ceq "system_helper" -and
        -not $Diagnostic.eligible_for_gui_verification -and -not $Diagnostic.eligible_for_backend_verification -and
        -not $Diagnostic.eligible_for_cleanup
}

$Owned = @{}
function Add-OwnedProcess {
    param([Diagnostics.Process] $Process, [int] $Depth, [DateTime] $ExpectedStart)
    $ActualStart = $Process.StartTime.ToUniversalTime()
    if ([Math]::Abs(($ActualStart - $ExpectedStart.ToUniversalTime()).TotalSeconds) -gt 2) { throw "owned_process_creation_mismatch" }
    if ($Owned.Count -ge $MaxOwnedDiagnostics) { $script:DiagnosticLimitExceeded = $true; return }
    $Image = [AifsProductionWindowApi]::GetImagePath($Process.Id)
    $Diagnostic = Get-OwnedDiagnostic $Image $Depth $ActualStart
    $Owned[$Process.Id] = [pscustomobject]@{
        Process = $Process; StartTime = $ActualStart; Depth = $Depth; Diagnostic = $Diagnostic
        TrustedAuxiliary = [bool](Test-TrustedAuxiliaryDiagnostic $Image $Diagnostic)
        EligibleForGui = [bool]$Diagnostic.eligible_for_gui_verification
        EligibleForBackend = [bool]$Diagnostic.eligible_for_backend_verification
        EligibleForCleanup = [bool]$Diagnostic.eligible_for_cleanup
    }
}
function Update-OwnedTree {
    $Pending = New-Object System.Collections.Generic.Queue[int]
    foreach ($PidValue in @($Owned.Keys)) { $Pending.Enqueue([int]$PidValue) }
    $Visited = @{}
    while ($Pending.Count -gt 0) {
        $ParentId = $Pending.Dequeue()
        if ($Visited.ContainsKey($ParentId)) { continue }
        $Visited[$ParentId] = $true
        $ParentDepth = [int]$Owned[$ParentId].Depth
        $Children = @(Get-CimInstance -Query "SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process WHERE ParentProcessId=$ParentId" -ErrorAction Stop)
        foreach ($Child in $Children) {
            $ChildId = [int]$Child.ProcessId
            if (-not $Owned.ContainsKey($ChildId)) {
                try {
                    $ChildProcess = [Diagnostics.Process]::GetProcessById($ChildId)
                    $Created = if ($Child.CreationDate -is [DateTime]) { ([DateTime]$Child.CreationDate).ToUniversalTime() } else { [Management.ManagementDateTimeConverter]::ToDateTime([string]$Child.CreationDate).ToUniversalTime() }
                    Add-OwnedProcess $ChildProcess ($ParentDepth + 1) $Created
                } catch [ArgumentException] { continue }
            }
            if ($Owned.ContainsKey($ChildId)) { $Pending.Enqueue($ChildId) }
        }
    }
}
function Assert-OwnedClassification {
    $script:OwnedTreeCount = $Owned.Count
    $script:AllImagesContained = @($Owned.Values | Where-Object { $_.Diagnostic.image_origin -cne "verified_install_root" -and -not $_.TrustedAuxiliary }).Count -eq 0
    if ($script:DiagnosticLimitExceeded) { throw "owned_process_diagnostic_limit_exceeded" }
    if (-not $script:AllImagesContained) {
        $script:WindowHandleValue = $null; $script:WindowTitle = ""; $script:WindowVisible = $false
        $script:WindowOwnerPid = $null; $script:WindowOwnerInTree = $false; $script:StableDuration = $null
        $script:WindowDetectedAt = $null; $script:StableStartedAt = $null; $script:StableVerifiedAt = $null
        $script:FirstLaunchVerified = $false; $script:GuiPassed = $false
        $script:BackendTracked = $false; $script:BackendImageContained = $false; $script:BackendStatus = "not_ready"
        $script:BackendHttpStatus = $null; $script:BackendResponseStatus = ""; $script:BackendResponseService = ""
        $script:BackendEndpointVerified = $false; $script:BackendPid = $null
        $script:BackendPidOwnershipVerified = $false; $script:BackendImageVerified = $false
        $script:BackendStartedAt = $null; $script:BackendReadyAt = $null; $script:FullyReady = $false
        throw "owned_process_image_outside_install_root"
    }
}
function Test-OwnedCreation {
    param([object] $Entry)
    try { return [Math]::Abs(($Entry.Process.StartTime.ToUniversalTime() - $Entry.StartTime).TotalSeconds) -lt 0.001 } catch { return $false }
}

$StartedAt = Get-UtcTimestamp
$GuestTerminalDeadlineUtc = $null; $GuestTerminalDeadlineText = ""
$RunId = "unknown"
$Phase = "installing"
$Status = "failed"
$Outcome = "infrastructure_error"
$OverallReadiness = "installation_failed"
$InstallerStartedAt = $null; $ProcessStartReturnedAt = $null; $InstallerFinishedAt = $null; $LaunchStartedAt = $null; $ProcessStartedAt = $null
$WindowDetectedAt = $null; $StableStartedAt = $null; $StableVerifiedAt = $null; $BackendStartedAt = $null; $BackendReadyAt = $null
$CleanupStartedAt = $null; $CleanupFinishedAt = $null; $CompletedAt = $null
$GuestHash = ""; $HostArtifactVerified = $true; $GuestArtifactVerified = $false; $NetworkDisabled = $false
$InstallerExitCode = $null; $InstallerExitCodeHex = ""; $InstallDurationSeconds = $null
$InstallerProcessStarted = $false; $InstallerProcessExited = $false; $CrashEventFound = $false
$FaultingApplicationBasename = "unavailable"; $FaultingModuleBasename = "unavailable"; $ExceptionCode = ""; $FaultOffset = ""
$WerEventType = "unavailable"; $InstallerDiagnosticSource = "none"
$RebootRequired = $false; $InstalledExeFound = $false; $InstalledExeHash = ""
$InstalledExeRegular = $false; $InstalledExeNonReparse = $false; $InstallRootVerified = $false; $InstallationPassed = $false
$RootPid = $null; $OwnedTreeCount = $null; $AllImagesContained = $false; $RootProcessRetained = $false
$OwnedDescendantDiagnostics = @(); $DiagnosticLimitExceeded = $false; $LaunchProcessStart = [DateTime]::MinValue
$WindowHandleValue = $null; $WindowTitle = ""; $WindowVisible = $false; $WindowOwnerPid = $null; $WindowOwnerInTree = $false
$StableDuration = $null; $FirstLaunchVerified = $false; $GuiPassed = $false
$BackendRequired = $true; $BackendTracked = $false; $BackendImageContained = $false; $BackendStatus = "not_ready"
$BackendHttpStatus = $null; $BackendResponseStatus = ""; $BackendResponseService = ""; $BackendEndpointVerified = $false; $FullyReady = $false
$BackendPid = $null; $BackendPidOwnershipVerified = $false; $BackendImageVerified = $false
$ProductionCleanupMethod = ""; $ProductionTaskkillObserved = $false; $TaskkillWatcher = $null; $TaskkillSource = ""
$GracefulCloseAttempted = $false; $GracefulCleanupSucceeded = $false; $FallbackKillUsed = $false; $OwnedProcessesExited = $false; $AuxiliaryProcessesExited = $true; $CleanupComplete = $false
$Errors = New-Object System.Collections.Generic.List[string]; $Warnings = New-Object System.Collections.Generic.List[string]
$InstallerProcess = $null; $RootProcess = $null; $VerifiedWindow = [IntPtr]::Zero; $ExitCode = 1
$GuestSystem = [ordered]@{ os_version = [Environment]::OSVersion.VersionString; architecture = [Environment]::Is64BitOperatingSystem.ToString(); powershell_version = $PSVersionTable.PSVersion.ToString() }

try {
    New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
    [IO.File]::WriteAllText($LifecyclePath, "", $Utf8NoBom)
    $null = Write-Lifecycle "production_self_test_started"
    $Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-ExactProperties $Request @("schema_version", "protocol", "run_id", "profile_name", "product", "version", "artifact_name", "artifact_size", "artifact_sha256_host", "guest_terminal_deadline_utc", "profile") "request"
    if ($Request.schema_version -ne 3 -or $Request.protocol -cne $Protocol -or $Request.profile_name -cne $ProfileName -or $Request.product -cne $Product -or $Request.version -cne $Version) { throw "request_identity_invalid" }
    $RunId = [string]$Request.run_id
    $ParsedRunId = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId) { throw "request_run_id_invalid" }
    $GuestTerminalDeadlineText = [string]$Request.guest_terminal_deadline_utc
    if ($GuestTerminalDeadlineText -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$') { throw "guest_terminal_deadline_invalid" }
    try {
        $GuestTerminalDeadlineUtc = [DateTime]::ParseExact(
            $GuestTerminalDeadlineText, "yyyy-MM-ddTHH:mm:ss.ffffffZ",
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        )
    } catch { throw "guest_terminal_deadline_invalid" }
    if ($GuestTerminalDeadlineUtc -le [DateTime]::UtcNow) { throw "insufficient_execution_budget" }
    if ($Request.artifact_name -cne "artifact.exe" -or [long]$Request.artifact_size -ne $ExpectedSize -or ([string]$Request.artifact_sha256_host).ToLowerInvariant() -cne $ExpectedHash) { throw "artifact_contract_invalid" }
    $Profile = $Request.profile
    Assert-ExactProperties $Profile @("installer_kind", "silent_argument", "expected_install_scope", "reboot_policy", "network_policy", "expected_install_root", "installed_executable_relative", "exact_window_title", "backend_executable_relative", "backend_endpoint", "backend_required", "install_timeout_seconds", "launch_timeout_seconds", "minimum_stable_duration_seconds", "backend_readiness_seconds", "cleanup_timeout_seconds") "profile"
    if ($Profile.installer_kind -cne "nsis_exe" -or $Profile.silent_argument -cne "/S" -or $Profile.expected_install_scope -cne "user" -or $Profile.reboot_policy -cne "forbid" -or $Profile.network_policy -cne "disabled") { throw "installation_policy_invalid" }
    if ($Profile.expected_install_root -cne "sandbox_user_local_app_data\Programs\AI Freelance Studio" -or $Profile.installed_executable_relative -cne $InstalledExeName -or $Profile.exact_window_title -cne $ExactTitle -or $Profile.backend_executable_relative -cne $BackendRelative -or $Profile.backend_endpoint -cne $HealthEndpoint -or $Profile.backend_required -ne $true) { throw "profile_identity_invalid" }
    if ([int]$Profile.install_timeout_seconds -ne 600 -or [int]$Profile.launch_timeout_seconds -ne 60 -or [int]$Profile.minimum_stable_duration_seconds -ne 5 -or [int]$Profile.backend_readiness_seconds -ne 30 -or [int]$Profile.cleanup_timeout_seconds -ne 15) { throw "profile_timeout_invalid" }
    $NetworkDisabled = -not [Net.NetworkInformation.NetworkInterface]::GetIsNetworkAvailable()
    if (-not $NetworkDisabled) { throw "network_not_disabled" }
    Write-AtomicJson $GuestSystemPath $GuestSystem

    $ArtifactPath = Join-Path $InputRoot "artifact.exe"
    $ArtifactItem = Get-Item -LiteralPath $ArtifactPath -Force
    if ($ArtifactItem.PSIsContainer -or ($ArtifactItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $ArtifactItem.Length -ne $ExpectedSize) { throw "guest_artifact_invalid" }
    $GuestHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $GuestArtifactVerified = $GuestHash -ceq $ExpectedHash
    if (-not $GuestArtifactVerified) { $Outcome = "failed"; throw "guest_artifact_hash_mismatch" }
    $CanonicalLocal = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $LocalDrive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($CanonicalLocal))
    if ($LocalDrive.AvailableFreeSpace -lt $MinimumFreeSpaceBytes) { throw "insufficient_local_free_space" }
    $CanonicalInstall = [IO.Path]::GetFullPath((Join-Path $CanonicalLocal "Programs\AI Freelance Studio"))
    if ([IO.Path]::GetFullPath($ActualInstallRoot) -cne $CanonicalInstall) { throw "install_root_invalid" }
    Assert-NonReparsePathChain $ActualInstallRoot
    if (Test-Path -LiteralPath $ActualInstallRoot) { throw "install_root_not_clean" }

    Set-Phase "installing"
    $InstallerFutureReserveSeconds = 60 + 5 + 30 + $MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds
    $InstallerDeadlineUtc = Get-EffectivePhaseDeadlineUtc 600 $InstallerFutureReserveSeconds
    $InstallStartInfo = New-Object Diagnostics.ProcessStartInfo
    $InstallStartInfo.FileName = $ArtifactPath
    $InstallStartInfo.Arguments = "/S"
    $InstallStartInfo.UseShellExecute = $false
    $InstallStartInfo.CreateNoWindow = $true
    $InstallerProcess = New-Object Diagnostics.Process
    $InstallerProcess.StartInfo = $InstallStartInfo
    $InstallerStartTimeUtc = [DateTime]::UtcNow
    $InstallerStartedAt = $InstallerStartTimeUtc.ToString("o")
    if (-not $InstallerProcess.Start()) { throw "installer_start_failed" }
    $ProcessStartReturnedTimeUtc = [DateTime]::UtcNow
    $ProcessStartReturnedAt = $ProcessStartReturnedTimeUtc.ToString("o")
    $InstallerProcessStarted = $true
    if ([DateTime]::UtcNow -ge $InstallerDeadlineUtc) {
        try {
            $InstallerProcess.Kill()
            $InstallerWaitMilliseconds = [Math]::Max(0, [Math]::Min(5000, [int](($GuestTerminalDeadlineUtc.AddSeconds(-$GuestEvidenceReserveSeconds) - [DateTime]::UtcNow).TotalMilliseconds)))
            $InstallerProcess.WaitForExit($InstallerWaitMilliseconds) | Out-Null
        } catch { $Warnings.Add("owned_installer_cleanup_failed") }
        $Outcome = "timed_out"; throw "installer_timed_out"
    }
    while (-not $InstallerProcess.WaitForExit(200)) {
        if ([DateTime]::UtcNow -ge $InstallerDeadlineUtc) {
            try {
                $InstallerProcess.Kill()
                $InstallerWaitMilliseconds = [Math]::Max(0, [Math]::Min(5000, [int](($GuestTerminalDeadlineUtc.AddSeconds(-$GuestEvidenceReserveSeconds) - [DateTime]::UtcNow).TotalMilliseconds)))
                $InstallerProcess.WaitForExit($InstallerWaitMilliseconds) | Out-Null
            } catch { $Warnings.Add("owned_installer_cleanup_failed") }
            if ($InstallerProcess.HasExited) {
                $InstallerFinishedTimeUtc = [DateTime]::UtcNow
                $InstallerProcessExited = $true; $InstallerFinishedAt = $InstallerFinishedTimeUtc.ToString("o")
                $InstallDurationSeconds = [Math]::Round(($InstallerFinishedTimeUtc - $InstallerStartTimeUtc).TotalSeconds, 3)
                $InstallerExitCode = $InstallerProcess.ExitCode; $InstallerExitCodeHex = Convert-ExitCodeToHex $InstallerExitCode
                if ($InstallerExitCode -ne 0) {
                    $CrashDiagnostic = Get-InstallerCrashDiagnostic $InstallerStartTimeUtc ([DateTime]::UtcNow)
                    $CrashEventFound = $CrashDiagnostic.crash_event_found; $FaultingApplicationBasename = $CrashDiagnostic.faulting_application_basename
                    $FaultingModuleBasename = $CrashDiagnostic.faulting_module_basename; $ExceptionCode = $CrashDiagnostic.exception_code
                    $FaultOffset = $CrashDiagnostic.fault_offset; $WerEventType = $CrashDiagnostic.wer_event_type
                    $InstallerDiagnosticSource = $CrashDiagnostic.installer_diagnostic_source
                }
            }
            $Outcome = "timed_out"; throw "installer_timed_out"
        }
    }
    $InstallerFinishedTimeUtc = [DateTime]::UtcNow
    $InstallerFinishedAt = $InstallerFinishedTimeUtc.ToString("o")
    $InstallerProcessExited = $true; $InstallDurationSeconds = [Math]::Round(($InstallerFinishedTimeUtc - $InstallerStartTimeUtc).TotalSeconds, 3)
    $InstallerExitCode = $InstallerProcess.ExitCode; $InstallerExitCodeHex = Convert-ExitCodeToHex $InstallerExitCode
    if ($InstallerExitCode -ne 0) {
        $CrashDiagnostic = Get-InstallerCrashDiagnostic $InstallerStartTimeUtc ([DateTime]::UtcNow)
        $CrashEventFound = $CrashDiagnostic.crash_event_found; $FaultingApplicationBasename = $CrashDiagnostic.faulting_application_basename
        $FaultingModuleBasename = $CrashDiagnostic.faulting_module_basename; $ExceptionCode = $CrashDiagnostic.exception_code
        $FaultOffset = $CrashDiagnostic.fault_offset; $WerEventType = $CrashDiagnostic.wer_event_type
        $InstallerDiagnosticSource = $CrashDiagnostic.installer_diagnostic_source
    }
    if ($InstallerFinishedTimeUtc -ge $InstallerDeadlineUtc) { $Outcome = "timed_out"; throw "installer_timed_out" }
    if ($InstallerExitCode -eq 1641 -or $InstallerExitCode -eq 3010) { $RebootRequired = $true; $Outcome = "reboot_required"; throw "reboot_forbidden" }
    if ($InstallerExitCode -ne 0) { $Outcome = "failed"; throw "installer_exit_nonzero" }
    Assert-NonReparsePathChain $ActualInstallRoot
    $InstalledExe = Assert-ContainedRegularFile $ActualInstallRoot $InstalledExeName
    $InstalledExeFound = $true; $InstalledExeRegular = $true; $InstalledExeNonReparse = $true; $InstallRootVerified = $true
    $InstalledExeHash = (Get-FileHash -LiteralPath $InstalledExe -Algorithm SHA256).Hash.ToLowerInvariant()
    $InstallationPassed = $true

    Set-Phase "launching"
    $LaunchDeadlineUtc = Get-EffectivePhaseDeadlineUtc 60 (5 + 30 + $MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds)
    $LaunchInfo = New-Object Diagnostics.ProcessStartInfo
    $LaunchInfo.FileName = $InstalledExe
    $LaunchInfo.UseShellExecute = $false
    $LaunchInfo.CreateNoWindow = $false
    $RootProcess = New-Object Diagnostics.Process
    $RootProcess.StartInfo = $LaunchInfo
    $LaunchStartedAt = Get-UtcTimestamp
    if (-not $RootProcess.Start()) { $OverallReadiness = "gui_launch_failed"; $Outcome = "failed"; throw "gui_start_failed" }
    $RootPid = $RootProcess.Id; $ProcessStartedAt = Get-UtcTimestamp; $LaunchProcessStart = $RootProcess.StartTime.ToUniversalTime()
    Add-OwnedProcess $RootProcess 0 $RootProcess.StartTime
    $RootProcessRetained = $true
    if ([DateTime]::UtcNow -ge $LaunchDeadlineUtc) { $OverallReadiness = "gui_launch_failed"; $Outcome = "timed_out"; throw "gui_launch_timed_out" }
    Set-Phase "window_detecting"
    while ([DateTime]::UtcNow -lt $LaunchDeadlineUtc) {
        if ($RootProcess.HasExited) { $OverallReadiness = "gui_launch_failed"; $Outcome = "failed"; throw "root_exited_early" }
        Update-OwnedTree
        Assert-OwnedClassification
        $Owner = 0
        $GuiPids = [int[]]@($Owned.GetEnumerator() | Where-Object { $_.Value.EligibleForGui } | ForEach-Object { [int]$_.Key })
        $VerifiedWindow = [AifsProductionWindowApi]::FindOwnedVisibleWindow($GuiPids, $ExactTitle, [ref]$Owner)
        if ($VerifiedWindow -ne [IntPtr]::Zero) { $WindowOwnerPid = $Owner; break }
        Start-Sleep -Milliseconds 200
    }
    if ($VerifiedWindow -eq [IntPtr]::Zero) { $OverallReadiness = "gui_launch_failed"; $Outcome = "timed_out"; throw "exact_owned_window_timeout" }
    $WindowHandleValue = $VerifiedWindow.ToInt64(); $WindowTitle = $ExactTitle; $WindowVisible = $true; $WindowOwnerInTree = $Owned.ContainsKey($WindowOwnerPid)
    $WindowDetectedAt = Get-UtcTimestamp; $StableStartedAt = Get-UtcTimestamp
    $StableTimer = [Diagnostics.Stopwatch]::StartNew()
    $StableDeadlineUtc = Get-EffectivePhaseDeadlineUtc 5 (30 + $MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds)
    while ([DateTime]::UtcNow -lt $StableDeadlineUtc) {
        if ($RootProcess.HasExited) { $OverallReadiness = "gui_launch_failed"; $Outcome = "failed"; throw "root_exited_during_stability" }
        Update-OwnedTree; Assert-OwnedClassification
        if (-not $Owned.ContainsKey($WindowOwnerPid) -or -not $Owned[$WindowOwnerPid].EligibleForGui -or -not (Test-OwnedCreation $Owned[$WindowOwnerPid]) -or -not [AifsProductionWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle)) { $OverallReadiness = "gui_launch_failed"; $Outcome = "failed"; throw "owned_window_not_stable" }
        Start-Sleep -Milliseconds 200
    }
    if ($StableTimer.Elapsed.TotalSeconds -lt 5 -or [DateTime]::UtcNow -lt $StableDeadlineUtc) { $OverallReadiness = "gui_launch_failed"; $Outcome = "timed_out"; throw "stable_window_timeout" }
    Update-OwnedTree; Assert-OwnedClassification
    if ($RootProcess.HasExited -or -not $Owned[$WindowOwnerPid].EligibleForGui -or -not (Test-OwnedCreation $Owned[$WindowOwnerPid]) -or -not [AifsProductionWindowApi]::IsOwnedVisibleWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle)) { $OverallReadiness = "gui_launch_failed"; $Outcome = "failed"; throw "owned_window_final_revalidation_failed" }
    $StableDuration = [Math]::Round($StableTimer.Elapsed.TotalSeconds, 3); $StableVerifiedAt = Get-UtcTimestamp
    $OwnedTreeCount = $Owned.Count; $AllImagesContained = $true; $FirstLaunchVerified = $true; $GuiPassed = $true

    Set-Phase "backend_checking"
    $BackendDeadlineUtc = Get-EffectivePhaseDeadlineUtc 30 ($MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds)
    $BackendStartedAt = Get-UtcTimestamp
    $BackendExpected = [IO.Path]::GetFullPath((Join-Path $ActualInstallRoot $BackendRelative))
    while ([DateTime]::UtcNow -lt $BackendDeadlineUtc) {
        if ($RootProcess.HasExited) { $OverallReadiness = "backend_not_ready"; $Outcome = "failed"; throw "root_exited_before_backend_ready" }
        Update-OwnedTree; Assert-OwnedClassification
        foreach ($Entry in @($Owned.Values)) {
            if (-not $Entry.Process.HasExited -and $Entry.EligibleForBackend) {
                $Image = [AifsProductionWindowApi]::GetImagePath($Entry.Process.Id)
                if ($null -ne $Image -and [IO.Path]::GetFullPath($Image) -ceq $BackendExpected -and [IO.Path]::GetFileName($Image) -ceq "freelancerstudio-backend.exe") {
                    $BackendPid = $Entry.Process.Id
                    $BackendTracked = $true
                    $BackendImageContained = Test-ImageContained $Image
                    $BackendImageVerified = $BackendImageContained
                    $BackendPidOwnershipVerified = $Owned.ContainsKey($BackendPid) -and (Test-OwnedCreation $Entry) -and $Entry.StartTime -ge $Owned[$RootPid].StartTime
                }
            }
        }
        if ($BackendTracked -and $BackendImageContained -and $BackendPidOwnershipVerified -and $BackendImageVerified) {
            try {
                $Http = [Net.HttpWebRequest]::Create($HealthEndpoint)
                $Http.Proxy = $null; $Http.Method = "GET"; $Http.Timeout = 1000; $Http.ReadWriteTimeout = 1000; $Http.AllowAutoRedirect = $false
                $Response = $Http.GetResponse()
                try {
                    $BackendHttpStatus = [int]$Response.StatusCode
                    $Reader = New-Object IO.StreamReader($Response.GetResponseStream())
                    $Body = $Reader.ReadToEnd(); $Reader.Dispose()
                    if ($Body.Length -le 4096) {
                        $Health = $Body | ConvertFrom-Json
                        $BackendResponseStatus = [string]$Health.status; $BackendResponseService = [string]$Health.service
                    }
                } finally { $Response.Dispose() }
                if ($BackendHttpStatus -eq 200 -and $BackendResponseStatus -ceq "ok" -and $BackendResponseService -ceq "FreelancerStudio") { $BackendEndpointVerified = $true; break }
            } catch [Net.WebException] { }
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $BackendEndpointVerified) { $OverallReadiness = "backend_not_ready"; $Outcome = "timed_out"; throw "backend_not_ready" }
    $BackendStatus = "ready"; $BackendReadyAt = Get-UtcTimestamp; $FullyReady = $true; $OverallReadiness = "fully_ready"
    $Status = "passed"; $Outcome = "passed"; $ExitCode = 0
}
catch {
    if ($Errors.Count -eq 0) { $Errors.Add(([string]$_.Exception.Message -replace "[^A-Za-z0-9_]", "_")) }
    if (-not $InstallationPassed) { $OverallReadiness = "installation_failed" }
    elseif (-not $GuiPassed -and $OverallReadiness -eq "installation_failed") { $OverallReadiness = "gui_launch_failed" }
    elseif ($GuiPassed -and -not $FullyReady) { $OverallReadiness = "backend_not_ready" }
    if ($Outcome -eq "infrastructure_error" -and $GuestArtifactVerified) { $Outcome = "failed" }
}
finally {
    if ($null -ne $InstallerProcess -and $InstallerProcessStarted -and -not $InstallerProcess.HasExited) {
        try {
            $InstallerProcess.Kill()
            $InstallerWaitMilliseconds = [Math]::Max(0, [Math]::Min(5000, [int](($GuestTerminalDeadlineUtc.AddSeconds(-$GuestEvidenceReserveSeconds) - [DateTime]::UtcNow).TotalMilliseconds)))
            $InstallerProcess.WaitForExit($InstallerWaitMilliseconds) | Out-Null
        } catch { $Warnings.Add("owned_installer_cleanup_failed") }
    }
    if ($Owned.Count -gt 0) {
        Set-Phase "cleanup"; $CleanupStartedAt = Get-UtcTimestamp
        try {
            $CleanupDeadlineUtc = Get-EffectivePhaseDeadlineUtc 15 $GuestEvidenceReserveSeconds
            $BackendOwnershipReady = $BackendPidOwnershipVerified -and $BackendImageVerified -and $null -ne $BackendPid -and $Owned.ContainsKey($BackendPid) -and $Owned[$BackendPid].EligibleForCleanup -and -not $Owned[$BackendPid].Process.HasExited -and (Test-OwnedCreation $Owned[$BackendPid])
            if ($BackendOwnershipReady -and $VerifiedWindow -ne [IntPtr]::Zero -and $null -ne $WindowOwnerPid -and $Owned.ContainsKey($WindowOwnerPid) -and (Test-OwnedCreation $Owned[$WindowOwnerPid])) {
                $ProductionCleanupMethod = "owned_backend_pid_tree"
                $TaskkillSource = "AifsProductionTaskkill_$RunId"
                $TaskkillQuery = "SELECT * FROM Win32_ProcessStartTrace WHERE ParentProcessID = $RootPid AND ProcessName = 'taskkill.exe'"
                $TaskkillWatcher = Register-WmiEvent -Query $TaskkillQuery -SourceIdentifier $TaskkillSource -ErrorAction Stop
                $GracefulCloseAttempted = [AifsProductionWindowApi]::CloseOwnedWindow($VerifiedWindow, $WindowOwnerPid, $ExactTitle)
            } elseif ($VerifiedWindow -ne [IntPtr]::Zero) {
                $Warnings.Add("graceful_close_withheld_backend_unverified")
            }
            while ([DateTime]::UtcNow -lt $CleanupDeadlineUtc -and @($Owned.Values | Where-Object { $_.EligibleForCleanup -and -not $_.Process.HasExited }).Count -gt 0) { Start-Sleep -Milliseconds 200 }
            $Remaining = @($Owned.Values | Where-Object { $_.EligibleForCleanup -and -not $_.Process.HasExited })
            if ($TaskkillWatcher) {
                $ProductionTaskkillObserved = @(Get-Event -SourceIdentifier $TaskkillSource -ErrorAction SilentlyContinue).Count -gt 0
            }
            $GracefulCleanupSucceeded = $Remaining.Count -eq 0
            if (-not $GracefulCleanupSucceeded) {
                $FallbackKillUsed = $true; $Warnings.Add("owned_tree_fallback_kill_used")
                foreach ($Entry in @($Remaining | Sort-Object Depth -Descending)) {
                    if (-not $Entry.Process.HasExited -and (Test-OwnedCreation $Entry)) {
                        $Entry.Process.Kill()
                        $CleanupWaitMilliseconds = [Math]::Max(0, [Math]::Min(5000, [int](($CleanupDeadlineUtc - [DateTime]::UtcNow).TotalMilliseconds)))
                        $Entry.Process.WaitForExit($CleanupWaitMilliseconds) | Out-Null
                    }
                }
            }
            $OwnedProcessesExited = @($Owned.Values | Where-Object { $_.EligibleForCleanup -and -not $_.Process.HasExited }).Count -eq 0
            while ([DateTime]::UtcNow -lt $CleanupDeadlineUtc -and @($Owned.Values | Where-Object { $_.TrustedAuxiliary -and -not $_.Process.HasExited }).Count -gt 0) { Start-Sleep -Milliseconds 200 }
            $AuxiliaryProcessesExited = @($Owned.Values | Where-Object { $_.TrustedAuxiliary -and -not $_.Process.HasExited }).Count -eq 0
            $CleanupComplete = $OwnedProcessesExited -and $AuxiliaryProcessesExited
        } catch {
            if ($_.Exception.Message -ceq "insufficient_execution_budget" -and -not $Errors.Contains("insufficient_execution_budget")) { $Errors.Add("insufficient_execution_budget") }
            $Warnings.Add("owned_tree_cleanup_failed")
        }
        finally {
            if ($TaskkillWatcher) {
                Unregister-Event -SourceIdentifier $TaskkillSource -ErrorAction SilentlyContinue
                Remove-Job -Id $TaskkillWatcher.Id -Force -ErrorAction SilentlyContinue
                Remove-Event -SourceIdentifier $TaskkillSource -ErrorAction SilentlyContinue
            }
        }
        $CleanupFinishedAt = Get-UtcTimestamp
    }
    $OwnedTreeCount = $Owned.Count
    $OwnedDescendantDiagnostics = @($Owned.Values | Sort-Object Depth | ForEach-Object { $_.Diagnostic })
    if ((Get-RemainingExecutionBudgetSeconds) -le 0 -and -not $Errors.Contains("insufficient_execution_budget")) {
        $Status = "failed"; $Outcome = "timed_out"; $FullyReady = $false; $ExitCode = 1; $Errors.Add("insufficient_execution_budget")
    }
    if ($Status -eq "passed" -and -not $CleanupComplete) { $Status = "failed"; $Outcome = "failed"; $FullyReady = $false; $OverallReadiness = "backend_not_ready"; $ExitCode = 1; $Errors.Add("owned_tree_cleanup_incomplete") }
    if (-not (Test-Path -LiteralPath $GuestSystemPath -PathType Leaf)) { Write-AtomicJson $GuestSystemPath $GuestSystem }
    if ($Status -eq "passed") {
        $terminalLifecycleMessage = "production_self_test_passed"
    }
    else {
        $terminalLifecycleMessage = "production_self_test_failed"
    }
    $TerminalLifecycleAt = Write-Lifecycle $terminalLifecycleMessage
    $CompletedAt = Get-UtcTimestamp
    if ([DateTime]::Parse($CompletedAt) -lt [DateTime]::Parse($TerminalLifecycleAt)) { $CompletedAt = $TerminalLifecycleAt }
    $Phase = "completed"
    Write-AtomicJson $HeartbeatPath ([ordered]@{ schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; phase = $Phase; updated_at = $CompletedAt })
    $Final = [ordered]@{
        schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; profile_name = $ProfileName; product = $Product; version = $Version
        guest_terminal_deadline_utc = $GuestTerminalDeadlineText
        status = $Status; outcome = $Outcome; overall_readiness = $OverallReadiness; started_at = $StartedAt; installer_started_at = $InstallerStartedAt
        process_start_returned_at = $ProcessStartReturnedAt; installer_finished_at = $InstallerFinishedAt; launch_started_at = $LaunchStartedAt; process_started_at = $ProcessStartedAt; window_detected_at = $WindowDetectedAt
        stable_started_at = $StableStartedAt; stable_verified_at = $StableVerifiedAt; backend_started_at = $BackendStartedAt; backend_ready_at = $BackendReadyAt
        cleanup_started_at = $CleanupStartedAt; cleanup_finished_at = $CleanupFinishedAt; completed_at = $CompletedAt; artifact_name = "artifact.exe"
        artifact_size = $ExpectedSize; artifact_sha256_host = $ExpectedHash; artifact_sha256_guest = $GuestHash; host_artifact_verified = $HostArtifactVerified
        guest_artifact_verified = $GuestArtifactVerified; network_disabled = $NetworkDisabled; installer_exit_code = $InstallerExitCode
        installer_exit_code_hex = $InstallerExitCodeHex; install_duration_seconds = $InstallDurationSeconds
        installer_process_started = $InstallerProcessStarted; installer_process_exited = $InstallerProcessExited; crash_event_found = $CrashEventFound
        faulting_application_basename = $FaultingApplicationBasename; faulting_module_basename = $FaultingModuleBasename
        exception_code = $ExceptionCode; fault_offset = $FaultOffset; wer_event_type = $WerEventType; installer_diagnostic_source = $InstallerDiagnosticSource
        reboot_required = $RebootRequired
        installed_exe_found = $InstalledExeFound; installed_exe_sha256 = $InstalledExeHash; installed_exe_regular = $InstalledExeRegular
        installed_exe_non_reparse = $InstalledExeNonReparse; install_root_verified = $InstallRootVerified; installation_passed = $InstallationPassed
        launch_root_pid = $RootPid; owned_tree_count = $OwnedTreeCount; all_images_contained = $AllImagesContained; root_process_retained = $RootProcessRetained
        window_handle = $WindowHandleValue; window_title = $WindowTitle; window_visible = $WindowVisible; window_owner_pid = $WindowOwnerPid
        window_owner_in_owned_tree = $WindowOwnerInTree; stable_duration_seconds = $StableDuration; first_launch_verified = $FirstLaunchVerified; gui_passed = $GuiPassed
        backend_required = $BackendRequired; backend_process_tracked = $BackendTracked; backend_image_contained = $BackendImageContained; backend_status = $BackendStatus
        backend_http_status = $BackendHttpStatus; backend_response_status = $BackendResponseStatus; backend_response_service = $BackendResponseService
        backend_endpoint_verified = $BackendEndpointVerified; backend_pid = $BackendPid; backend_pid_ownership_verified = $BackendPidOwnershipVerified
        backend_image_verified = $BackendImageVerified; fully_ready = $FullyReady; production_cleanup_method = $ProductionCleanupMethod
        production_taskkill_observed = $ProductionTaskkillObserved; graceful_close_attempted = $GracefulCloseAttempted
        graceful_cleanup_succeeded = $GracefulCleanupSucceeded; fallback_kill_used = $FallbackKillUsed; owned_processes_exited = $OwnedProcessesExited
        auxiliary_processes_exited = $AuxiliaryProcessesExited; cleanup_complete = $CleanupComplete; owned_descendant_diagnostics = @($OwnedDescendantDiagnostics); errors = @($Errors); warnings = @($Warnings)
    }
    Write-AtomicJson $ProductionEvidencePath $Final
    Write-AtomicJson $StatusPath $Final
    $CompletionCreatedAt = Get-UtcTimestamp
    Write-AtomicJson $CompletionPath ([ordered]@{ schema_version = $SchemaVersion; protocol = $Protocol; run_id = $RunId; completed = $true; status = $Status; outcome = $Outcome; overall_readiness = $OverallReadiness; completion_created_at = $CompletionCreatedAt })
}
exit $ExitCode
