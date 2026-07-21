# Interactive Sandbox Test Lab Phase 2A

## Purpose

Phase 2A adds a typed installer execution contract and an offline dry-run planner. It validates one selected artifact and describes the only executable and argument tokens a future Windows Sandbox guest may use. It does not launch Windows Sandbox, execute an installer, install or launch an application, or modify the host.

The contract exists to prevent installer metadata from becoming a general command-execution interface. Installer kind is an explicit trusted profile and is checked against the artifact. A filename or `.exe` extension is never used to guess silent switches.

The current AI Freelance Studio package is an electron-builder 24.13.3 NSIS assisted installer. Its configured product and executable name is `AI Freelance Studio`; the expected installed executable is `AI Freelance Studio.exe`. The inherited NSIS implementation supports silent `/S`, but static packaging inspection cannot prove the provenance or runtime behavior of a particular binary. Phase 2A therefore validates and plans only; it does not execute that binary.

## Supported Profiles

| Profile | Controlled executable | Fixed argument tokens | Phase 2A result |
|---|---|---|---|
| `nsis_exe` | `C:\SandboxTestLab\Input\artifact.exe` | `/S` | Supported dry-run plan |
| `msi` | `C:\Windows\System32\msiexec.exe` | `/i`, mapped MSI path, `/qn`, `/norestart`, `/L*v`, controlled evidence log | Supported dry-run plan |
| `portable_exe` | `C:\SandboxTestLab\Input\artifact.exe` | None | Supported launch-only future profile; no installation |
| `unsupported` | None | None | `supported=false` with `installer_profile_is_unsupported` blocker |

Only `.exe` is compatible with `nsis_exe` and `portable_exe`; only `.msi` is compatible with `msi`. Script artifacts including `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, and `.hta` are rejected. Known command interpreters including PowerShell, `cmd`, Windows Script Host, and `mshta` cannot be submitted as installer artifacts. Unsupported or unknown executables do not inherit NSIS switches based on their filename.

All profiles are user-scope, offline, and no-reboot. Machine-scope, network-enabled, and reboot-enabled recipes are rejected. Installation timeout is limited to 10 through 1800 seconds; launch timeout is limited to 1 through 300 seconds.

## Dry-Run CLI

```powershell
python -m sandbox_test_lab plan-install `
  --artifact "frontend\installers\AI Freelance Studio-Setup-1.0.0-beta.1-win.exe" `
  --sha256 B72AD863F045F7877E9BEB32826C2090D96BAFE09F73B68C1892072CD4F1EF1F `
  --installer-kind nsis_exe `
  --expected-executable "AI Freelance Studio.exe" `
  --json
```

`--sha256` is required and is recomputed from the regular, non-symlink, non-reparse artifact. The command does not accept a command string or arbitrary argument list. It does not accept a runtime root because it creates no run workspace.

A supported NSIS plan has the following abbreviated execution representation; policy, timeout, success-requirement, and profile-version fields are omitted here and described under Schema And Evidence Preparation:

```json
{
  "schema_version": 2,
  "supported": true,
  "installer_kind": "nsis_exe",
  "controlled_executable": "C:\\SandboxTestLab\\Input\\artifact.exe",
  "argument_tokens": ["/S"],
  "expected_paths": ["<sandbox-user-local-app-data>\\Programs\\AI Freelance Studio\\AI Freelance Studio.exe"],
  "required_evidence": ["artifact_sha256_guest", "installer_started_at", "installer_finished_at", "installer_exit_code", "reboot_required", "install_log", "installed_executable_found", "launch_started_at", "launched_process", "first_launch_verified"],
  "blockers": [],
  "warnings": [],
  "dry_run": true
}
```

The MSI execution representation is exactly:

```text
executable: C:\Windows\System32\msiexec.exe
arguments:
  /i
  C:\SandboxTestLab\Input\artifact.msi
  /qn
  /norestart
  /L*v
  C:\SandboxTestLab\Evidence\installer.log
```

`expected_executable` is a relative Windows `.exe` path below the controlled logical user application root. Absolute paths, drives, roots, traversal, UNC paths, URLs, environment expansion, scripts, and shell metacharacters are rejected. `expected_process_name`, when supplied, must be one `.exe` filename without a path or metacharacters.

