"""Delegates coding tasks to the official Claude Code CLI -- the Anthropic-subscription
counterpart to ConfiguredOpenCodeExecutionClient (which delegates to OpenCode). Which one
runs is picked by select_coding_execution_client() based on which CLI is actually logged
in right now, never by a stored config flag: Claude Code CLI owns its own `claude auth
login` session and this module never reads or sets ANTHROPIC_API_KEY.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import claude_bridge

from .executors import CancellationToken, ExecutionEventSink, emit_coding
from .models import EventLevel, TokenUsage
from .production_adapter import OpenCodeExecutionClient, OpenCodeExecutionResult
from .readiness import CLAUDE_CODE_UNAVAILABLE, ReadinessResult
from .workspace_processes import stop_processes_left_in_workspace

# Measured, not guessed. 900s was sized when a phase prompt asked for screens and navigation
# and little else; once the ui_shell prompt started stating the visual gate's criteria (approved
# palette, AA contrast, 375px layout, tap targets) the first attempt legitimately does more
# work, and measured build times went from ~6.5min to 11.5min, 12.5min, and one run that
# crossed 15min and was killed with nothing to show -- a whole build's cost for no artifact,
# because a timed-out phase leaves no checkpoint. The ceiling has to fit the job it is now
# asking for; prevention is only cheaper than repair if the prevention is allowed to finish.
CLAUDE_CODE_TASK_TIMEOUT = 1500
# A repair call is a narrower job than the build it follows: the project already exists and
# the prompt names the exact gate output to fix. It still gets its own, smaller clock than a
# build, so that a repair which is going nowhere cannot eat the phase. See run_qa_repair_loop,
# which passes this per call.
#
# 450 -> 900 on 2026-08-26, decided from 13 recorded repair calls rather than from the feeling
# that the number looked low (which it had done for six days on a sample the ceiling itself
# was censoring -- see b55028a and bench/budget.py):
#
#   ceiling 450s   13 calls, 4 stopped at it (31%)
#   finished       n=9  median=216s  p90=411s (91% of the ceiling)
#
# Nearly a third killed and the survivors pressed against the limit is the shape of a ceiling
# that decides outcomes. Two direct observations settled where to put it: the killed calls
# wrote their first file at ~444s, so 450 cut exactly when output began landing; and build
# calls, doing strictly more work under 1500s, peak at 74%. 900 gives the writing half of a
# repair as much room as the reading half took, and two repairs still stay inside a build's
# worth of wall time.
CLAUDE_CODE_REPAIR_TIMEOUT = 900


@dataclass(frozen=True, slots=True)
class _InvokeOutcome:
    returncode: int
    stdout_text: str
    stderr_text: str
    timed_out: bool = False
    cancelled: bool = False
    # Anything the call raised inside the workspace and left running, already stopped by the
    # time this is returned. Carried so the caller can put it in the event stream: a build
    # that had to have processes killed after it is a fact about the run, not housekeeping.
    stopped_processes: tuple[str, ...] = ()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def _extract_usage(payload: dict | None) -> TokenUsage | None:
    """Read the exact usage/cost figures Claude Code CLI's own JSON output reports for
    this call -- present on both successful and failed (e.g. rate-limited) invocations,
    since the CLI still bills/reports for the API round-trip that hit the error."""
    if not isinstance(payload, dict):
        return None
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    try:
        return TokenUsage(
            total_cost_usd=float(payload.get("total_cost_usd") or 0.0),
            input_tokens=int(raw_usage.get("input_tokens") or 0),
            output_tokens=int(raw_usage.get("output_tokens") or 0),
            cache_read_input_tokens=int(raw_usage.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(raw_usage.get("cache_creation_input_tokens") or 0),
        )
    except (TypeError, ValueError):
        return None


def _extract_rate_limit_message(payload: dict | None) -> str | None:
    """The CLI's own human-readable rate-limit message (e.g. "You've hit your session
    limit - resets 1:10am (Europe/Berlin)"), only present once a limit is actually hit --
    Claude Code CLI has no separate command to check quota before hitting it."""
    if not isinstance(payload, dict) or payload.get("api_error_status") != 429:
        return None
    result = payload.get("result")
    return result.strip()[:240] if isinstance(result, str) and result.strip() else None


def _ensure_isolated_git_repo(workspace_path: Path) -> None:
    """Give the generated project its own .git so `git diff`/`git status` inside the
    Claude Code CLI's sandbox stay scoped to it -- workspace_path lives underneath
    Studio's own repo, and without this, git plumbing commands (which the CLI's sandbox
    does not block, unlike file Read/Write/Edit) walk up to the parent .git and expose
    Studio's own uncommitted source changes to the model generating the project."""
    if (workspace_path / ".git").exists():
        return
    try:
        subprocess.run(["git", "init"], cwd=str(workspace_path), capture_output=True, timeout=10, check=False)
    except OSError:
        pass


