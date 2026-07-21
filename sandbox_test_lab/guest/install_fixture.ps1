$ErrorActionPreference = "Stop"
$InputRoot = "C:\SandboxTestLab\Input"
$GuestRoot = "C:\SandboxTestLab\Guest"
$EvidenceRoot = "C:\SandboxTestLab\Evidence"
$RequestPath = Join-Path $GuestRoot "request.json"
$StatusPath = Join-Path $EvidenceRoot "status.json"
$HeartbeatPath = Join-Path $EvidenceRoot "heartbeat.json"
$GuestSystemPath = Join-Path $EvidenceRoot "guest-system.json"
$CompletionPath = Join-Path $EvidenceRoot "completion.json"
$InstallerLogPath = Join-Path $EvidenceRoot "installer.log"
$InstallationEvidencePath = Join-Path $EvidenceRoot "installation-evidence.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$SchemaVersion = 2
$Protocol = "controlled_fixture_installation_v1"
$FixtureProfile = "aifs_sandbox_fixture_v1"
$LogicalInstallRoot = "sandbox_user_local_app_data\Programs\AIFS Sandbox Fixture"
$ActualInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\AIFS Sandbox Fixture"

function Get-UtcTimestamp {
    return [DateTime]::UtcNow.ToString("o")
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [Parameter(Mandatory = $true)] [object] $Value
    )
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $Json = $Value | ConvertTo-Json -Depth 8 -Compress
    [System.IO.File]::WriteAllText($Temporary, $Json, $Utf8NoBom)
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}

function Write-InstallerLog {
    param([string] $Message)
    $SafeMessage = $Message -replace "[^A-Za-z0-9_:\-. ]", "_"
    [System.IO.File]::AppendAllText(
        $InstallerLogPath,
        "$(Get-UtcTimestamp) $SafeMessage$([Environment]::NewLine)",
        $Utf8NoBom
    )
}

function Assert-ExactProperties {
    param([object] $Value, [string[]] $Expected, [string] $Name)
    $Actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $Wanted = @($Expected | Sort-Object)
    if (($Actual -join "|") -ne ($Wanted -join "|")) {
        throw "$Name contract is invalid"
    }
}

function Write-Heartbeat {
    param([string] $Phase, [string] $RunId)
    Write-AtomicJson -Path $HeartbeatPath -Value ([ordered]@{
        schema_version = $SchemaVersion
        protocol = $Protocol
        run_id = $RunId
        phase = $Phase
        updated_at = Get-UtcTimestamp
    })
}

$StartedAt = Get-UtcTimestamp
$CompletedAt = $null
$RunId = "unknown"
$ArtifactName = "artifact.exe"
$HostHash = ""
$GuestHash = ""
$HashVerified = $false
$Status = "failed"
$Outcome = "infrastructure_error"
$Phase = "artifact_verified"
$InstallerStartedAt = $null
$InstallerFinishedAt = $null
$InstallerExitCode = $null
$InstallDurationSeconds = $null
$RebootRequired = $false
$InstalledMarkerFound = $false
$InstalledPayloadFound = $false
$FirstLaunchVerified = $false
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
$ExitCode = 1
$InstallerProcess = $null
$InstallTimeoutSeconds = 0
$ExpectedMarkerName = ""
$ExpectedMarkerHash = ""
$ExpectedPayloadName = ""
$ExpectedPayloadHash = ""
$GuestSystem = [ordered]@{
    os_version = [Environment]::OSVersion.VersionString
    architecture = [Environment]::Is64BitOperatingSystem.ToString()
    powershell_version = $PSVersionTable.PSVersion.ToString()
}

