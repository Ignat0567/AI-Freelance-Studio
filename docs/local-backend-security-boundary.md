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

## Agent Execution Boundary

A second boundary, independent of the HTTP one above, separates a generated project from the coding agent that produces the next one. `order_workflow/claude_code_client.py` invokes the Claude Code CLI with its working directory set to the generated project and with permissions bypassed, because a non-interactive call has no TTY to answer a permission prompt. The CLI's default behaviour is to load `.claude/` from that working directory, and a settings file is not inert data: it can declare hooks, which are shell commands, and skills, which are model-invocable bundled scripts. The generated project is a directory the agent itself writes into, and will hold client-supplied templates and repositories once orders carry them.

The boundary is held by two mechanisms, because one is not sufficient.

The invocation pins `--setting-sources` to the empty value, loading neither the workspace's settings nor the operator's. No `.claude/` on disk reaches the run, so no artifact of one order can execute code during another, and nothing an operator installs into their own `~/.claude` can reach an unattended order. Authentication is resolved separately and is unaffected; model and tool selection are passed explicitly on the command line.

That flag does not cover `CLAUDE.md`, which is loaded by a separate mechanism and was measured still reaching the model with setting sources pinned to empty. A `CLAUDE.md` is not executable, but it is read as project instructions, and an unattended order must take instructions only from its own brief. The workspace is therefore cleared as well as the loader restricted: `scrub_agent_config` removes both `.claude/` and `CLAUDE.md` from the project directory before every CLI invocation, not only the first, because a build phase can write either file and the repair calls that follow run in the same directory. Removals are reported into the run's event stream rather than performed silently.

At delivery the same helper runs again, for every stack, removing `.claude/` alone. A `.claude/` in a shipped project would execute on the client's machine the moment they opened it in agent tooling; a `CLAUDE.md` the build wrote is documentation and ships with the rest.

Skills, if adopted, are subject to the same boundary and are governed by `docs/agent-skills.md`: vendored into this repository, never fetched at runtime, and never reachable from an unattended order except through an explicitly passed directory.

`provider_adapters.py` (`CLISubscriptionAdapter.execute`) runs vendor CLIs with the generated project as working directory and does not yet pin an equivalent isolation flag for each vendor. That path is not the pinned coding backend; the gap is recorded here rather than assumed closed.

## Threat Model

1. A malicious website cannot supply the process token, and Electron injects it only for the trusted Studio renderer and owned backend endpoint. Exact Origin and Host checks provide defense in depth against localhost CSRF and DNS rebinding.
2. Untrusted renderer content cannot read the token from preload, DOM, storage, cookies, URLs, or static JavaScript. Compromise of the trusted Studio renderer can still invoke the same operations available to that renderer; this boundary does not provide per-feature renderer authorization.
3. An ordinary local process cannot invoke protected routes without the token. A same-user process capable of inspecting process memory or inherited handles is outside this boundary's guarantee.
4. A process occupying a stale stored port is never sent the bearer token. Electron trusts the port only from its child control pipe and requires a matching launch/instance HMAC proof, so a stale port file or compatible `/health` service cannot be adopted.
5. A LAN device cannot invoke the local boundary because clients must be loopback. Network bind mode does not enable local-only capabilities and is not a remote multi-user authentication mode.
6. The token is never logged or returned, is not written to the port file or configuration, and is not passed in argv or URLs. Startup diagnostics redact common secret labels as an additional safeguard.

7. A generated project cannot influence the execution of a later order through configuration it leaves on disk, and cannot instruct the agent through documentation it leaves there. Verified by direct observation on 2026-09-10, in three parts: a `SessionStart` hook placed in a workspace's `.claude/settings.json` ran on a default CLI invocation and did not run once `--setting-sources` was pinned; the same test against a throwaway `CLAUDE_CONFIG_DIR` confirmed the operator's own scope is excluded as well; and a `CLAUDE.md` naming a codename had that codename answered back *with* the flag pinned, which is why the workspace is cleared rather than the loader trusted alone. A `CLAUDE.md` in a parent directory was not read, so Studio's own files do not reach a generated project by this route.
8. A client cannot be handed a project that executes code when they open it. `.claude/` is removed from every delivery. This is a property of what Studio ships, not of what Studio runs, and it is checked at delivery because the last gate to touch a workspace runs after the last coding call.

Administrator/root-equivalent attackers that can read another process's memory, inject into Electron/Python, or replace trusted application binaries are explicitly out of scope.
