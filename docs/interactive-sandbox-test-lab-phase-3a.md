# Interactive Sandbox Test Lab Phase 3A

## Purpose

Phase 3A is a single immutable production self-test for AI Freelance Studio `1.0.0-beta.1`. It installs the exact ignored release installer in Windows Sandbox, verifies the first Electron window and owned process tree, requires the packaged backend health check, records bounded evidence, and cleans up only creation-verified owned processes. It does not add a CLI, UI, QA, API, `main.py`, installer, package metadata, or production-code integration.

Phase 1 and Phase 2 contracts remain unchanged. Phase 3A uses schema version `3`, protocol `aifs_production_self_test_v1`, and the only trusted profile name `aifs_production_beta_1_self_test`.

## Audit Findings

The packaged Electron application starts a backend sidecar from `resources\backend\freelancerstudio-backend\freelancerstudio-backend.exe`. The application may select a source port from `8080-8099` during normal operation. A clean self-test has no competing instance and therefore pins the expected first port, `8080`. The test polls only `http://127.0.0.1:8080/health` and fails closed rather than discovering or trusting another port, including any port reported by a response payload.

Electron can transfer the visible window and backend work to descendants. The retained launch root is therefore not treated as the sole window owner. Descendants are accepted only when discovered recursively by a parent-filtered `Win32_Process` query and matched to a retained `System.Diagnostics.Process` object and creation time. No unrelated Electron process or global process inventory is accepted or emitted.

The static packaging audit found that Electron renderer/utility children normally execute from the install root. The backend is a PyInstaller onedir executable built with `console=True`; Electron starts it with `windowsHide`, but does not use `CREATE_NO_WINDOW`, so Windows may create a canonical-system `conhost.exe` descendant. The package also bundles Playwright's Node runtime. Phase 3A therefore has one exact auxiliary allowlist entry, `conhost.exe`, guarded by canonical System32 origin and the complete ownership, creation, file-safety, signature, role, and non-eligibility policy below. It does not trust arbitrary System32 images or basenames.

## Artifact Trust

The only source is:

```text
frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe
size:    215224643 bytes
SHA-256: B72AD863F045F7877E9BEB32826C2090D96BAFE09F73B68C1892072CD4F1EF1F
```

Its exact host-only sidecar is `AI Freelance Studio-Setup-1.0.0-beta.1-win.exe.sha256`, with this exact ASCII content and final LF:

```text
B72AD863F045F7877E9BEB32826C2090D96BAFE09F73B68C1892072CD4F1EF1F *AI Freelance Studio-Setup-1.0.0-beta.1-win.exe
```

Both files remain ignored under `frontend/installers/`; they are not repository additions. The host checks canonical repository containment, exact name, regular-file type, every path component for symlink/reparse indirection, size, SHA-256, and exact sidecar content before staging. It verifies the staged copy immediately after copying and rechecks source trust, staged trust, the guest script, exact request, empty evidence directory, WSB layout, and active-session guard immediately before the controlled Sandbox launcher. Only `artifact.exe` is mapped. The sidecar is never staged.

The request has an exact key allowlist and contains only the UUID run ID, schema/protocol, trusted product/version/profile, `artifact.exe`, trusted size/hash, the runner-generated `guest_terminal_deadline_utc`, fixed logical paths, fixed endpoint, and fixed policies. There is no caller-selected deadline, installer, hash, executable, title, port, endpoint, command, arguments, working directory, or environment override.

## Install And Launch

The fixed profile requires NSIS, one argument `/S`, user scope, disabled networking, and forbidden reboot. The installer configured maximum remains 600 seconds, but it is never used as an independent elapsed budget. Immediately before `Process.Start()`, the guest captures `installer_started_at`; the effective UTC deadline is the lesser of that configured maximum and the one remaining execution budget after reserving launch (60), stability (5), backend (30), cleanup (20), and terminal evidence (15) seconds. It captures optional `process_start_returned_at` immediately after a successful return and captures `installer_finished_at` only after the retained installer process is confirmed exited. If `Process.Start()` returns after the effective phase deadline, the retained installer is cleaned and the run fails safely. The normative duration is solely the rounded millisecond difference between the finish and start UTC values, so it consistently includes time spent inside `Process.Start()`; no stopwatch supplies installer duration or timeout authority. Exit codes `1641` and `3010` are rejected, and exit zero is insufficient without the expected installed executable.

Host preflight requires a fixed minimum of 2 GiB free on both the canonical installer parent and runtime parent. The guest independently requires 2 GiB free on the `LOCALAPPDATA` drive before installation. This threshold is not caller-configurable. WSB memory remains bounded at 4096 MiB. Run workspaces are created exclusively, so an existing run ID is rejected rather than partially reused.

The logical install root is `sandbox_user_local_app_data\Programs\AI Freelance Studio`. The guest internally derives the exact Sandbox-user root below `LOCALAPPDATA`, rejects a non-clean root, validates the complete path chain as non-reparse, and requires regular contained `AI Freelance Studio.exe`. Its SHA-256 is diagnostic evidence. The installed executable alone is launched directly with `System.Diagnostics.Process`, `UseShellExecute=false`, no shell, no arguments, and no environment overrides.