try {
    New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
    [System.IO.File]::WriteAllText($InstallerLogPath, "", $Utf8NoBom)
    Write-InstallerLog "controlled_fixture_bootstrap_started"
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw "guest_request_missing"
    }
    $Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-ExactProperties -Value $Request -Name "request" -Expected @(
        "schema_version", "protocol", "run_id", "artifact_name", "artifact_sha256_host",
        "controlled_fixture_profile", "installation_recipe", "expected_install_root",
        "expected_marker_name", "expected_marker_sha256", "expected_payload_name", "expected_payload_sha256"
    )
    if ($Request.schema_version -ne $SchemaVersion -or $Request.protocol -ne $Protocol) {
        throw "guest_request_schema_unsupported"
    }
    $RunId = [string]$Request.run_id
    $ParsedRunId = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId) {
        throw "guest_request_run_id_invalid"
    }
    if ($Request.controlled_fixture_profile -ne $FixtureProfile) {
        throw "controlled_fixture_required"
    }
    if ($Request.artifact_name -ne "artifact.exe") {
        throw "controlled_fixture_artifact_invalid"
    }
    $HostHash = ([string]$Request.artifact_sha256_host).ToLowerInvariant()
    if ($HostHash -notmatch "^[0-9a-f]{64}$") {
        throw "artifact_host_hash_invalid"
    }
    if ($Request.expected_install_root -ne $LogicalInstallRoot) {
        throw "controlled_install_root_invalid"
    }

    $Recipe = $Request.installation_recipe
    Assert-ExactProperties -Value $Recipe -Name "installation_recipe" -Expected @(
        "installer_kind", "artifact_sha256", "install_timeout_seconds", "expected_install_scope",
        "reboot_policy", "network_policy", "success_requirements", "profile_version"
    )
    if ($Recipe.installer_kind -ne "nsis_exe" -or $Recipe.expected_install_scope -ne "user") {
        throw "controlled_install_profile_invalid"
    }
    if ($Recipe.network_policy -ne "disabled" -or $Recipe.reboot_policy -ne "forbid") {
        throw "controlled_install_policy_invalid"
    }
    if ($Recipe.profile_version -ne 1 -or ([string]$Recipe.artifact_sha256).ToLowerInvariant() -ne $HostHash) {
        throw "controlled_install_recipe_invalid"
    }
    $Requirements = @($Recipe.success_requirements)
    if ($Recipe.success_requirements -isnot [System.Array] -or $Requirements.Count -ne 2 -or ($Requirements -join "|") -ne "artifact_hash_verified|installer_exit_zero") {
        throw "controlled_install_success_requirements_invalid"
    }
    $InstallTimeoutSeconds = [int]$Recipe.install_timeout_seconds
    if ($InstallTimeoutSeconds -lt 10 -or $InstallTimeoutSeconds -gt 1800) {
        throw "controlled_install_timeout_invalid"
    }

    $ExpectedMarkerName = [string]$Request.expected_marker_name
    $ExpectedMarkerHash = ([string]$Request.expected_marker_sha256).ToLowerInvariant()
    $ExpectedPayloadName = [string]$Request.expected_payload_name
    $ExpectedPayloadHash = ([string]$Request.expected_payload_sha256).ToLowerInvariant()
    if ($ExpectedMarkerName -ne "fixture-manifest.json" -or $ExpectedPayloadName -ne "payload.txt") {
        throw "controlled_fixture_evidence_names_invalid"
    }
    if ($ExpectedMarkerHash -notmatch "^[0-9a-f]{64}$" -or $ExpectedPayloadHash -notmatch "^[0-9a-f]{64}$") {
        throw "controlled_fixture_evidence_hash_invalid"
    }

    Write-AtomicJson -Path $GuestSystemPath -Value $GuestSystem
    $ArtifactPath = Join-Path $InputRoot "artifact.exe"
    if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        throw "artifact_missing_from_read_only_input"
    }
    $GuestHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashVerified = $GuestHash -eq $HostHash
    if (-not $HashVerified) {
        $Outcome = "failed"
        throw "artifact_hash_mismatch"
    }
    Write-Heartbeat -Phase "artifact_verified" -RunId $RunId

    $MarkerPath = Join-Path $ActualInstallRoot "fixture-manifest.json"
    $PayloadPath = Join-Path $ActualInstallRoot "payload.txt"
    if ((Test-Path -LiteralPath $MarkerPath) -or (Test-Path -LiteralPath $PayloadPath)) {
        throw "fixture_install_root_not_clean"
    }

    $Phase = "installing"
    Write-Heartbeat -Phase $Phase -RunId $RunId
    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $ArtifactPath
    $StartInfo.Arguments = "/S"
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $InstallerProcess = New-Object System.Diagnostics.Process
    $InstallerProcess.StartInfo = $StartInfo
    $InstallerStartedAt = Get-UtcTimestamp
    $Timer = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $InstallerProcess.Start()) {
        throw "controlled_installer_start_failed"
    }
    Write-InstallerLog "installer_started"
    while (-not $InstallerProcess.WaitForExit(200)) {
        if ($Timer.Elapsed.TotalSeconds -ge $InstallTimeoutSeconds) {
            try {
                $InstallerProcess.Kill()
                $InstallerProcess.WaitForExit(5000) | Out-Null
            }
            catch {
                $Warnings.Add("owned_installer_cleanup_failed")
            }
            $InstallerFinishedAt = Get-UtcTimestamp
            $InstallDurationSeconds = [Math]::Round($Timer.Elapsed.TotalSeconds, 3)
            $Outcome = "timed_out"
            $Errors.Add("controlled_installer_timed_out")
            throw "controlled_installer_timed_out"
        }
    }
    $Timer.Stop()
    $InstallerFinishedAt = Get-UtcTimestamp
    $InstallDurationSeconds = [Math]::Round($Timer.Elapsed.TotalSeconds, 3)
    $InstallerExitCode = $InstallerProcess.ExitCode
    Write-InstallerLog "installer_finished exit_code_$InstallerExitCode"
    if ($InstallerExitCode -eq 1641 -or $InstallerExitCode -eq 3010) {
        $RebootRequired = $true
        $Outcome = "reboot_required"
        $Errors.Add("installer_reported_reboot_required")
        throw "installer_reported_reboot_required"
    }
    if ($InstallerExitCode -ne 0) {
        $Outcome = "failed"
        $Errors.Add("installer_exit_code_nonzero")
        throw "installer_exit_code_nonzero"
    }

    $Phase = "verifying"
    Write-Heartbeat -Phase $Phase -RunId $RunId
    if (Test-Path -LiteralPath $MarkerPath -PathType Leaf) {
        $InstalledMarkerFound = ((Get-FileHash -LiteralPath $MarkerPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $ExpectedMarkerHash)
    }
    if (Test-Path -LiteralPath $PayloadPath -PathType Leaf) {
        $InstalledPayloadFound = ((Get-FileHash -LiteralPath $PayloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $ExpectedPayloadHash)
    }
    if (-not $InstalledMarkerFound -or -not $InstalledPayloadFound) {
        $Outcome = "failed"
        $Errors.Add("fixture_installation_evidence_missing")
        throw "fixture_installation_evidence_missing"
    }
    $UnexpectedExecutables = @(Get-ChildItem -LiteralPath $ActualInstallRoot -File -Recurse | Where-Object { $_.Extension -ieq ".exe" })
    if ($UnexpectedExecutables.Count -ne 0) {
        $Outcome = "failed"
        $Errors.Add("unexpected_installed_executable")
        throw "unexpected_installed_executable"
    }

    $Status = "passed"
    $Outcome = "passed"
    $ExitCode = 0
    Write-InstallerLog "controlled_fixture_installation_passed"
}
catch {
    $Status = "failed"
    if ($Outcome -eq "infrastructure_error" -and $HashVerified) {
        $Outcome = "failed"
    }
    if ($Errors.Count -eq 0) {
        $Errors.Add("controlled_fixture_installation_failed")
    }
    Write-InstallerLog "controlled_fixture_installation_failed"
}
finally {
    try {
        $Phase = "completed"
        $CompletedAt = Get-UtcTimestamp
        Write-Heartbeat -Phase $Phase -RunId $RunId
        if (-not (Test-Path -LiteralPath $GuestSystemPath -PathType Leaf)) {
            Write-AtomicJson -Path $GuestSystemPath -Value $GuestSystem
        }
        $FinalEvidence = [ordered]@{
            schema_version = $SchemaVersion
            protocol = $Protocol
            run_id = $RunId
            status = $Status
            phase = $Phase
            outcome = $Outcome
            started_at = $StartedAt
            updated_at = $CompletedAt
            completed_at = $CompletedAt
            artifact = $ArtifactName
            installer_kind = "nsis_exe"
            installer_started_at = $InstallerStartedAt
            installer_finished_at = $InstallerFinishedAt
            installer_exit_code = $InstallerExitCode
            install_duration_seconds = $InstallDurationSeconds
            reboot_required = $RebootRequired
            artifact_sha256_host = $HostHash
            artifact_sha256_guest = $GuestHash
            hash_verified = $HashVerified
            expected_install_root = $LogicalInstallRoot
            installed_marker_found = $InstalledMarkerFound
            installed_payload_found = $InstalledPayloadFound
            installed_executable_found = $null
            first_launch_verified = $FirstLaunchVerified
            errors = @($Errors)
            warnings = @($Warnings)
        }
        Write-AtomicJson -Path $StatusPath -Value $FinalEvidence
        Write-AtomicJson -Path $InstallationEvidencePath -Value $FinalEvidence
        Write-AtomicJson -Path $CompletionPath -Value ([ordered]@{
            schema_version = $SchemaVersion
            protocol = $Protocol
            run_id = $RunId
            completed = $true
            status = $Status
            outcome = $Outcome
            phase = $Phase
            completed_at = $CompletedAt
        })
    }
    catch {
        Write-InstallerLog "evidence_finalization_failed"
        $ExitCode = 2
    }
}

exit $ExitCode