## Exit Codes

| Code | Meaning for `plan-install` |
|---:|---|
| 0 | A supported dry-run plan was produced |
| 2 | Invalid command-line syntax or unknown installer kind reported by `argparse` |
| 4 | Invalid artifact/recipe, hash mismatch, or an explicit unsupported profile |
| 70 | Unexpected CLI boundary failure |

Unsupported profiles still print their structured plan and blockers. Other validation errors are written to stderr without a traceback.

## Security Boundary

- There is no `command` field and no raw-arguments field in the recipe or plan contract.
- Executable selection and every argument token are formed only by `plan_installation()`.
- `InstallationPlan` rejects executable/argument combinations that do not exactly match its normalized profile. Schema-v2 guest builders accept only the validated application request and recompute the plan rather than accepting a caller-supplied plan.
- The planner imports no `subprocess`, calls no `Popen`, and performs no execution. Therefore no shell is involved; existing Phase 1 subprocess boundaries continue to use argument lists and `shell=False`.
- NSIS can refer only to the staged `artifact.exe` in the read-only input mount.
- MSI can refer only to trusted `C:\Windows\System32\msiexec.exe` and the staged `artifact.msi`.
- MSI logging targets only `C:\SandboxTestLab\Evidence\installer.log`.
- Portable EXE receives no arguments and is not installed.
- The input and guest mounts remain read-only. The per-run evidence mount remains the only writable host mapping.
- Networking, clipboard, printer, audio input, video input, and vGPU remain disabled by the Phase 1 WSB contract.
- The project root, `generated_projects`, Studio configuration, auth storage, browser profiles, environment files, and host logs are not mounted or included in the plan.
- Request metadata is validated as host-only. It is absent from `InstallationPlan` and schema-v2 guest requests; secret-like metadata keys are rejected.
- Reboot and machine-scope installation are forbidden.
- Planning never creates a workspace and never invokes the Phase 1 runner or Windows Sandbox.

Path checks are Python-level checks and are not a handle-based defense against a same-user race. A future execution phase must repeat artifact and mapped-path validation immediately before broker and installer execution.

## Schema And Evidence Preparation

Phase 2A defines application protocol schema version 2 separately from Phase 1 schema version 1. A schema-v2 guest request contains only:

```text
schema_version
run_id
artifact_name
artifact_sha256_host
installation_plan
```

Host metadata and the original host artifact path are omitted. The planned artifact name and every execution path use controlled guest mappings.

The application phase vocabulary is:

```text
artifact_verified
installation_planned
installing
installed
launching
launched
verifying
completed
```

The outcome vocabulary is:

```text
passed
failed
timed_out
reboot_required
unsupported
infrastructure_error
```

Future evidence reserves these installation and launch fields:

```text
installer_kind
installer_started_at
installer_finished_at
installer_exit_code
reboot_required
install_log
installed_executable_found
launch_started_at
launched_process
first_launch_verified
```

Phase 2A initial evidence uses phase `installation_planned`, outcome `null`, runtime state `not_started`, and leaves every runtime field `null`. It never reports installation or launch success.

Schema-v2 plans also carry the validated profile version, install and launch timeouts, expected process name, user scope, reboot/network policies, and success requirements. These values are policy inputs for a future executor; Phase 2A does not claim that static inspection can guarantee an opaque NSIS or MSI package will honor scope or avoid requesting a reboot. A future executor must fail closed on a reboot request, scope violation, timeout, or missing success evidence.

## Phase 1 Compatibility

Phase 1 request, heartbeat, status, completion, bootstrap, evidence validation, state machine, runner lifecycle, external opt-in, and WSB generation remain unchanged. Existing Phase 1 workspaces continue to use schema version 1 and the fixed hash-verification bootstrap. Schema version 2 is only a prepared data contract and is not sent to the Phase 1 bootstrap.

## Not Implemented

- Real installer execution or application installation
- Application launch or first-launch verification
- UI automation
- Screenshots or video
- Uninstall
- QA Engine, repair loop, or delivery integration
- Studio API or UI
- Docker
- Hyper-V persistent provider
- Network access