Launch timeout is at most 60 seconds and leaves the full stability, backend, cleanup, and evidence reserves. Stability starts only when the complete configured five-second interval remains in addition to all later reserves. Every recursively discovered child is retained only through its parent relation and creation time, but only install-root Electron processes may prove the GUI and only the exact install-root backend candidate may prove backend readiness. External images fail before window/backend proof unless the image is exactly case-insensitive `conhost.exe` at the canonical System32 directory obtained from `Environment.SystemDirectory`, is a regular file with a reparse-free chain, has valid Authenticode signed by Microsoft Corporation, belongs to the current recursive parent tree, was created at or after this launch, is classified `system_helper`, and has GUI, backend, and cleanup eligibility all false. SysWOW64, nested or outside copies, arbitrary System32 images, unsigned or wrong-signer files, reparse paths, preexisting processes, and unverified parent relations fail closed. A visible top-level window with exact title `AI Freelance Studio`, nonzero HWND, and an eligible owner PID in the creation-verified owned tree must remain visible, exactly titled, owned, and image-contained for at least five seconds. Blank, wrong-title, unrelated, outside-root, unstable, and early-exit windows fail. A descendant-owned install-root Electron window is valid; the authorized conhost or any other system-helper window is not.

## Backend

The backend is required for `fully_ready`. The guest verifies that a tracked descendant has the exact internally derived backend image and contained path, then polls only `http://127.0.0.1:8080/health` for at most the lesser of 30 seconds and the remaining budget after cleanup and evidence reserves, with proxying and redirects disabled. Success is exactly HTTP `200` plus JSON `status=ok` and `service=FreelancerStudio`. Headers, cookies, tokens, response paths, and response port data are not stored. Outcomes distinguish `installation_failed`, `gui_launch_failed`, `backend_not_ready`, and `fully_ready`. The validator also supports internally consistent `not_applicable` backend evidence for offline non-production consistency tests, but this production profile marks the backend required.

## External Gate

