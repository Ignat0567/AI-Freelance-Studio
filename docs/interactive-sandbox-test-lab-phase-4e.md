# Interactive Sandbox Test Lab Phase 4E

## Purpose

Phase 4E is the production runner bridge: the layer that turns the blocking, synchronous Phase 3A/3B runners (`ProductionSelfTestRunner`, `ScreenshotSelfTestRunner`) into an asynchronous, pollable job that the backend API and the Electron UI can drive without blocking a request thread for up to 590 seconds. It adds no new Sandbox behavior, no new guest script, and no new evidence contract — every install/launch/capture/cleanup guarantee from Phases 1 through 3B is unchanged. Phase 4E only orchestrates *when* those runners run and *how* their result is surfaced.

Three files carry the phase: `sandbox_test_lab/production_bridge.py` (async adapter over the blocking runners), `sandbox_test_lab/job_service.py` (single-active-run job orchestration used by the API), and `api/sandbox_test_lab.py` (the FastAPI routes). `docs/sandbox-test-lab-control-api.md` documents the route contract; this document covers the bridge and job layers underneath it.

## Wiring And Execution Gate

`create_production_sandbox_runtime()` (`production_bridge.py`) is called unconditionally at backend startup, from `main.py`, on every process start — there is no feature flag around *whether the bridge exists*. What is gated is whether it can ever actually run Sandbox:

1. The environment variable `FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_TEST_LAB_UI` must equal `"1"` (`PRODUCTION_UI_EXTERNAL_OPT_IN`). If unset, `create_production_sandbox_runtime()` returns a job service with `enabled=False` and a permanently `SandboxAvailability.disabled` provider — every route call gets `test_lab_disabled`.
2. If the opt-in is set, `validate_trusted_production_artifact()` (`production_self_test.py`) must succeed: the exact pinned installer (`frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe`, exact size, exact SHA-256, matching `.sha256` sidecar, no symlinks/reparse points anywhere in the path, 2 GiB free disk) must be present. If it isn't, the runtime is force-disabled with `SANDBOX_UNAVAILABLE` rather than failing at run time.
3. Both `ProductionSandboxTestLabRunner.prepare()` and `.launch()` re-check the same opt-in at call time (`_require_opt_in()`), so flipping the env var off mid-session blocks a new launch even if the process-level runtime was constructed while it was on.

Only two profiles exist, and both point at the same pinned self-installing artifact — there is no way to target an arbitrary customer-delivered installer through this bridge. `production_self_test` runs the Phase 3A install/launch/backend-health self-test; `production_screenshot` runs the Phase 3B screenshot self-test on top of the same install. Both are exercises of the harness against the app's own packaged output, not a generic "audit any installer" capability.

## State Machine

`ProductionSandboxTestLabRunner` (`production_bridge.py`) implements the `prepare → launch → status/evidence → cancel` protocol expected by `SandboxTestLabAdapter`:

