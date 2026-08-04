# Local Backend Security Boundary

## Scope

The Studio desktop boundary protects FastAPI HTTP and WebSocket operations with a fresh 256-bit process token. Electron main owns the token, sends it to a newly spawned backend over one-shot stdin, and receives the non-secret port descriptor over a child-owned control pipe. It verifies ownership with a fresh HMAC challenge before injecting the token only into requests from the trusted Studio `webContents` to the exact owned backend origin. The preload API does not expose the token.

Public routes are limited to frontend/static bootstrap, `GET /health`, and the non-secret challenge endpoint `GET /health/owner`. The ownership endpoint returns a one-challenge HMAC proof, never the bearer token. `/api/**` and project-log WebSockets are authenticated. HTTP mutations additionally require exact Host and Origin policy plus strict request media types. Upload routes remain explicitly classified multipart mutations and still require authentication.

The existing routes are classified as follows:

| Class | Routes | Policy |
| --- | --- | --- |
| Public bootstrap/health | `/`, static assets, `/health`, `/health/owner` | No secret or local state disclosure; owner route proves token possession with HMAC |
| Authenticated read | `/api/**` GET routes | Token, loopback client, exact active Host |
| Privileged mutation | `/api/**` POST/PUT/PATCH/DELETE | Read policy plus exact Origin and strict media type |
| Sandbox Test Lab | `/api/sandbox-test-lab/**` | Common authenticated policy plus mandatory local-only dependency; unavailable for network binds |
| WebSocket | `/ws/projects/{project_id}/logs` | Token header, loopback client, exact Host and Origin before accept |
| Legacy unsafe mutation | Multipart uploads and host-control routes | Authenticated; uploads are the only multipart exceptions |

`GET /api/system/open-path` is removed. AI configuration verification is POST because it may regenerate OpenCode configuration. Provider-list reads no longer persist normalization changes.

Local-only capabilities, including Sandbox Test Lab operations, are unavailable whenever the configured bind address is not an IP loopback address or `localhost`. A hostname that merely resolves to loopback is not trusted for this classification. Test Lab capability requests in network mode return the existing `403 network_bind_disallowed` policy response rather than an availability document.

## Threat Model

1. A malicious website cannot supply the process token, and Electron injects it only for the trusted Studio renderer and owned backend endpoint. Exact Origin and Host checks provide defense in depth against localhost CSRF and DNS rebinding.
2. Untrusted renderer content cannot read the token from preload, DOM, storage, cookies, URLs, or static JavaScript. Compromise of the trusted Studio renderer can still invoke the same operations available to that renderer; this boundary does not provide per-feature renderer authorization.
3. An ordinary local process cannot invoke protected routes without the token. A same-user process capable of inspecting process memory or inherited handles is outside this boundary's guarantee.
4. A process occupying a stale stored port is never sent the bearer token. Electron trusts the port only from its child control pipe and requires a matching launch/instance HMAC proof, so a stale port file or compatible `/health` service cannot be adopted.
5. A LAN device cannot invoke the local boundary because clients must be loopback. Network bind mode does not enable local-only capabilities and is not a remote multi-user authentication mode.
6. The token is never logged or returned, is not written to the port file or configuration, and is not passed in argv or URLs. Startup diagnostics redact common secret labels as an additional safeguard.

Administrator/root-equivalent attackers that can read another process's memory, inject into Electron/Python, or replace trusted application binaries are explicitly out of scope.