The test has an independent dual gate. Phase 1 and fixture opt-ins do not authorize it. Plain pytest, fixture tests, broad marker expressions, and environment opt-in alone do not execute it.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PRODUCTION_SELF_TEST = "1"
python -m pytest test_sandbox_test_lab_phase3a_external.py -m external -q -s
```

The marker expression must be exactly `external`. No installer build or automatic invocation occurs. The external command is the only supported Phase 3A entry point.

## Evidence

The host starts one 590-second monotonic deadline and matching UTC deadline at run entry. It derives the guest terminal deadline exactly 45 seconds earlier and does not accept that value from a request caller. The value remains byte-for-byte stable through request writing and final prelaunch hash/contract validation. Immediately after that validation the host rechecks cancellation, expiration, and that more than the 45-second terminal-evidence margin remains; cancellation or insufficient time returns a structured result without calling the Sandbox launcher. Static timeout and reserve constants are validated fail closed. The guest never resets the total budget between phases and emits terminal evidence immediately, without sleeps, if the budget is exhausted. The host cross-validates the trusted deadline and requires lifecycle completion and completion creation no later than it.

The writable evidence mapping permits exactly `status.json`, `production-evidence.json`, `completion.json`, `heartbeat.json`, `guest-system.json`, and `lifecycle.log`, plus bounded atomic temporary names while writes are in progress. The host enforces per-file and total limits, UTF-8 JSON, duplicate-key rejection, exact field allowlists, regular non-reparse files, and no executable or script content.

Evidence includes product/version/profile; host and guest artifact checks; bounded lifecycle timestamps including optional `process_start_returned_at`; signed installer code plus lowercase unsigned eight-digit hex, UTC-derived duration, process-start/process-exit flags, and reboot result; installed-executable existence, type, non-reparse status, and diagnostic hash; root PID, owned-tree count, retained root and image-containment/authorization claims; HWND, exact title, visibility and owner PID; stable duration; safe backend state, HTTP status and two expected JSON values; first-launch, installation, GUI and full-readiness outcomes; managed-process and natural-auxiliary exit results; cleanup results; and bounded safe error/warning codes. It excludes host absolute paths, guest paths, command lines, arguments, environment data, global process/window inventories, headers, cookies, secrets, tokens, screenshots, video, and user data.

For a nonzero or crash exit only, the guest polls the Application log for at most ten seconds over the narrow installer run interval. It considers only Application Error event 1000 and Windows Error Reporting event 1001 mapped to exact basename `artifact.exe`. Safe top-level diagnostics are limited to crash-found state, application/module basenames, normalized exception and fault-offset hex, bounded WER event type, and diagnostic source. Missing events produce explicit unavailable values. Paths, messages, users, commands, environments, and dumps are never emitted, and crash diagnostics can never elevate a failure.

`owned_descendant_diagnostics` is bounded to 64 parent/creation-verified retained processes. Each entry contains exactly a safe image basename, role, canonical origin class, regular-file and reparse-chain results, optional lowercase SHA-256, bounded Authenticode status, Microsoft-signature boolean/null, relation/creation checks, and GUI/backend/cleanup eligibility. It contains no PID, path, command line, environment, identity, profile, or global inventory. Origins are segment-aware classifications against the derived install root, Windows root, Environment system folder, and canonical SysWOW64; System32 lookalikes do not qualify. Microsoft signing means a `Valid` Authenticode signature whose certificate organization is exactly `Microsoft Corporation`; the certificate subject is never serialized.

Diagnostics are non-elevating evidence. Passed runs require every diagnostic either to originate in `verified_install_root` or satisfy the exact trusted-conhost predicate. Accordingly, `all_images_contained=true` means every application image is contained in the verified install root and every external image is that narrowly authorized auxiliary. The trusted conhost cannot own accepted GUI/backend proof and is never cleanup-eligible. Any other system image, unavailable image, or outside/untrusted descendant sets `all_images_contained=false` and produces `owned_process_image_outside_install_root` only after the diagnostic and owned-tree count can be emitted. Unknown external images remain fail closed. The host independently checks every serialized predicate field and allows only case-insensitive basename `conhost.exe` with `canonical_system32`, regular/reparse-free proof, valid Microsoft signature, verified parent and launch-time creation, `system_helper`, and all eligibility false.

The host does not trust booleans alone. It cross-validates timestamps, installer start/return/exit/duration/signed-code/hex/crash consistency, artifact identity, installation and executable claims, root/tree/window identity, stable duration, exact backend proof, readiness classification, auxiliary authorization and natural exit, cleanup, warnings, and terminal status. Installer order must be `started <= Start returned <= finished`, and duration must equal `finished - started` within 0.01 seconds of timestamp serialization and three-decimal rounding. Negative, reversed, missing, or contradictory values are rejected; a coherent start failure retains its attempt timestamp but has no successful-return, finish, duration, or exit-code claim. Every present phase timestamp must be ordered within `started_at..completed_at`; the terminal lifecycle and heartbeat cannot be later than `completed_at`. The guest atomically writes `production-evidence.json`, then equal `status.json`, then creates `completion.json` last with a distinct `completion_created_at` no earlier than `completed_at` and within five seconds. No evidence is written after completion. Validated evidence is atomically snapshotted to host-only `logs/validated-production-evidence.json`.

## Cleanup And Security

Cleanup first proves that the exact backend PID belongs to the current retained Studio tree, was created after its root, still has the retained creation time, has exact image name `freelancerstudio-backend.exe`, and resolves inside the verified install root. Only then does it register a parent-filtered process-start observer for the production runtime's `taskkill.exe` child and post close to the revalidated exact owned window. Evidence records `production_cleanup_method=owned_backend_pid_tree`, backend ownership/image verification, and whether production `taskkill` was observed; it records no command line or path. If backend ownership is not proven, graceful close is withheld and the result fails closed.

The harness cleanup deadline is at most 15 seconds and always leaves the 15-second terminal-evidence reserve; owned targets and retained auxiliaries share that deadline rather than resetting it. A retained installer is also cleaned on every exit path using the remaining bounded budget. If needed, it calls `Kill` only on install-root retained process objects explicitly marked cleanup-eligible whose creation time still matches, in reverse descendant depth. It then waits for retained trusted auxiliaries to exit naturally within the same bounded interval and records `auxiliary_processes_exited`; a passed or complete cleanup requires this to be true when an auxiliary exists. Trusted conhost and unknown external descendants are never closed, killed, or otherwise managed. The harness never starts `taskkill`, performs process-name cleanup, inventories global processes, handles unrelated processes, or closes Sandbox globally. The host may stop only its retained Sandbox broker on cancellation, invalid evidence, or timeout; broker termination is not claimed as guest-tree cleanup.

The self-test performs no UI clicks, network-provider access, OpenCode call, generation, screenshot, video, or uninstall.

## Limitations

- The installer and checksum sidecar must already exist exactly at the ignored canonical paths.
- The installer is unsigned-trust-neutral here: identity is pinned by path, size, SHA-256, and sidecar, not by a publisher-certificate policy.
- The installed executable hash is recorded diagnostically because no separate trusted installed-image hash is available; launch trust comes from the pinned installer, clean install root, and regular non-reparse containment.
- Window verification proves exact title, visibility, owned-tree association, image containment, and stability, not rendering quality, responsiveness, or application workflows.
- Backend verification proves the fixed clean-run loopback health contract only. It deliberately does not accommodate a shifted dynamic port.
- The unchanged production Electron runtime calls `taskkill.exe /PID <owned-backend-pid> /T /F` from `frontend/main.js` when its final window closes. Phase 3A permits that production-owned behavior only after the backend ownership gate above. The harness neither invokes nor controls that command.
- Path and process checks are not handle-based defenses against a malicious same-user race inside the Sandbox. The input remains restricted to the immutable trusted installer.
- No uninstall is attempted because Windows Sandbox disposal provides isolation.
