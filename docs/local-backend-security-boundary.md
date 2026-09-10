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

The primary control is the loader. The invocation pins `--setting-sources` to the empty value, so neither the workspace's configuration nor the operator's is loaded: no `.claude/` and no `CLAUDE.md` from the working directory reaches the run, no artifact of one order can execute code during another, and nothing an operator installs into their own `~/.claude` can reach an unattended order. Authentication is resolved separately and is unaffected; model and tool selection are passed explicitly on the command line.

One residual exposure is accepted rather than closed. The loader governs what is loaded, not what the agent chooses to open: a `CLAUDE.md` left in the working directory is still a readable file, and measurement shows an agent asked about "this project" will read it and answer from it. Clearing the directory before each run was implemented and then removed, because reading files inside the project under construction is the agent's ordinary work, deleting whatever a client supplied has its own cost, and a control whose stated purpose has collapsed is worse than no control -- it invites the loader flag to be dropped later on the belief that something downstream still cleans up. If orders begin carrying client-supplied templates, this is the decision to revisit, and the place to revisit it is template intake rather than the CLI call.

`remove_executable_agent_config` runs at delivery, for every stack, removing `.claude/` alone. This is a distinct concern from everything above and does not depend on any of it: a `.claude/` in a shipped project would execute on the *client's* machine the moment they opened it in agent tooling. A `CLAUDE.md` the build wrote is documentation and ships with the rest.

Skills, if adopted, are subject to the same boundary and are governed by `docs/agent-skills.md`: vendored into this repository, never fetched at runtime, and never reachable from an unattended order except through an explicitly passed directory.

`provider_adapters.py` (`CLISubscriptionAdapter.execute`) runs vendor CLIs with the generated project as working directory and does not yet pin an equivalent isolation flag for each vendor. That path is not the pinned coding backend; the gap is recorded here rather than assumed closed.

## Threat Model

1. A malicious website cannot supply the process token, and Electron injects it only for the trusted Studio renderer and owned backend endpoint. Exact Origin and Host checks provide defense in depth against localhost CSRF and DNS rebinding.
2. Untrusted renderer content cannot read the token from preload, DOM, storage, cookies, URLs, or static JavaScript. Compromise of the trusted Studio renderer can still invoke the same operations available to that renderer; this boundary does not provide per-feature renderer authorization.
3. An ordinary local process cannot invoke protected routes without the token. A same-user process capable of inspecting process memory or inherited handles is outside this boundary's guarantee.
4. A process occupying a stale stored port is never sent the bearer token. Electron trusts the port only from its child control pipe and requires a matching launch/instance HMAC proof, so a stale port file or compatible `/health` service cannot be adopted.
5. A LAN device cannot invoke the local boundary because clients must be loopback. Network bind mode does not enable local-only capabilities and is not a remote multi-user authentication mode.
6. The token is never logged or returned, is not written to the port file or configuration, and is not passed in argv or URLs. Startup diagnostics redact common secret labels as an additional safeguard.

7. A generated project cannot influence the execution of a later order through configuration it leaves on disk, and cannot instruct the agent through documentation it leaves there. Verified by direct observation on 2026-09-10, against the exact flag set the worker uses: a `SessionStart` hook placed in a workspace's `.claude/settings.json` ran on a default CLI invocation and did not run once `--setting-sources` was pinned; a `CLAUDE.md` naming a codename was answered back on default sources and answered `UNKNOWN` with sources pinned, one turn each with tools disallowed so that reading the file was not an available route; and the same test against a throwaway `CLAUDE_CONFIG_DIR` confirmed the operator's own scope is excluded as well.

   The tool-use control in that second test is the whole of its validity. An earlier run of it left tools enabled, and the model answered with the codename because it had opened the file itself — which was recorded here as the flag failing to cover `CLAUDE.md`. It does cover it. What that misreading did establish is that an agent will read such a file when one is present, which is the accepted residual exposure named above, not something this boundary closes.
8. A client cannot be handed a project that executes code when they open it. `.claude/` is removed from every delivery. This is a property of what Studio ships, not of what Studio runs, and it is checked at delivery because the last gate to touch a workspace runs after the last coding call.

Administrator/root-equivalent attackers that can read another process's memory, inject into Electron/Python, or replace trusted application binaries are explicitly out of scope.
