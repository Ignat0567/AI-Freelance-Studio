# Interactive Sandbox Test Lab Phase 2C

## Purpose

Phase 2C installs the repository-owned NSIS fixture in Windows Sandbox, directly starts its pinned GUI executable, verifies one visible top-level window owned by that process, observes a stable interval, and cleans up only that owned process. It does not accept or test arbitrary applications.

Phase 1 remains schema version 1. Phase 2A remains a schema-version-2 dry-run. Phase 2B remains the separate `controlled_fixture_installation_v1` installation-only protocol and verifies the installed GUI hash without launching it. Phase 2C extends schema version 2 under the distinct protocol `controlled_fixture_install_launch_v1`.

## Reproducible Fixture

Tracked source is under `sandbox_test_lab/fixtures/nsis/`:

- `aifs_sandbox_fixture_gui.nsi`
- `aifs_sandbox_fixture.nsi`
- `fixture-manifest.json`
- `payload.txt`

The pinned local electron-builder `makensis.exe` builds the GUI first. The builder verifies its hash, normalizes its modification time to remove embedded filesystem timestamp variance, and then builds the installer containing that exact GUI. Both generated executables and provenance remain under ignored `sandbox_test_lab/fixtures/nsis/build/`.

```powershell
python -m sandbox_test_lab.fixture_builder
```

Pinned values:

```text
makensis SHA-256: e277b7378931b74392015f5ad6b1d744dcd8a347baa4480350a75ebeab8d8e3d
GUI SHA-256:       ce0b92f797ad93657705d3696e03897be9ee556e820f2ee9e53f2bd9a5c02c15
installer SHA-256: b550df0f7fab343d5c60c00885a1b20716634475843db3e7d8497975e3eabeb0
```

The former Phase 2B installer hash `179773764ae8843d27dbde78b9162a94fc9c924f30bb3e9d2f3a02a0722aa79f` is rejected. Provenance schema version 2 pins compiler version/hash, every tracked source hash, GUI name/hash, and installer name/hash.

The installer uses `RequestExecutionLevel user` and writes only these files below the controlled Sandbox user's `LOCALAPPDATA` root:

```text
Programs\AIFS Sandbox Fixture\fixture-manifest.json
Programs\AIFS Sandbox Fixture\payload.txt
Programs\AIFS Sandbox Fixture\AIFS Sandbox Fixture.exe
```

## Launch Profile

The only profile name is `controlled_fixture_gui_v1`. Its payload contains exactly:

```text
logical_executable_name
expected_installed_exe_sha256
process_image_name
exact_window_title
launch_timeout_seconds
minimum_stable_duration_seconds
cleanup_timeout_seconds
```

The executable and process image are `AIFS Sandbox Fixture.exe`, the exact title is `AIFS Sandbox Fixture`, launch timeout is 30 seconds, minimum stable duration is 3 seconds, and cleanup timeout is 10 seconds. The CLI supplies no command, arguments, path, process name, title, environment override, or working directory.

## External Gate

Phase 2C has a distinct environment gate. The Phase 2B environment variable does not authorize it.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_GUI_EXTERNAL = "1"
python -m sandbox_test_lab run-install-launch `
  --artifact "sandbox_test_lab\fixtures\nsis\build\aifs-sandbox-fixture-v1.exe" `
  --sha256 b550df0f7fab343d5c60c00885a1b20716634475843db3e7d8497975e3eabeb0 `
  --launch-profile controlled_fixture_gui_v1 `
  --timeout 300 `
  --external `
  --json
```

The real test additionally requires exact pytest marker selection:

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_GUI_EXTERNAL = "1"
python -m pytest test_sandbox_test_lab_phase2c_external.py -m external -q -s
```

Plain pytest, offline tests, and broader marker expressions neither build the fixture nor launch Windows Sandbox.
Immediately before launch, a fixed read-only CIM query rejects an already active Windows Sandbox session. The check records no PID or process inventory and never closes or terminates the existing session.

## Guest Lifecycle

The package-owned `guest/install_launch_fixture.ps1` validates the exact schema, protocol, canonical run ID, installation policy, disabled network, forbidden reboot, fixed profile, fixed names, fixed hashes, and fixed timeouts. It verifies the mapped installer hash, performs a silent user-scope installation, and requires the marker, payload, and GUI to be regular non-reparse files at their internally derived contained paths with matching hashes.

The GUI is started with `System.Diagnostics.Process`, `UseShellExecute=false`, no arguments property, and no environment or caller working-directory override. The guest retains that process object and PID. Static Win32 declarations enumerate top-level visible windows transiently and retain evidence only for the exact-title window owned by that PID. The expected full process image path is checked internally but is never emitted.

The successful transition sequence is exact:

```text
installed -> launching -> process_started -> window_detecting -> window_visible -> first_launch_verified -> cleanup -> completed
```

The PID-owned window must remain visible with the exact title and expected process image for at least three seconds. Cleanup calls `CloseMainWindow`, then PID/handle-revalidated `WM_CLOSE` when needed, waits the fixed timeout, and finally calls `Kill` only on the retained process object. A fallback-kill warning does not negate an already verified first launch when the owned process exits successfully.

## Evidence

The writable mapping permits exactly:

```text
status.json
heartbeat.json
completion.json
guest-system.json
lifecycle.log
launch-evidence.json
```

The host validator rejects duplicate JSON keys; missing, extra, oversized, executable, script, non-regular, symlink, or reparse evidence; unknown fields; path-bearing values; path/command/argument/environment/secret-like fields; invalid identity; unsupported phases; timestamp disorder; failed installation or hash claims; zero or mismatched PID/window association; wrong image/title; a short stable interval; inconsistent first-launch claims; and inconsistent cleanup/fallback claims. It does not collect or persist global process or window inventory.

Validated guest evidence is copied atomically to host-only `logs/validated-launch-evidence.json`. Installed files are never mapped back to the host.

## Limitations

- This is a controlled fixture test, not AI Freelance Studio application testing.
- Production installer testing is not implemented.
- No arbitrary installer, executable, command, arguments, title, process, path, environment, or launch profile is supported.
- Networking, reboot, machine-scope installation, uninstall, screenshots, video, UI automation, accessibility assertions, and application behavior assertions are not supported.
- QA Engine, API, UI, and `main.py` integration are not implemented.
- Docker and a persistent Hyper-V provider are not implemented.
- Window verification proves only exact title, visibility, PID ownership, image identity, and stable duration. It does not prove useful rendering or responsiveness.
- Windows Sandbox broker termination on host timeout or cancellation does not prove guest process termination. The guest's owned-process cleanup is the primary lifecycle control; manual Sandbox closure may still be required after infrastructure failure.
- The path checks are not a handle-based defense against a malicious process running as the same Sandbox user. Phase 2C remains restricted to the pinned repository fixture.
- Repair, delivery, and production packaging are not integrated or modified.
