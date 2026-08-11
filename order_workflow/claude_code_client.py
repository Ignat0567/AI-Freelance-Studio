"""Delegates coding tasks to the official Claude Code CLI -- the Anthropic-subscription
counterpart to ConfiguredOpenCodeExecutionClient (which delegates to OpenCode). Which one
runs is picked by select_coding_execution_client() based on which CLI is actually logged
in right now, never by a stored config flag: Claude Code CLI owns its own `claude auth
login` session and this module never reads or sets ANTHROPIC_API_KEY.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import threading
import time
from pathlib import Path

import claude_bridge

from .executors import CancellationToken, ExecutionEventSink
from .models import EventLevel, TokenUsage
from .production_adapter import OpenCodeExecutionClient, OpenCodeExecutionResult
from .readiness import CLAUDE_CODE_UNAVAILABLE, ReadinessResult

CLAUDE_CODE_TASK_TIMEOUT = 900


@dataclass(frozen=True, slots=True)
class _InvokeOutcome:
    returncode: int
    stdout_text: str
    stderr_text: str
    timed_out: bool = False
    cancelled: bool = False


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
    ) -> OpenCodeExecutionResult:
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Claude Code execution was cancelled before invocation.")
        binary = claude_bridge._discover_claude()
        if not binary:
            return OpenCodeExecutionResult(success=False, summary="Claude Code CLI is not available.")

        _ensure_isolated_git_repo(Path(workspace_path))
        event_sink.emit(stage="implementation", agent="Claude Code", progress=50, message="Invoking Claude Code CLI")
        # No --bare: it forces standard (prompting) permission behavior, which blocks
        # every file write in this non-interactive context with no TTY to answer the
        # prompt. _ensure_isolated_git_repo() above already stops `git diff`/`git status`
        # (which --bare would otherwise be needed to guard against) from walking up to
        # Studio's own repo, so --bare's CLAUDE.md/auto-memory isolation is redundant here.
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
        ]
        if model:
            # Studio's stored model ids carry a "claude/" catalog prefix (e.g. "claude/opus");
            # the CLI's --model flag wants the bare alias ("opus", "sonnet", "fable", ...).
            cmd.extend(["--model", model.split("/", 1)[-1]])

        for attempt in range(2):
            try:
                outcome = self._invoke(cmd, prompt, workspace_path, cancellation, attempt=attempt)
            except OSError as exc:
                return OpenCodeExecutionResult(success=False, summary=f"Failed to start Claude Code CLI: {exc}")
            if outcome.cancelled:
                return OpenCodeExecutionResult(success=False, summary="Claude Code execution was cancelled.")
            if outcome.timed_out:
                return OpenCodeExecutionResult(success=False, summary="Claude Code execution timed out.", errors=("claude_code_execution_timeout",), timed_out=True)
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
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {failure_text}", errors=("claude_code_process_failed",), usage=usage, rate_limit_message=rate_limit_message)

        if payload is None:
            # Not JSON -- the CLI still wrote files directly to the workspace on the way
            # here, so treat a clean (zero-exit) completion as generated regardless.
            return OpenCodeExecutionResult(success=True, summary=stdout_text[-2000:] or "Claude Code completed.", outcome="generated")

        if payload.get("is_error"):
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {payload.get('result') or stdout_text[-2000:]}", errors=("claude_code_process_failed",), usage=usage, rate_limit_message=rate_limit_message)

        return OpenCodeExecutionResult(success=True, summary=str(payload.get("result") or "Claude Code completed."), outcome="generated", usage=usage)

    @staticmethod
    def _invoke(cmd: list[str], prompt: str, workspace_path: Path, cancellation: CancellationToken, *, attempt: int = 0) -> _InvokeOutcome:
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

        def _drain(stream, sink: list[str]) -> None:
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
            except Exception:
                pass

        def _feed_stdin() -> None:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        stdin_thread = threading.Thread(target=_feed_stdin, daemon=True)
        stdout_thread = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
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
            if time.time() - started > CLAUDE_CODE_TASK_TIMEOUT:
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

        return _InvokeOutcome(returncode=proc.returncode if proc.returncode is not None else -1, stdout_text=stdout_text, stderr_text=stderr_text, timed_out=timed_out, cancelled=cancelled)


def active_coding_backend() -> str:
    """Single source of truth for which coding CLI's own subscription is actually logged
    in right now: 'claude_code', 'opencode_bridge', or '' if neither is ready. Deliberately
    reads live CLI auth state instead of any of Studio's stored provider config, since a
    stored flag can go stale (a subscription can lapse, or the config file a script wrote
    to isn't even the one the running app reads) without Studio knowing. Claude Code is
    preferred when both happen to be ready; there is no product requirement yet for a
    user-facing priority choice between two simultaneously-active subscriptions.
    """
    if ConfiguredClaudeCodeExecutionClient().check_readiness().ready:
        return "claude_code"
    from .service import ConfiguredOpenCodeExecutionClient  # local import: avoids a service<->client import cycle

    if ConfiguredOpenCodeExecutionClient().check_readiness().ready:
        return "opencode_bridge"
    return ""


def select_coding_execution_client() -> OpenCodeExecutionClient:
    from .service import ConfiguredOpenCodeExecutionClient  # local import: avoids a service<->client import cycle

    if active_coding_backend() == "claude_code":
        return ConfiguredClaudeCodeExecutionClient()
    return ConfiguredOpenCodeExecutionClient()
