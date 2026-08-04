$ErrorActionPreference = "Stop"
$InputRoot = "C:\SandboxTestLab\Input"
$GuestRoot = "C:\SandboxTestLab\Guest"
$EvidenceRoot = "C:\SandboxTestLab\Evidence"
$RequestPath = Join-Path $GuestRoot "request.json"
$StatusPath = Join-Path $EvidenceRoot "status.json"
$HeartbeatPath = Join-Path $EvidenceRoot "heartbeat.json"
$GuestSystemPath = Join-Path $EvidenceRoot "guest-system.json"
$CompletionPath = Join-Path $EvidenceRoot "completion.json"
$DiagnosticLogPath = Join-Path $EvidenceRoot "bootstrap.log"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

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

function Write-Diagnostic {
    param([string] $Message)
    $SafeMessage = $Message -replace "[\r\n]+", " "
    [System.IO.File]::AppendAllText(
        $DiagnosticLogPath,
        "$(Get-UtcTimestamp) $SafeMessage$([Environment]::NewLine)",
        $Utf8NoBom
    )
}

$StartedAt = Get-UtcTimestamp
$RunId = "unknown"
$ArtifactName = ""
$HostHash = ""
$GuestHash = ""
$HashVerified = $false
$Status = "failed"
$Phase = "initializing"
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
$ExitCode = 1
$GuestSystem = [ordered]@{
    os_version = [Environment]::OSVersion.VersionString
    architecture = [Environment]::Is64BitOperatingSystem.ToString()
    powershell_version = $PSVersionTable.PSVersion.ToString()
}

try {
    New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
    Write-Diagnostic "Bootstrap started"
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw "Guest request is missing"
    }
    $Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Request.schema_version -ne 1) {
        throw "Guest request schema is unsupported"
    }
    $RunId = [string]$Request.run_id
    $ArtifactName = [string]$Request.artifact_name
    $HostHash = ([string]$Request.artifact_sha256_host).ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($RunId) -or [string]::IsNullOrWhiteSpace($ArtifactName)) {
        throw "Guest request identity is invalid"
    }
    if ([System.IO.Path]::GetFileName($ArtifactName) -ne $ArtifactName) {
        throw "Artifact name is invalid"
    }

    $Phase = "hashing_artifact"
    Write-AtomicJson -Path $HeartbeatPath -Value ([ordered]@{
        schema_version = 1
        run_id = $RunId
        phase = $Phase
        updated_at = Get-UtcTimestamp
    })
    Write-AtomicJson -Path $GuestSystemPath -Value $GuestSystem

    $ArtifactPath = Join-Path $InputRoot $ArtifactName
    if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        throw "Artifact is missing from the read-only input mapping"
    }
    $GuestHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashVerified = $GuestHash -eq $HostHash
    if ($Request.expected_sha256) {
        $HashVerified = $HashVerified -and ($GuestHash -eq ([string]$Request.expected_sha256).ToLowerInvariant())
    }
    if (-not $HashVerified) {
        throw "Artifact SHA-256 verification failed"
    }

    $Status = "passed"
    $Phase = "completed"
    $ExitCode = 0
    Write-Diagnostic "Artifact verification passed"
}
catch {
    $Status = "failed"
    $Phase = "failed"
    $Errors.Add([string]$_.Exception.Message)
    Write-Diagnostic "Bootstrap failed"
}
finally {
    try {
        $CompletedAt = Get-UtcTimestamp
        Write-AtomicJson -Path $HeartbeatPath -Value ([ordered]@{
            schema_version = 1
            run_id = $RunId
            phase = $Phase
            updated_at = $CompletedAt
        })
        Write-AtomicJson -Path $StatusPath -Value ([ordered]@{
            schema_version = 1
            run_id = $RunId
            status = $Status
            phase = $Phase
            started_at = $StartedAt
            updated_at = $CompletedAt
            completed_at = $CompletedAt
            artifact = $ArtifactName
            artifact_sha256_host = $HostHash
            artifact_sha256_guest = $GuestHash
            hash_verified = $HashVerified
            guest_system = $GuestSystem
            errors = @($Errors)
            warnings = @($Warnings)
        })
        Write-AtomicJson -Path $CompletionPath -Value ([ordered]@{
            schema_version = 1
            run_id = $RunId
            completed = $true
            status = $Status
            completed_at = $CompletedAt
        })
    }
    catch {
        Write-Diagnostic "Failed to finalize evidence protocol"
        $ExitCode = 2
    }
}

exit $ExitCode
