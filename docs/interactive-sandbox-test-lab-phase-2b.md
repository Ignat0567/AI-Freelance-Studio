# Interactive Sandbox Test Lab Phase 2B

## Purpose

Phase 2B performs one real, controlled, user-scope NSIS installation inside Windows Sandbox. It accepts only the repository-owned `aifs_sandbox_fixture_v1` profile. It does not execute the AI Freelance Studio installer and does not launch any installed application.

Phase 1 schema version 1 remains unchanged. Phase 2A `plan-install` remains a schema-version-2 dry-run. Phase 2B uses schema version 2 with the explicit `controlled_fixture_installation_v1` protocol discriminator; the two contracts cannot be confused because their required fields and validators are separate.

## Controlled Fixture

Source files are under `sandbox_test_lab/fixtures/nsis/`:

- `aifs_sandbox_fixture.nsi`
- `fixture-manifest.json`
- `payload.txt`

The fixture uses `RequestExecutionLevel user` and installs only these inert files:

```text
%LOCALAPPDATA%\Programs\AIFS Sandbox Fixture\fixture-manifest.json
%LOCALAPPDATA%\Programs\AIFS Sandbox Fixture\payload.txt
```

It does not contain an executable payload, process launch, service, scheduled task, autorun, shortcut, registry change, file association, firewall rule, driver, network operation, uninstaller, or reboot instruction.

The fixture binary is built into ignored `sandbox_test_lab/fixtures/nsis/build/` and is never committed. The builder resolves only electron-builder's pinned local NSIS cache and never searches `PATH` or downloads dependencies:

```powershell
python -m sandbox_test_lab.fixture_builder
```

Expected compiler location:

```text
%LOCALAPPDATA%\electron-builder\Cache\nsis\nsis-3.0.4.1\Bin\makensis.exe
```

The command validates NSIS `v3.04`, invokes it with an argv list and `shell=False`, and prints the built fixture SHA-256.

The pinned compiler and artifact hashes apply only to reproducible development/test fixture builds. The AI Freelance Studio production runtime and packaging pipeline do not invoke this builder and do not require `makensis.exe`. The installation runner verifies an already built controlled fixture and never acts as a compiler or production installer executor.

## Execution CLI

Execution has two independent opt-ins: an environment variable and the explicit `--external` option.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_NSIS_INSTALL_EXTERNAL = "1"
python -m sandbox_test_lab run-install `
  --artifact "sandbox_test_lab\fixtures\nsis\build\aifs-sandbox-fixture-v1.exe" `
  --sha256 HASH `
  --installer-kind nsis_exe `
  --expected-fixture-profile aifs_sandbox_fixture_v1 `
  --timeout 240 `
  --external `
  --json
```

The artifact must resolve to the exact ignored fixture build output. A different path or profile is rejected with `controlled_fixture_required`. The command has no raw command or raw arguments option.

The real pytest test has its own dual gate: the same environment variable and exact marker selection.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_NSIS_INSTALL_EXTERNAL = "1"
python -m pytest test_sandbox_test_lab_phase2b_external.py -m external -q -s
```

Plain pytest, normal offline suites, and broader marker expressions do not build the fixture or launch Windows Sandbox.

## Guest Flow

The package-owned `guest/install_fixture.ps1` is copied into the read-only guest mapping as the fixed `bootstrap.ps1`. The WSB logon command remains static.

The guest performs these steps:

1. Strictly validates schema, protocol, canonical run ID, fixture profile, recipe fields, user scope, disabled network, forbidden reboot, timeout, and fixed marker/payload identities.
2. Reads only `C:\SandboxTestLab\Input\artifact.exe` and verifies its SHA-256 against the host request.
3. Derives the install path internally from Sandbox user `LOCALAPPDATA`; the request cannot supply a host or arbitrary guest path.
4. Creates `ProcessStartInfo` with the mapped artifact, the exact argument string `/S`, and `UseShellExecute=false`.
5. Waits only for the owned installer process using a bounded timeout.
6. On timeout, attempts to kill only that process and never uses process-name-wide `taskkill`.
7. Treats NSIS exit codes `1641` and `3010` as `reboot_required` and never reboots the Sandbox.
8. Requires exit code zero plus matching marker and payload hashes. Exit code alone is not success evidence.
9. Rejects any installed `.exe` in the fixture root.
10. Never launches the installed payload and reports `first_launch_verified=false` and `installed_executable_found=null`.

The controlled fixture has no reboot behavior. Phase 2B uses explicit installer exit codes as the supported reboot signal; it does not make ambiguous claims from generic pending-reboot registry state.

## Evidence

The writable evidence mapping must contain exactly:

```text
status.json
heartbeat.json
completion.json
guest-system.json
installer.log
installation-evidence.json
```

Final installation evidence includes:

```text
phase
outcome
installer_kind
installer_started_at
installer_finished_at
installer_exit_code
install_duration_seconds
reboot_required
artifact_sha256_host
artifact_sha256_guest
hash_verified
expected_install_root
installed_marker_found
installed_payload_found
installed_executable_found
first_launch_verified
errors
warnings
```

The host rejects missing or unexpected files, symlinks, reparse points, non-regular files, executable/script additions, duplicate JSON keys, oversized JSON/log/aggregate evidence, invalid UTF-8, unknown fields, identity/schema mismatches, invalid controlled path fields, error messages containing Windows separators or URL-style path prefixes, inconsistent timestamps/status/outcome, hash mismatch, missing marker/payload, unexpected executable evidence, launch claims, and reboot inconsistencies.

The evidence mapping is treated as untrusted. Validated evidence is atomically copied as JSON to the host-only `logs/validated-installation-evidence.json`; `host-result.json` is also written atomically outside the writable guest mapping. Installed files are never copied back to the host.

## Security Boundary

- Networking and all existing WSB redirection controls remain disabled.
- Input and guest mappings are read-only; evidence is the only writable mapping.
- The project root, generated projects, Studio config, auth storage, keys, tokens, browser data, and host logs are not mounted.
- Guest requests omit metadata, host paths, raw commands, raw arguments, environment dumps, and arbitrary install roots.
- The NSIS executable and `/S` token are derived only by controlled code.
- Machine scope, reboot, arbitrary NSIS installers, MSI, portable execution, uninstall, and application launch are forbidden.
- Host cancellation and timeout stop only the owned Windows Sandbox broker process. Because that lifecycle is brokered, this does not prove guest termination; the guest-owned installer timeout is the primary bound and manual Sandbox closure may still be required.
- Path checks are not a handle-based defense against a malicious process running as the same host user. Phase 2B remains fixture-only for this reason.

## Not Implemented

- AI Freelance Studio installer execution
- Arbitrary installer execution
- Installed application launch or first-launch verification
- Screenshots or video
- UI automation
- Uninstall
- QA Engine, repair loop, delivery, API, or Studio UI integration
- Docker
- Persistent Hyper-V provider
- Network-enabled tests
