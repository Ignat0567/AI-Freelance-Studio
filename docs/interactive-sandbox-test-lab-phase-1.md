# Interactive Sandbox Test Lab Phase 1

## Purpose

Phase 1 is an independent Windows Sandbox host harness. It detects capability, stages one selected artifact, creates a hardened `.wsb` configuration, runs a fixed offline guest bootstrap, and records structured evidence. It is not connected to the Studio API, UI, QA Engine, repair loop, delivery audit, OpenCode, or installer.

The guest does not install or launch the selected application. It verifies that the staged artifact is readable and has the expected SHA-256.

## Host requirements

- Windows 10 build 18362 or later, or a supported Windows 11 build.
- A supported Pro, Enterprise, Education, or equivalent edition. Windows Home/Core is not supported.
- Hardware virtualization and a usable hypervisor.
- The optional feature `Containers-DisposableClientVM` already enabled.
- Windows PowerShell and `WindowsSandbox.exe` available.

Capability detection is read-only. It does not enable Windows features, install software, request administrator privileges, or modify the host.
Host `WindowsSandbox.exe` and Windows PowerShell are resolved only from their canonical paths below the Windows system root. The host does not trust `PATH` for either executable.

## CLI

```powershell
python -m sandbox_test_lab capability
python -m sandbox_test_lab capability --json
python -m sandbox_test_lab prepare --artifact PATH --sha256 HASH
python -m sandbox_test_lab run --artifact PATH --sha256 HASH --timeout 300
python -m sandbox_test_lab inspect --run-id UUID --json
```

`prepare` creates the workspace and `.wsb` file but does not launch Windows Sandbox. Phase 1 has no network-enabling CLI option.

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | Capability available, preparation succeeded, or run passed |
| 2 | Invalid command-line syntax reported by `argparse` |
| 3 | Windows Sandbox unavailable |
| 4 | Invalid artifact, workspace, configuration, or inspect input |
| 5 | Launch or infrastructure failure |
| 6 | Timeout |
| 7 | Cancelled |
| 8 | Guest bootstrap failed |
| 9 | Missing or invalid evidence |
| 70 | Unexpected CLI boundary failure |

## Run workspace

The default root is `%LOCALAPPDATA%\AI Freelance Studio\sandbox-test-lab` on Windows. Tests and automation can inject another root.

```text
runs/<uuid>/
  input/                 copied artifact; read-only guest mount
  guest/                 bootstrap.ps1 and request.json; read-only guest mount
  evidence/              guest status and diagnostics; writable guest mount
  logs/                  host-only log
  sandbox.wsb
  host-result.json
```

The source project is never mounted. The source artifact must be a regular file and cannot be a symlink or reparse point. The run directory is created exclusively and is never overwritten. SHA-256 is calculated from the copied file. Existing run evidence is retained; Phase 1 has no automatic cleanup.

Immediately before launching the broker, the host revalidates the expected run layout, containment, required files, and absence of symlink/reparse-point indirection for every mapped path. Request metadata remains host-only and is not written to the guest request, evidence, or logs.

Python-level source checks protect against static or accidental reparse points. They are not a handle-based defense against a concurrent malicious process running as the same host user.

## Sandbox security defaults

- Networking: disabled.
- Clipboard redirection: disabled.
- Printer redirection: disabled.
- Audio input: disabled.
- Video input: disabled.
- vGPU: disabled.
- Protected client: enabled.
- Memory: 4096 MiB by default, configurable only through the library contract.
- Input and guest mappings: read-only.
- Evidence mapping: writable and unique to one run.
- Logon command: fixed PowerShell invocation of the controlled read-only bootstrap.

No Studio configuration, auth storage, environment dump, API keys, browser profile, generated project, or user-data directory is mapped into the guest.

## Evidence protocol

Schema version 1 uses these guest files:

- `heartbeat.json`: current phase and last update timestamp.
- `status.json`: final validated guest result.
- `guest-system.json`: limited OS, architecture, and PowerShell version information.
- `completion.json`: written last and used as the completion barrier.
- `bootstrap.log`: bounded-purpose diagnostics without environment or credential collection.

The status contract contains:

```text
schema_version
run_id
status
phase
started_at
updated_at
completed_at
artifact
artifact_sha256_host
artifact_sha256_guest
hash_verified
guest_system
errors
warnings
```

The host limits JSON size, rejects duplicate keys, decodes UTF-8, validates schema and run identity, restricts the artifact field to a filename, validates timestamps/status/hash fields, and does not trust a guest `passed` value without matching host and guest SHA-256. Valid `failed` evidence may omit a guest hash when hashing could not complete; malformed hashes still fail closed.

The final `host-result.json` records one terminal result using the validated state vocabulary: `passed`, `failed`, `timed_out`, `unavailable`, `cancelled`, or `infrastructure_error`. Internal transitions through `created`, `launching`, and `running` are validated but are not persisted as final results.

## Timeout and cancellation

The host uses one monotonic deadline starting before capability detection and polls heartbeat/completion files. Launch alone never counts as success. Cancellation is checked before workspace creation, immediately before launch, and while polling. On timeout or cancellation it terminates only the process handle created by that runner and never calls `taskkill` by process name.

Windows Sandbox uses a brokered process lifecycle on supported systems. Exit of the launcher process is recorded with its return code and timing but does not end evidence polling or decide success/failure. Phase 1 does not globally close a successfully completed Sandbox session because doing so could terminate a user-owned instance. The Sandbox window can remain open after successful evidence collection and must then be closed manually by the user.

## External smoke test

The real test is marked `external` and also requires an explicit environment opt-in. The test verifies both the environment value and the exact `-m external` selection; plain `pytest` skips it even if the environment variable was left set:

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_EXTERNAL = "1"
python -m pytest test_sandbox_test_lab_external.py -m external -q
```

It skips when the opt-in is absent or `capability.available` is false. When explicitly enabled, it launches Windows Sandbox with a non-sensitive generated artifact and verifies the complete evidence protocol.

## Known limitations

Phase 1 does not implement application installation, application launch, Playwright, screenshots or video, network access, cryptographic guest attestation, automated evidence deletion, QA integration, repair loop integration, API endpoints, or Studio UI.

Guest evidence is consistency-checked but is not a tamper-proof attestation against malicious guest code. The writable evidence mapping is intentionally treated as untrusted input.
