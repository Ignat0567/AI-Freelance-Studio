"""Environment checks that run once, immediately before an execution starts.

Why this exists as its own gate, separate from `check_readiness`:

`ConfiguredClaudeCodeExecutionClient.check_readiness()` asks the coding CLI `auth status`,
which answers *are there credentials on disk* -- not *do they still work*. A live run on
2026-08-17 passed readiness, transitioned to running, sent the ui_shell prompt, and died
18.7 seconds later on `api_error_status: 401`, an expired OAuth token. Readiness had said
yes because the credential file was there.

The same shape applies to the rest of the environment. Docker is only contacted at the
first QA gate, which is 6-8 minutes into a run, and Node is never checked at all -- so a
stopped Docker Desktop or a too-old Node spends most of a build before it reports. Every
check here is deliberately cheap enough to run before the client is told work has started.

Design rules, both learned from the runs this replaces:

* **Fail closed only on proof.** A definitive 401 blocks. A probe that merely fails to
  produce an answer -- timeout, missing binary path, unparsable output -- does not block,
  because an environment check that guesses wrong strands a working setup. Ambiguity is
  reported as `unknown` and the run proceeds.
* **Every check names its own remedy.** A blocker whose action is "Open Settings" when the
  real fix is `claude` in a terminal sends the next person to the wrong place.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

import claude_bridge

from .deployment import container_deploy_enabled
from .docker_qa_runner import docker_engine_available
from .models import ExecutionBlocker, StrictDomainModel
from .readiness import ReadinessResult, readiness_blocker

# Playwright's image ships Node 22; the toolchains generated projects use (Vite 6-8) need
# at least 20.19. Below 20 nothing builds, so that is the blocking floor.
MINIMUM_NODE_MAJOR = 20
# A generated project plus its node_modules runs to a few hundred MB; a Docker QA image
# pull needs more. Under 2 GB free, a run fails somewhere unpredictable instead of here.
MINIMUM_FREE_DISK_BYTES = 2 * 1024 * 1024 * 1024
# Long enough for a cold CLI start on Windows, short enough that a stuck probe cannot
# become the thing that delays the run it is protecting.
AUTH_PROBE_TIMEOUT_SECONDS = 90
PROVIDER_FAST_FAIL_SECONDS = 10

PreflightStatus = Literal["ok", "blocked", "unknown", "skipped"]

_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class PreflightCheck(StrictDomainModel):
    code: str
    label: str
    status: PreflightStatus
    message: str
    action: str = ""


class PreflightReport(StrictDomainModel):
    ready: bool
    checks: tuple[PreflightCheck, ...] = ()
    blockers: tuple[ExecutionBlocker, ...] = ()

    def readiness(self) -> ReadinessResult:
        return ReadinessResult.ready_result() if self.ready else ReadinessResult.blocked(*self.blockers)

    def summary_line(self) -> str:
        return "; ".join(f"{check.label}: {check.message}" for check in self.checks)


CODING_CLI_AUTH_EXPIRED = readiness_blocker(
    "preflight_coding_cli_auth_expired",
    "The coding CLI's login has expired. Run `claude` in a terminal, sign in again, then start this order.",
    "Re-authenticate",
)

DOCKER_UNAVAILABLE = readiness_blocker(
    "preflight_docker_unavailable",
    "Docker is not reachable, and QA runs inside it. Start Docker Desktop, or set FREELANCERSTUDIO_PHASED_QA_BACKEND=host to run QA on the bare host instead (less isolated).",
    "Start Docker Desktop",
)

NODE_MISSING = readiness_blocker(
    "preflight_node_missing",
    "Node.js was not found on PATH, and every generated web project is built with it. Install Node 20 or newer.",
    "Install Node",
)

NODE_TOO_OLD = readiness_blocker(
    "preflight_node_too_old",
    f"Node.js is older than {MINIMUM_NODE_MAJOR}, which the generated projects' build toolchain requires. Upgrade Node.",
    "Upgrade Node",
)

DISK_SPACE_LOW = readiness_blocker(
    "preflight_disk_space_low",
    "Less than 2 GB is free on the workspace drive. A build needs room for dependencies; free space before starting.",
    "Free disk space",
)

GROK_CLI_MISSING = readiness_blocker(
    "preflight_grok_cli_unavailable",
    "Grok CLI was not found. Install Grok Build TUI, then run `grok login` in a terminal.",
    "grok login",
)

GROK_NOT_LOGGED_IN = readiness_blocker(
    "preflight_grok_not_logged_in",
    "Grok is installed but not logged in. Run `grok login` in a terminal, then refresh readiness.",
    "grok login",
)

OLLAMA_NOT_REACHABLE = readiness_blocker(
    "preflight_ollama_unavailable",
    "Ollama is not reachable. Run `ollama serve`, then `ollama pull qwen2.5-coder:14b`.",
    "ollama serve",
)

OLLAMA_MODEL_MISSING = readiness_blocker(
    "preflight_ollama_model_missing",
    "No coding-capable Ollama model is installed. Run `ollama pull qwen2.5-coder:14b`.",
    "ollama pull qwen2.5-coder:14b",
)

OPENROUTER_KEY_MISSING = readiness_blocker(
    "preflight_openrouter_key_missing",
    "OpenRouter API key is not configured. Save an OpenRouter key in Settings, or set OPENROUTER_API_KEY.",
    "Open Settings",
)

PROVIDER_RECENTLY_FAILED = readiness_blocker(
    "preflight_provider_recently_failed",
    "The last live build died in under 10 seconds on the provider. Fix the login or local coder below, then retry this workspace.",
    "Retry this workspace",
)

PROVIDER_QUOTA = readiness_blocker(
    "provider_rate_limited",
    "The last live build hit a provider quota. Wait for the reset, then retry this workspace.",
    "Wait, then retry this workspace",
)


def looks_like_claude_session_limit(message: str) -> bool:
    """Claude leftover copy must not be treated as a Grok/Ollama rate limit."""
    lowered = (message or "").casefold()
    return "you've hit your session limit" in lowered or "europe/berlin" in lowered


def _probe_node_version(runner: Callable[[Sequence[str]], tuple[int, str]]) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    if shutil.which("node") is None:
        return "blocked", "not found on PATH", NODE_MISSING
    try:
        code, output = runner(["node", "--version"])
    except OSError as exc:
        return "unknown", f"could not be probed ({exc})", None
    if code != 0:
        return "unknown", "did not report a version", None
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", output.strip())
    if match is None:
        return "unknown", f"reported an unreadable version ({output.strip()[:40]})", None
    major = int(match.group(1))
    version = f"v{match.group(1)}.{match.group(2)}.{match.group(3)}"
    if major < MINIMUM_NODE_MAJOR:
        return "blocked", f"{version} is older than v{MINIMUM_NODE_MAJOR}", NODE_TOO_OLD
    return "ok", version, None


def _probe_disk_space(workspace_root: Path | None, usage_probe: Callable[[Path], int]) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    root = workspace_root
    while root is not None and not root.exists():
        root = root.parent if root.parent != root else None
    if root is None:
        return "unknown", "workspace drive could not be located", None
    try:
        free = usage_probe(root)
    except OSError as exc:
        return "unknown", f"could not be measured ({exc})", None
    gigabytes = free / (1024 * 1024 * 1024)
    if free < MINIMUM_FREE_DISK_BYTES:
        return "blocked", f"{gigabytes:.1f} GB free", DISK_SPACE_LOW
    return "ok", f"{gigabytes:.1f} GB free", None


def probe_coding_cli_credentials(
    *,
    binary_probe: Callable[[], str | None],
    runner: Callable[[Sequence[str], str, int], tuple[int, str, str]],
    timeout: int = AUTH_PROBE_TIMEOUT_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    """Spend one trivial model call to find out whether the stored login still works.

    `auth status` cannot answer this: it reads the local credential store, and an OAuth
    token that expired server-side still looks present there. The only thing that knows is
    the API, so the probe is a real -- and deliberately tiny -- request.

    Only a 401 blocks. Any other failure (rate limit, network, unparsable payload) leaves
    the run to proceed: those either resolve themselves or produce their own, more
    informative failure later, and none of them justifies refusing to start.
    """
    binary = binary_probe()
    if not binary:
        return "unknown", "coding CLI was not found; the adapter's own readiness check covers this", None
    command = [binary, "-p", "--output-format", "json", "--model", "sonnet"]
    # A 401 is not believed until it has been asked for three times, spread over a minute.
    #
    # 2026-08-26 21:35 and 2026-08-27 11:19: this probe got a 401 and blocked an unattended
    # run at its first order. Both times the same probe, unchanged and with nobody signing in,
    # answered "login accepted" minutes later -- 9 and 6 respectively. The CLI holds an OAuth
    # token it refreshes on demand, and the refresh is evidently not finished by the time the
    # failing call returns. The first version of this retry asked twice back to back, which is
    # a gap of seconds, and the second run was blocked exactly the same way.
    #
    # The gaps below are chosen against those two observations and no more: recovery inside a
    # few minutes, unmeasured within that. They are the cheapest thing that would have saved
    # both runs. A login that really is gone answers 401 all three times and costs 80 seconds
    # to establish, against the twenty minutes of build the block is protecting.
    backoffs = (0.0, 20.0, 60.0)
    for attempt, pause in enumerate(backoffs, start=1):
        if pause:
            sleeper(pause)
        try:
            code, stdout, stderr = runner(command, "Reply with the single word: ok", timeout)
        except OSError as exc:
            return "unknown", f"login could not be probed ({exc})", None
        if code == 0:
            if attempt == 1:
                return "ok", "login accepted", None
            return "ok", f"login accepted on attempt {attempt} (the first needed a token refresh)", None
        try:
            payload = json.loads(_ANSI.sub("", stdout))
        except ValueError:
            payload = None
        status = payload.get("api_error_status") if isinstance(payload, dict) else None
        if status != 401:
            detail = (stderr or stdout).strip()[:120] or f"exit code {code}"
            return "unknown", f"login probe was inconclusive ({detail})", None
    return "blocked", f"login has expired (HTTP 401 on {len(backoffs)} calls over {int(sum(backoffs))}s)", CODING_CLI_AUTH_EXPIRED


def _default_cli_runner(command: Sequence[str], prompt: str, timeout: int) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory(prefix="studio-preflight-") as scratch:
        # Run outside any project workspace: this call must not be able to touch generated
        # code, and a fresh empty directory also keeps it away from Studio's own repository.
        completed = subprocess.run(
            list(command),
            input=prompt,
            cwd=scratch,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _default_version_runner(command: Sequence[str]) -> tuple[int, str]:
    completed = subprocess.run(list(command), capture_output=True, text=True, timeout=20, check=False)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _default_free_bytes(path: Path) -> int:
    return shutil.disk_usage(str(path)).free


def probe_grok_cli(
    *,
    readiness_probe: Callable[[], dict] | None = None,
) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    """Cheap `grok models` check: Grok authors the spec even when Ollama writes files."""
    try:
        if readiness_probe is not None:
            payload = readiness_probe()
        else:
            import grok_bridge

            payload = grok_bridge.test_grok_readiness()
    except Exception as exc:
        return "unknown", f"could not be probed ({exc})", None
    if not isinstance(payload, dict):
        return "unknown", "Grok CLI returned an unreadable status", None
    if payload.get("ready"):
        detail = str(payload.get("account") or payload.get("message") or "logged in").strip()
        return "ok", detail, None
    code = str(payload.get("error_code") or "")
    message = str(payload.get("message") or "Grok CLI is not ready")
    if code == "grok_cli_unavailable":
        return "blocked", message, GROK_CLI_MISSING
    if code == "grok_not_logged_in":
        return "blocked", message, GROK_NOT_LOGGED_IN
    return "unknown", message, None


def probe_ollama_runtime(
    *,
    tags_probe: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    from .ollama_code_client import list_ollama_models, select_ollama_coding_model

    try:
        models = tags_probe() if tags_probe is not None else list_ollama_models()
    except Exception as exc:
        return "unknown", f"could not be probed ({exc})", None
    if not models:
        return "blocked", OLLAMA_NOT_REACHABLE.message, OLLAMA_NOT_REACHABLE
    selected = select_ollama_coding_model(models)
    if not selected:
        return "blocked", OLLAMA_MODEL_MISSING.message, OLLAMA_MODEL_MISSING
    return "ok", selected, None


def probe_openrouter_key(
    *,
    key_probe: Callable[[], bool] | None = None,
) -> tuple[PreflightStatus, str, ExecutionBlocker | None]:
    try:
        if key_probe is not None:
            present = bool(key_probe())
        else:
            from .openrouter_code_client import resolve_openrouter_api_key

            present = bool(resolve_openrouter_api_key())
    except Exception as exc:
        return "unknown", f"could not be probed ({exc})", None
    if present:
        return "ok", "API key configured", None
    return "blocked", OPENROUTER_KEY_MISSING.message, OPENROUTER_KEY_MISSING


def run_preflight(
    *,
    workspace_root: Path | None = None,
    environ: dict[str, str] | None = None,
    binary_probe: Callable[[], str | None] | None = None,
    cli_runner: Callable[[Sequence[str], str, int], tuple[int, str, str]] | None = None,
    version_runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    free_bytes_probe: Callable[[Path], int] | None = None,
    docker_probe: Callable[[], bool] | None = None,
    check_credentials: bool = True,
    ollama_tags_probe: Callable[[], tuple[str, ...]] | None = None,
    grok_probe: Callable[[], dict] | None = None,
    openrouter_key_probe: Callable[[], bool] | None = None,
    # Injected so a test exercising the 401 path does not spend the real backoff waiting.
    sleeper: Callable[[float], None] = time.sleep,
) -> PreflightReport:
    """Check the environment a live run depends on, in seconds rather than in minutes."""
    env = environ if environ is not None else dict(os.environ)
    checks: list[PreflightCheck] = []
    blockers: list[ExecutionBlocker] = []

    def record(code: str, label: str, outcome: tuple[PreflightStatus, str, ExecutionBlocker | None]) -> None:
        status, message, blocker = outcome
        checks.append(
            PreflightCheck(
                code=code,
                label=label,
                status=status,
                message=message,
                action=blocker.action if blocker is not None else "",
            )
        )
        if blocker is not None:
            blockers.append(blocker)

    coding_backend = (env.get("FREELANCERSTUDIO_CODING_BACKEND", "") or "").strip().lower()
    if coding_backend == "openrouter":
        checks.append(
            PreflightCheck(
                code="grok_cli",
                label="Grok CLI login",
                status="skipped",
                message="using OpenRouter as the coding worker",
            )
        )
    else:
        record("grok_cli", "Grok CLI login", probe_grok_cli(readiness_probe=grok_probe))

    uses_local_coder = coding_backend not in {"claude_code", "opencode_bridge", "grok", "openrouter"}
    uses_cloud_cli = coding_backend in {"claude_code", "opencode_bridge"}
    if uses_local_coder:
        record("ollama_runtime", "Ollama local coder", probe_ollama_runtime(tags_probe=ollama_tags_probe))
        checks.append(
            PreflightCheck(
                code="coding_cli_credentials",
                label="Coding CLI login",
                status="skipped",
                message="using local Ollama instead of a cloud coding CLI",
            )
        )
    elif uses_cloud_cli and check_credentials:
        record(
            "coding_cli_credentials",
            "Coding CLI login",
            probe_coding_cli_credentials(
                binary_probe=binary_probe or claude_bridge._discover_claude,
                runner=cli_runner or _default_cli_runner,
                sleeper=sleeper,
            ),
        )
    elif uses_cloud_cli:
        checks.append(PreflightCheck(code="coding_cli_credentials", label="Coding CLI login", status="skipped", message="not checked"))
    else:
        skip_reason = "using Grok CLI as the coding worker" if coding_backend == "grok" else "using OpenRouter as the coding worker"
        checks.append(
            PreflightCheck(
                code="coding_cli_credentials",
                label="Coding CLI login",
                status="skipped",
                message=skip_reason,
            )
        )
        if coding_backend == "openrouter":
            record(
                "openrouter_api_key",
                "OpenRouter API key",
                probe_openrouter_key(key_probe=openrouter_key_probe),
            )

    qa_backend = (env.get("FREELANCERSTUDIO_PHASED_QA_BACKEND", "docker") or "docker").strip().lower()
    docker_needed = qa_backend != "host" or container_deploy_enabled(env)
    if docker_needed:
        probe = docker_probe or docker_engine_available
        reachable = probe()
        record(
            "docker_engine",
            "Docker engine",
            ("ok", "reachable", None) if reachable else ("blocked", DOCKER_UNAVAILABLE.message, DOCKER_UNAVAILABLE),
        )
    else:
        checks.append(PreflightCheck(code="docker_engine", label="Docker engine", status="skipped", message="QA runs on the host and container deploy is off"))

    record("node_runtime", "Node.js", _probe_node_version(version_runner or _default_version_runner))
    record("workspace_disk_space", "Workspace disk space", _probe_disk_space(workspace_root, free_bytes_probe or _default_free_bytes))

    return PreflightReport(ready=not blockers, checks=tuple(checks), blockers=tuple(blockers))