- `prepare(profile)` builds the fixed request (`ProductionSelfTestRequest` or `ScreenshotSelfTestRequest`) and returns a `PREPARED` snapshot. Only one run may be prepared at a time per runner instance.
- `launch(run_id)` starts a background `Thread` running the blocking runner's `.run()` and returns immediately with a `LAUNCHING` snapshot.
- `status`/`evidence` return the last snapshot written by the worker thread; no additional work happens on these calls.
- `cancel(run_id)` sets a cooperative `threading.Event`; the worker observes it between polls of the guest evidence files (poll interval matches the blocking runner's own `poll_interval`, 0.5s). A run that already reached `PASSED` internally is retroactively reported as `CANCELLED` if cancellation raced it.

Above this, `SandboxTestLabJobService` (`job_service.py`) is the actual thing the API talks to: "single-active-run, in-process orchestration". It owns a second background thread per job that polls the adapter (`prepare` → `launch` → loop of `status`/`evidence` at `poll_interval=0.1s` until terminal), normalizes results into a 5-field `SandboxJobSnapshot` (`run_id`, `profile`, `status`, `progress`, `manual_close_required`), and persists that snapshot after every transition via `JsonSandboxJobStateStore` (`<runtime_dir>/sandbox-test-lab-jobs.json`). Only one job — of either profile — can be active system-wide; a second `start()` call while one is active raises `sandbox_job_already_active`.

On backend restart, `_restore()` reads the persisted snapshots and force-terminalizes anything not already terminal to `INTERRUPTED` — a job snapshot left `RUNNING` on disk means the process died mid-run, not that the run is still going.

## Exact Contract

The job snapshot persisted to `sandbox-test-lab-jobs.json` has exactly five fields (`job_service.py:53-79`, schema-validated on load — an unrecognized key set makes the *entire* store fail to load with `sandbox_job_state_invalid`, not just the offending entry):

```text
run_id, profile, status, progress, manual_close_required
```

`docs/sandbox-test-lab-control-api.md` documents the public API mapping on top of this (route table, idempotency, cancellation semantics, the `succeeded`/`failed`/`cancelled`/`infrastructure_error` public status vocabulary) — that document is the authoritative contract for anything client-facing. This document only adds the bridge/job internals underneath it.

## Failure And Cleanup

`ProductionSelfTestRunner`/`ScreenshotSelfTestRunner` (`production_runner.py`, `screenshot_runner.py`) compute real, short, safe-string diagnostics for every run — `exit_reason` and an `errors` list (e.g. `terminal_evidence_missing_after_completed_heartbeat`, `production_completion_after_deadline`, `owned_sandbox_session_cleanup_failed_<ExceptionType>`) — and always write them to `host-result.json` and `logs/host.log` inside the run's workspace (`%LOCALAPPDATA%\AI Freelance Studio\sandbox-test-lab\runs\<run_id>\`). That workspace is ephemeral: nothing else in the codebase archives or retains it, so historically a run's failure reason has been unrecoverable once the directory is gone.

Two things changed in this pass to make failures diagnosable after the fact, without touching the strict, schema-validated `SandboxJobSnapshot`/`SandboxTestLabResult` contracts (so `sandbox-test-lab-jobs.json` stays backward compatible):

- **`_translate()` bug fix** (`production_bridge.py`): `SandboxDiagnosticCode.SANDBOX_OWNERSHIP_FAILED` was defined in the enum but never emitted. It now fires whenever cleanup failed *and* the guest test itself did not reach validated evidence (the common historical case — test failed, sandbox also needed a manual close) — previously this case surfaced no diagnostic code at all, only `manual_close_required=true` with no explanation. The existing `SANDBOX_GUEST_SUCCEEDED_OWNERSHIP_FAILED` code (cleanup failed but the guest test *did* pass) is unchanged.
- **Diagnostics archival** (`sandbox_test_lab/runner.py:archive_run_diagnostics`): both blocking runners now accept a `diagnostics_root: Path | None` constructor parameter (wired by `create_production_sandbox_runtime()` to `<runtime_dir>/sandbox-test-lab-diagnostics`). On every terminal `_finish()`, `host-result.json`, `host.log`, and whichever validated evidence snapshot exists (`validated-production-evidence.json` or `validated-screenshot-evidence.json`) are best-effort copied into `<diagnostics_root>/<run_id>/`, then the diagnostics root is pruned to the most recent 20 run directories by modification time. This is deliberately separate from the ephemeral run workspace and from the public job snapshot: it exists purely as a local debugging aid (no API surface reads it), never raises (a diagnostics-copy failure must never fail or mask the real run result), and only ever contains the same short, safe strings the runners already compute — no paths, secrets, or free text, matching the existing evidence-validation discipline from Phases 1-3B.

Manual-close handling itself is unchanged from earlier phases: if the harness's own owned-session cleanup fails, `manual_close_required=true` propagates through the snapshot and the operator must close the leftover Sandbox window by hand (`ensure_no_active_windows_sandbox_session` in `production_self_test.py` blocks the next launch until that happens). There is no automatic retry, consistent with Phase 3B.

## Release Checklist

`production_self_test.py:29-34` hardcodes the exact pinned artifact identity (`PRODUCT_VERSION`, `INSTALLER_FILENAME`, `INSTALLER_SIZE`, `INSTALLER_SHA256`). This is fail-loud by design — `validate_trusted_production_artifact()` refuses to run against anything else — but it is not automated: whoever cuts a new installer build must update these four constants (and the `.sha256` sidecar under `frontend/installers/`) by hand, or every self-test run fails immediately with an artifact-identity mismatch before ever touching Sandbox. Add this as an explicit step to the release process.

## External Gate

Real execution requires the Electron/API-level opt-in from "Wiring And Execution Gate" above (`FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_TEST_LAB_UI=1`) — this is a superset gate on top of the independent Phase 3A/3B external opt-ins (`FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PRODUCTION_SELF_TEST`, `FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_SCREENSHOT_EXTERNAL`) those runners still enforce internally. `test_sandbox_test_lab_phase4e_external.py` exercises the real bridge-to-runner path end to end and self-skips without both the environment opt-in and an exact `-m external` marker selection:

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_TEST_LAB_UI = "1"
python -m pytest test_sandbox_test_lab_phase4e_external.py -m external -q -s
```

This is also how to validate this change: run it, and if a run fails, `<runtime_dir>/sandbox-test-lab-diagnostics/<run_id>/host-result.json` now survives past the ephemeral workspace and contains the real `exit_reason`/`errors` for that run.

## Not Implemented

Phase 4E intentionally does not implement:

- Run history or a listing API — the Job Service has no bounded retention/listing contract by design (`docs/sandbox-test-lab-control-api.md`).
- Evidence download or a public evidence manifest endpoint.
- Auditing an arbitrary, customer-delivered installer — both profiles always target this app's own pinned build.
- Automatic retry after a failed or manual-close-required run.
- More than one concurrent run, of either profile, system-wide.
- CI scheduling of the external end-to-end path — it remains a manual, opt-in-gated developer action.
- A public API surface for the new diagnostics archive — it is a local file-system debugging aid only, not part of the client-facing contract.
