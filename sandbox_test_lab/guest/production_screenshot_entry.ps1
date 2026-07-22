$ErrorActionPreference = "Stop"
$GuestRoot = "C:\SandboxTestLab\Guest"
$EvidenceRoot = "C:\SandboxTestLab\Evidence"
$RequestPath = Join-Path $GuestRoot "request.json"
$PayloadPath = Join-Path $GuestRoot "production_screenshot_self_test.ps1"
$FailurePath = Join-Path $EvidenceRoot "entry-failure.json"
$ResultPath = Join-Path $EvidenceRoot "entry-payload-result.json"
$CanonicalPowerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$CanonicalShutdown = "C:\Windows\System32\shutdown.exe"
$ExpectedPayloadHash = "fcce845efe5e54521a57eee79ac845c88db8339797f9a5ecd6d7381f856b110e"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$RunId = ""
$EntryExitCode = 1

function Write-AtomicJson {
    param([string] $Path, [object] $Value)
    $Temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 4 -Compress), $Utf8NoBom)
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}

function Write-EntryMarker {
    param([string] $Stage)
    $Path = Join-Path $EvidenceRoot "entry-$Stage.json"
    if (Test-Path -LiteralPath $Path) { throw "entry_stage_already_exists" }
    Write-AtomicJson $Path ([ordered]@{
        schema_version = 1
        run_id = $RunId
        stage = $Stage
        timestamp = [DateTime]::UtcNow.ToString("o")
    })
}

try {
    New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
    Write-EntryMarker "entry_script_started"
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) { throw "request_missing" }
    Write-EntryMarker "request_found"
    $Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $RunId = [string]$Request.run_id
    $ParsedRunId = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$ParsedRunId) -or $ParsedRunId.ToString() -cne $RunId) { throw "request_run_id_invalid" }
    if ([string]$Request.execution_mode -notin @("production_screenshot", "payload_load_probe")) { throw "execution_mode_invalid" }
    if (-not (Test-Path -LiteralPath $PayloadPath -PathType Leaf)) { throw "payload_missing" }
    Write-EntryMarker "payload_found"
    $PayloadItem = Get-Item -LiteralPath $PayloadPath -Force
    if ($PayloadItem.PSIsContainer -or ($PayloadItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "payload_invalid" }
    $PayloadHash = (Get-FileHash -LiteralPath $PayloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($PayloadHash -cne $ExpectedPayloadHash -or [string]$Request.payload_sha256 -cne $ExpectedPayloadHash) { throw "payload_hash_mismatch" }
    Write-EntryMarker "payload_hash_verified"
    $StartInfo = New-Object Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $CanonicalPowerShell
    $StartInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\SandboxTestLab\Guest\production_screenshot_self_test.ps1"'
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.EnvironmentVariables["AIFS_PHASE3B_RUN_ID"] = $RunId
    $PayloadProcess = New-Object Diagnostics.Process
    $PayloadProcess.StartInfo = $StartInfo
    if (-not $PayloadProcess.Start()) { throw "payload_start_failed" }
    Write-EntryMarker "payload_process_started"
    $PayloadProcess.WaitForExit()
    $PayloadExitCode = $PayloadProcess.ExitCode
    Write-AtomicJson $ResultPath ([ordered]@{
        schema_version = 1
        run_id = $RunId
        stage = "payload_process_exited"
        timestamp = [DateTime]::UtcNow.ToString("o")
        exit_code = $PayloadExitCode
        status = $(if ($PayloadExitCode -eq 0) { "passed" } else { "failed" })
    })
    $EntryExitCode = $PayloadExitCode
}
catch {
    $Reason = ([string]$_.Exception.Message -replace "[^A-Za-z0-9_]", "_")
    if ($Reason.Length -gt 96) { $Reason = $Reason.Substring(0, 96) }
    try {
        Write-AtomicJson $FailurePath ([ordered]@{
            schema_version = 1
            run_id = $RunId
            stage = "entry_script_failed"
            timestamp = [DateTime]::UtcNow.ToString("o")
            reason = $Reason
        })
    } catch { }
    $EntryExitCode = 1
}

# Terminal evidence is complete before the guest requests normal Sandbox shutdown.
try {
    $ShutdownInfo = New-Object Diagnostics.ProcessStartInfo
    $ShutdownInfo.FileName = $CanonicalShutdown
    $ShutdownInfo.Arguments = "/s /t 0"
    $ShutdownInfo.UseShellExecute = $false
    $ShutdownInfo.CreateNoWindow = $true
    $ShutdownProcess = [Diagnostics.Process]::Start($ShutdownInfo)
} catch { }
exit $EntryExitCode