class ConfiguredClaudeCodeExecutionClient:
    def check_readiness(self) -> ReadinessResult:
        binary = claude_bridge._discover_claude()
        if not binary:
            return ReadinessResult.blocked(CLAUDE_CODE_UNAVAILABLE)
        code, stdout, _stderr = claude_bridge._run_capture([binary, "auth", "status"], timeout=15)
        if code != 0:
            return ReadinessResult.blocked(CLAUDE_CODE_UNAVAILABLE)
        try:
            status = json.loads(_strip_ansi(stdout))
        except ValueError:
            return ReadinessResult.blocked(CLAUDE_CODE_UNAVAILABLE)
        if not status.get("loggedIn"):
            return ReadinessResult.blocked(CLAUDE_CODE_UNAVAILABLE)
        return ReadinessResult.ready_result()

    def execute_project_prompt(
        self,
        prompt: str,
        workspace_path: Path,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        model: str | None = None,
        timeout: int | None = None,
    ) -> OpenCodeExecutionResult:
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Claude Code execution was cancelled before invocation.")
        binary = claude_bridge._discover_claude()
        if not binary:
            return OpenCodeExecutionResult(success=False, summary="Claude Code CLI is not available.")

        _ensure_isolated_git_repo(Path(workspace_path))
        event_sink.emit(stage="implementation", agent="Claude Code", progress=50, message="Invoking Claude Code CLI")
        emit_coding(event_sink, "Claude Code is writing project files...\n", agent="Claude Code")
        # No --bare: it forces standard (prompting) permission behavior, which blocks
        # every file write in this non-interactive context with no TTY to answer the
        # prompt. _ensure_isolated_git_repo() above already stops `git diff`/`git status`
        # (which --bare would otherwise be needed to guard against) from walking up to
        # Studio's own repo. --setting-sources below, not --bare, is what isolates this
        # call's configuration -- it does that without touching permission mode, so the
        # reason --bare was rejected does not apply to it.
        # The prompt is sent over stdin, not as a command-line argument: prompts routinely
        # exceed cmd.exe's ~8191-character command-line limit on Windows (claude.cmd is a
        # batch wrapper, so every invocation goes through cmd.exe), which previously made
        # every non-trivial execution fail immediately with a garbled shell-level error
        # before Claude Code CLI itself ever ran.
        cmd = [
            binary, "-p",
            "--output-format", "json",
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--permission-mode", "bypassPermissions",
            "--tools", "default",
            # Load no settings sources at all. The CLI's default is to read `.claude/`
            # from its working directory, and this call's working directory is the
            # generated project -- a directory this very call writes into, and which
            # will hold client-supplied templates and repositories as soon as orders
            # start carrying them. Settings files are not inert data: they can define
            # hooks, which are shell commands, and skills, which are model-invocable
            # bundled scripts. Combined with bypassPermissions above, that made any
            # `.claude/settings.json` appearing in a workspace arbitrary code execution
            # on the next invocation, and gave one order a way to reach the next one.
            #
            # Measured 2026-09-10 rather than read off --help: a workspace holding a
            # SessionStart hook that wrote a marker file had that hook run on a default
            # invocation, and not run once this flag was passed. The same test, with
            # CLAUDE_CONFIG_DIR pointed at a throwaway home, showed the empty value also
            # excludes the *user* scope -- so nothing an operator installs into their own
            # ~/.claude (skills especially) can reach an unattended order. Authentication
            # is resolved separately and survives this; --model and --tools are already
            # passed explicitly above, and user scope holds no pipeline setting.
            #
            # This flag covers CLAUDE.md as well, measured the same way and controlled for
            # tool use: with tools disallowed, a workspace CLAUDE.md naming a codename was
            # answered back on default sources and not with sources pinned, in one turn
            # each, and the same held under the bypass flags above. An earlier reading of
            # this said the opposite; that run had left tools enabled, and the model had
            # simply opened the file. Nothing else guards this directory, so the flag is
            # the whole control -- do not drop it on the assumption that something
            # downstream also cleans up.
            "--setting-sources", "",
        ]
        if model:
            # Studio's stored model ids carry a "claude/" catalog prefix (e.g. "claude/opus");
            # the CLI's --model flag wants the bare alias ("opus", "sonnet", "fable", ...).
            cmd.extend(["--model", model.split("/", 1)[-1]])

        limit = timeout or CLAUDE_CODE_TASK_TIMEOUT
        elapsed = 0.0
        for attempt in range(2):
            started = time.monotonic()
            try:
                outcome = self._invoke(
                    cmd,
                    prompt,
                    workspace_path,
                    cancellation,
                    attempt=attempt,
                    timeout=limit,
                    on_stdout=lambda line: emit_coding(event_sink, line, agent="Claude Code"),
                )
            except OSError as exc:
                return OpenCodeExecutionResult(success=False, summary=f"Failed to start Claude Code CLI: {exc}", elapsed_seconds=time.monotonic() - started, timeout_seconds=limit)
            elapsed = time.monotonic() - started
            if outcome.stopped_processes:
                # In the stream rather than a log file: a build that left servers running is
                # a fact about that run, and the last time it happened it cost an hour of
                # nobody understanding why the next run would not start.
                event_sink.emit(
                    stage="implementation",
                    agent="Claude Code",
                    progress=50,
                    message=f"Stopped {len(outcome.stopped_processes)} process(es) the call left running in the workspace.",
                    level=EventLevel.WARNING,
                    details=("\n".join(outcome.stopped_processes)[:2000],),
                )
            if outcome.cancelled:
                return OpenCodeExecutionResult(success=False, summary="Claude Code execution was cancelled.", elapsed_seconds=elapsed, timeout_seconds=limit)
            if outcome.timed_out:
                # Timed-out calls are the ones the budget question is *about*, so they have to
                # enter the record in the same shape as the rest. Emitting only on the return
                # path below meant every call killed by the ceiling vanished from the sample:
                # five completed repairs looked like a comfortable p90 of 72% while three
                # others that same week had been killed at 100% and left no trace. A
                # distribution whose upper tail is deleted by the very limit under review
                # cannot answer whether that limit is too low.
                event_sink.emit(
                    stage="implementation",
                    agent="Claude Code",
                    progress=50,
                    message=f"Claude Code CLI returned after {elapsed:.0f}s of its {limit}s budget (100% -- stopped at the ceiling)",
                    level=EventLevel.WARNING,
                )
                # The limit is named in the summary because it is no longer a single global
                # value: a caller reading "timed out" cannot otherwise tell whether it had
                # the full build budget or a repair's shorter one.
                return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution timed out after {limit}s.", errors=("claude_code_execution_timeout",), timed_out=True, elapsed_seconds=elapsed, timeout_seconds=limit)
            if outcome.returncode != 0 and not outcome.stdout_text.strip() and not outcome.stderr_text.strip() and attempt == 0:
                # A nonzero exit with *zero* output on both streams doesn't look like a real
                # coding failure -- a genuine model-level failure almost always produces
                # JSON with is_error/a reasoning result, or at least some stderr text. This
                # matches a CLI-wrapper-level crash observed live: the actual work (file
                # writes via tool calls) had already completed successfully, but the process
                # itself then exited 1 with nothing captured, and a manual retry of the exact
                # same prompt succeeded outright. One retry recovers from that transient
                # wrapper crash without masking a real failure (which would produce output).
                event_sink.emit(stage="implementation", agent="Claude Code", progress=50, message="Claude Code CLI exited with no output; retrying once.", level=EventLevel.WARNING)
                continue
            stdout_text, stderr_text, returncode = outcome.stdout_text, outcome.stderr_text, outcome.returncode
            break

        # Elapsed against the budget, on every call rather than only on the ones that die of
        # it. A timeout and a silent crash write the same log signature, so the only way to
        # tell them apart afterwards was to compare timestamps by hand; and a distribution
        # whose p90 sits just under the ceiling means the ceiling is producing failures,
        # which is invisible while only the failures are timed.
        event_sink.emit(
            stage="implementation",
            agent="Claude Code",
            progress=50,
            message=f"Claude Code CLI returned after {elapsed:.0f}s of its {limit}s budget ({elapsed / limit:.0%})",
        )

        # Parse JSON before branching on returncode: a nonzero exit (e.g. a provider
        # rate limit, api_error_status 429) still comes with a full JSON payload on
        # stdout carrying usage/cost figures and the human-readable rate-limit reset
        # message -- both worth keeping even though the call itself failed.
        try:
            payload: dict | None = json.loads(stdout_text)
        except ValueError:
            payload = None
        usage = _extract_usage(payload)
        rate_limit_message = _extract_rate_limit_message(payload)

        if returncode != 0:
            failure_text = (payload.get("result") if isinstance(payload, dict) else None) or (stderr_text or stdout_text)[-2000:]
            # An expired CLI login is not a coding failure and no amount of retrying or
            # repairing fixes it -- it needs a human to run `claude` and sign in again. It
            # arrives as a normal-looking nonzero exit with a full JSON payload, so without
            # naming it here it reads as "the model declined", which sends the next person
            # looking at prompts instead of at their session.
            status = payload.get("api_error_status") if isinstance(payload, dict) else None
            if status == 401:
                return OpenCodeExecutionResult(
                    success=False,
                    summary="The Claude Code CLI is no longer signed in: its OAuth token has expired. Run `claude` and re-authenticate, then retry this execution.",
                    errors=("claude_code_auth_expired",),
                    usage=usage,
                    rate_limit_message=rate_limit_message,
                    elapsed_seconds=elapsed, timeout_seconds=limit,
                )
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {failure_text}", errors=("claude_code_process_failed",), usage=usage, rate_limit_message=rate_limit_message, elapsed_seconds=elapsed, timeout_seconds=limit)

        if payload is None:
            # Not JSON -- the CLI still wrote files directly to the workspace on the way
            # here, so treat a clean (zero-exit) completion as generated regardless.
            return OpenCodeExecutionResult(success=True, summary=stdout_text[-2000:] or "Claude Code completed.", outcome="generated", elapsed_seconds=elapsed, timeout_seconds=limit)

        if payload.get("is_error"):
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {payload.get('result') or stdout_text[-2000:]}", errors=("claude_code_process_failed",), usage=usage, rate_limit_message=rate_limit_message, elapsed_seconds=elapsed, timeout_seconds=limit)

        return OpenCodeExecutionResult(success=True, summary=str(payload.get("result") or "Claude Code completed."), outcome="generated", usage=usage, elapsed_seconds=elapsed, timeout_seconds=limit)

    @staticmethod
    def _invoke(cmd: list[str], prompt: str, workspace_path: Path, cancellation: CancellationToken, *, attempt: int = 0, timeout: int = CLAUDE_CODE_TASK_TIMEOUT, on_stdout=None) -> _InvokeOutcome:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(workspace_path)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _drain(stream, sink: list[str], notify=None) -> None:
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
                    if notify is not None and line:
                        notify(line)
            except Exception:
                pass

        def _feed_stdin() -> None:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        stdin_thread = threading.Thread(target=_feed_stdin, daemon=True)
        stdout_thread = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks, on_stdout), daemon=True)
        stderr_thread = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        stdin_thread.start()
        stdout_thread.start()
        stderr_thread.start()

        started = time.time()
        timed_out = False
        cancelled = False
        while proc.poll() is None:
            if cancellation.is_cancelled():
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            if time.time() - started > timeout:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(0.2)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        stdout_text = _strip_ansi("".join(stdout_chunks))
        stderr_text = _strip_ansi("".join(stderr_chunks))

        try:
            # attempt 0 starts a fresh log for this phase call; a retry (attempt 1) appends
            # rather than overwrites, so if attempt 0 also failed its output isn't lost --
            # a previous silent-crash investigation had only the *last* attempt's output to
            # go on and couldn't tell whether the retry had even fired.
            mode = "w" if attempt == 0 else "a"
            with (Path(workspace_path) / "claude_code_raw_output.log").open(mode, encoding="utf-8") as handle:
                handle.write(f"=== Attempt {attempt + 1} ===\nCMD: {cmd}\nEXIT: {proc.returncode}\n\n--- STDOUT ---\n{stdout_text}\n\n--- STDERR ---\n{stderr_text}\n\n")
        except OSError:
            pass

        # Whatever the call raised inside the workspace and did not stop. Three
        # `python -m http.server 8099` processes outlived acceptance run 8 on 2026-08-27,
        # serving a generated project to the local network and sitting on the port the demo
        # backend uses. Done here rather than at the phase boundary because a timed-out or
        # crashed call is the likeliest one to leave something running, and this is the only
        # place that sees all three endings.
        left_running = stop_processes_left_in_workspace(workspace_path)

        return _InvokeOutcome(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            timed_out=timed_out,
            cancelled=cancelled,
            stopped_processes=tuple(left_running),
        )


