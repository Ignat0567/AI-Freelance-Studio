"""Delegates coding tasks to the official Claude Code CLI -- the Anthropic-subscription
counterpart to ConfiguredOpenCodeExecutionClient (which delegates to OpenCode). Which one
runs is picked by select_coding_execution_client() based on which CLI is actually logged
in right now, never by a stored config flag: Claude Code CLI owns its own `claude auth
login` session and this module never reads or sets ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

import claude_bridge

from .executors import CancellationToken, ExecutionEventSink
from .production_adapter import OpenCodeExecutionClient, OpenCodeExecutionResult
from .readiness import CLAUDE_CODE_UNAVAILABLE, ReadinessResult

CLAUDE_CODE_TASK_TIMEOUT = 900


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


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
        cmd = [
            binary, "-p", prompt,
            "--output-format", "json",
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--permission-mode", "bypassPermissions",
            "--tools", "default",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(Path(workspace_path)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return OpenCodeExecutionResult(success=False, summary=f"Failed to start Claude Code CLI: {exc}")

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _drain(stream, sink: list[str]) -> None:
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        started = time.time()
        timed_out = False
        while proc.poll() is None:
            if cancellation.is_cancelled():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return OpenCodeExecutionResult(success=False, summary="Claude Code execution was cancelled.")
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
            (Path(workspace_path) / "claude_code_raw_output.log").write_text(
                f"CMD: {cmd}\nEXIT: {proc.returncode}\n\n--- STDOUT ---\n{stdout_text}\n\n--- STDERR ---\n{stderr_text}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        if timed_out:
            return OpenCodeExecutionResult(success=False, summary="Claude Code execution timed out.", errors=("claude_code_execution_timeout",), timed_out=True)

        if proc.returncode != 0:
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {(stderr_text or stdout_text)[-2000:]}", errors=("claude_code_process_failed",))

        try:
            payload = json.loads(stdout_text)
        except ValueError:
            # Not JSON -- the CLI still wrote files directly to the workspace on the way
            # here, so treat a clean (zero-exit) completion as generated regardless.
            return OpenCodeExecutionResult(success=True, summary=stdout_text[-2000:] or "Claude Code completed.", outcome="generated")

        if payload.get("is_error"):
            return OpenCodeExecutionResult(success=False, summary=f"Claude Code execution failed: {payload.get('result') or stdout_text[-2000:]}", errors=("claude_code_process_failed",))

        return OpenCodeExecutionResult(success=True, summary=str(payload.get("result") or "Claude Code completed."), outcome="generated")


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
