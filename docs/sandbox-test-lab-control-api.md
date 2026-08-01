# Sandbox Test Lab Control API

## Boundary

All routes require the process bearer token, a loopback client, the exact active Host, and the shared `require_local_only_request` dependency. POST routes also require the existing exact Origin or trusted Electron-main transport policy and `application/json`. Network-bound backends receive `403 network_bind_disallowed`; the API is never available over LAN mode.

The token and ownership internals are headers-only security material and are never accepted or returned in API payloads. Renderer code does not receive the token.

## Routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/sandbox-test-lab/capabilities` | Report loopback Test Lab readiness and supported operations |
| `POST` | `/api/sandbox-test-lab/runs` | Queue one allow-listed operation and return `202` |
| `GET` | `/api/sandbox-test-lab/runs/{run_id}` | Return an immutable, sanitized run snapshot |
| `GET` | `/api/sandbox-test-lab/runs/{run_id}/frame` | Return a live, best-effort thumbnail for an active `interactive_session` run |
| `POST` | `/api/sandbox-test-lab/runs/{run_id}/input` | Send one validated click/type/key action to an active `interactive_session` run |
| `POST` | `/api/sandbox-test-lab/runs/{run_id}/cancel` | Request cooperative cancellation of an owned run |

Run listing is omitted because the Job Service has no bounded retention/listing contract. Evidence retrieval is omitted because the Job Service intentionally does not retain a safe public evidence manifest. The API never accepts paths and does not serve evidence contents.

There are two deliberate, narrow exceptions, both scoped to `interactive_session` only -- every other profile always reports `frame_base64: null` / `executed: false`:

- **`/runs/{run_id}/frame`** (Phase 5b): returns a downscaled PNG (base64-encoded) of whatever `interactive_session`'s already-visible Sandbox window currently shows, captured host-side on each request via `PrintWindow`/`CopyFromScreen` -- no guest script is involved. Always `200` with `frame_base64: null` when nothing is available (wrong profile, no active run, or capture failed) rather than an error, since a frame is a live convenience, not a correctness artifact. Nothing is persisted or retained across requests; this is not the "safe public evidence manifest" the paragraph above rules out.
- **`/runs/{run_id}/input`** (Phase 5c): the real security-critical validation surface for this endpoint -- a closed `kind` vocabulary (`click`/`type`/`key`) with per-kind bounded fields (normalized `x`/`y` in `[0.0, 1.0]`, `text` capped at 500 characters with control characters rejected, `key` allow-listed to `enter`/`escape`/`tab`/`backspace`), validated identically at the Pydantic request layer and again in `SandboxInputAction.__post_init__` (defense in depth). Input is injected host-side (`SendInput`/cursor placement against the same identity-verified window `/frame` reads from) -- no guest script, no relaxation of any guest trust boundary. Every single action re-verifies real OS focus on the exact target window immediately before executing and sends nothing at all if that check fails; see `OwnedSandboxSession.send_input()`. A genuinely unknown `run_id` is `404 run_not_found` (unlike `/frame`, since a caller sending input needs to know clearly whether the target existed); `executed: false` with `200` covers every other reason nothing happened (wrong profile, not live right now, or a failed focus check inside the sandbox).

## Operations And States

The only accepted operations are `production_self_test`, `production_screenshot`, and `interactive_session`, each with a required empty `parameters` object. Commands, PowerShell, executable paths, arguments, environment variables, and arbitrary installer settings are rejected as extra or unknown fields.

Public lifecycle statuses are `queued`, `preparing`, `launching`, `running`, `cancelling`, `succeeded`, `failed`, `cancelled`, and `infrastructure_error`. Internal states are explicitly mapped; raw enum names, backend run IDs, process details, paths, and exception text are not returned.

Cancellation is cooperative. Queued and active runs accept cancellation. Repeated cancellation while cancelling is accepted without another service call, an already cancelled run returns `accepted=false`, and other terminal runs return `409 run_already_terminal`. Completion wins a cancellation race and terminal Job Service states remain absorbing.

## Idempotency

Launch requires one canonical UUID in the `Idempotency-Key` header. Exact retries while the key is retained return the original launch response without starting another job. Reusing a retained key with a different strict payload returns `409 idempotency_conflict`. The process-local registry retains the 256 most recent keys, so clients must not retry an evicted request; it is not authorization or persistent identity.

## Availability And Execution

Capability reasons are stable and limited to `test_lab_disabled`, `sandbox_capability_unavailable`, and `job_service_unavailable`. Capability detection reuses the existing Windows Sandbox checks and does not launch Sandbox.

The application-owned Job Service now has a production runner bridge (`sandbox_test_lab/production_bridge.py`, wired unconditionally at backend startup in `main.py`). The Job Service and Sandbox capability remain disabled unless the existing explicit external-execution opt-ins are set; the control API does not enable them, mutate environment variables, or run Windows Sandbox, Store CLI, installers, packaging, or signing by itself. Tests may still inject deterministic offline services in place of the production bridge.

Known limits are deliberate: there is one active run, no history API, no evidence download, and no arbitrary executor. Shutdown waits are bounded and cancellation remains cooperative; an already blocked serialized runner call can delay backend cancellation delivery until that call returns.

## Trusted Desktop UI

The Studio page uses six semantic preload operations backed by fixed Electron-main requests. Renderer code cannot provide a URL, HTTP method, headers, arbitrary body, command, path, or environment value. Electron main validates the exact main frame and owned renderer origin, builds the fixed Test Lab request, adds authorization from process memory, rejects redirects, bounds response size and time, and returns only allow-listed response fields.

Direct renderer requests to `/api/sandbox-test-lab/**`, including encoded path forms, are deliberately excluded from normal browser token injection. Renderer-supplied authorization and trusted-transport headers are stripped on every request and redirect hop. The UI holds only the current run and a pending idempotency key in memory; neither is persisted as run history. Electron binds a pending launch key to the current backend instance and rejects reuse after an instance change.