CODING_BACKENDS = ("ollama", "grok", "claude_code", "opencode_bridge", "openrouter")


def _requested_coding_backend() -> str:
    pinned = os.environ.get("FREELANCERSTUDIO_CODING_BACKEND", "").strip().lower()
    if pinned:
        return pinned
    try:
        from system_settings import SYSTEM_SETTINGS

        return str(SYSTEM_SETTINGS.get("coding_backend") or "").strip().lower()
    except Exception:
        return ""


def _requested_repair_backend() -> str:
    """Empty means "same as the build worker" -- see active_repair_backend()."""
    pinned = os.environ.get("FREELANCERSTUDIO_REPAIR_BACKEND", "").strip().lower()
    if pinned:
        return pinned
    try:
        from system_settings import SYSTEM_SETTINGS

        return str(SYSTEM_SETTINGS.get("repair_backend") or "").strip().lower()
    except Exception:
        return ""


def _resolve_ready_backend(requested: str) -> str:
    """Shared resolution: an explicit pin wins outright, bypassing readiness -- Grok's and
    Claude's readiness checks both only detect a CLI login, not real quota (a real 13-day-old
    stale Claude session limit still blocked every live start when found 2026-09-03; see
    MVP_ACCEPTANCE.md), so trusting auto-detection for either would risk silently retrying a
    subscription that looks ready but is not. Unpinned, the default order is Ollama (local,
    free), then OpenCode, then Claude, then Grok. OpenRouter is last because it spends a paid
    API key.
    """
    from .grok_code_client import ConfiguredGrokExecutionClient
    from .ollama_code_client import ConfiguredOllamaExecutionClient
    from .openrouter_code_client import ConfiguredOpenRouterExecutionClient
    from .service import ConfiguredOpenCodeExecutionClient  # local import: avoids a service<->client import cycle

    if requested in CODING_BACKENDS:
        return requested
    ready = {
        "ollama": ConfiguredOllamaExecutionClient(expand_prompt=False).check_readiness().ready,
        "grok": ConfiguredGrokExecutionClient().check_readiness().ready,
        "opencode_bridge": ConfiguredOpenCodeExecutionClient().check_readiness().ready,
        "claude_code": ConfiguredClaudeCodeExecutionClient().check_readiness().ready,
        "openrouter": ConfiguredOpenRouterExecutionClient().check_readiness().ready,
    }
    for backend in ("ollama", "opencode_bridge", "claude_code", "grok"):
        if ready[backend]:
            return backend
    if ready["openrouter"]:
        return "openrouter"
    return ""


def active_coding_backend() -> str:
    """Which coding worker writes the first draft of each phase.

    A saved Settings choice or FREELANCERSTUDIO_CODING_BACKEND pins the worker when that
    worker is ready. Otherwise the default order is Ollama (local coder), then OpenCode,
    then Claude, then Grok. OpenRouter is last because it spends a paid API key. Claude is
    not preferred automatically: an expired subscription still looks installed and used to
    win over a working local Ollama.
    """
    return _resolve_ready_backend(_requested_coding_backend())


def active_repair_backend() -> str:
    """Which coding worker fixes a QA failure -- independent of who wrote the first draft.

    Empty (the default, nobody has touched Settings' "Who fixes QA failures") means "the same
    worker that wrote the draft" -- active_coding_backend()'s own result -- so every existing
    setup behaves exactly as it did before this setting existed: one worker for the whole
    phase. A pinned value (e.g. Ollama drafts, Claude Code or Grok repairs) resolves through
    the same pin-bypasses-readiness rule active_coding_backend() uses, for the same reason.
    """
    requested = _requested_repair_backend()
    if not requested:
        return active_coding_backend()
    return _resolve_ready_backend(requested)


_SECOND_OPINION_BACKENDS = frozenset({"grok"})


def active_second_opinion_backend() -> str:
    """Which worker, if any, reads the finished project after every gate has already passed
    and writes down what it notices -- report-only, never blocking, never triggering another
    repair (confirmed explicitly with the user 2026-09-07 over the escalate-to-repair and
    block-delivery alternatives).

    Empty (the default) means skip the pass entirely: unlike coding_backend/repair_backend
    there is no "worker who already touched this order" fallback that makes sense here -- an
    empty setting is not a request for auto-detection, it is "nobody has turned this on".
    Only "grok" is meaningful right now; an unrecognised value (a stale pin, a typo) silently
    resolves to skip rather than raising over an advisory extra that was never load-bearing.
    """
    pinned = os.environ.get("FREELANCERSTUDIO_SECOND_OPINION_BACKEND", "").strip().lower()
    if not pinned:
        try:
            from system_settings import SYSTEM_SETTINGS

            pinned = str(SYSTEM_SETTINGS.get("second_opinion_backend") or "").strip().lower()
        except Exception:
            pinned = ""
    return pinned if pinned in _SECOND_OPINION_BACKENDS else ""


def select_coding_execution_client(backend: str | None = None) -> OpenCodeExecutionClient:
    """`backend`, when given, is used as-is (already resolved by the caller, e.g. from
    active_repair_backend()) instead of re-resolving via active_coding_backend() -- so build
    and repair clients share this one dispatch table without a second readiness pass."""
    from .grok_code_client import ConfiguredGrokExecutionClient
    from .ollama_code_client import ConfiguredOllamaExecutionClient
    from .openrouter_code_client import ConfiguredOpenRouterExecutionClient
    from .service import ConfiguredOpenCodeExecutionClient  # local import: avoids a service<->client import cycle

    resolved = backend if backend is not None else active_coding_backend()
    if resolved == "ollama":
        return ConfiguredOllamaExecutionClient()
    if resolved == "grok":
        return ConfiguredGrokExecutionClient()
    if resolved == "claude_code":
        return ConfiguredClaudeCodeExecutionClient()
    if resolved == "openrouter":
        return ConfiguredOpenRouterExecutionClient()
    return ConfiguredOpenCodeExecutionClient()
