"""Grok CLI writes project files directly, instead of authoring a spec for Ollama.

Same emit-and-write contract as the local coder: Grok prints <<<FILE ... FILE>>>
markers, Studio writes them. Tools stay off -- grok_bridge already runs the CLI in
plan mode -- so a subscription login is enough and Studio never holds an xAI key.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import grok_bridge

from .coding_prompt import is_one_file_static_page_task
from .executors import CancellationToken, ExecutionEventSink, emit_coding
from .models import EventLevel
from .ollama_code_client import coder_preamble_for_spec, parse_emitted_files, write_emitted_files
from .production_adapter import OpenCodeExecutionResult
from .readiness import GROK_CLI_UNAVAILABLE, ReadinessResult
from .workspace_processes import stop_processes_left_in_workspace

GROK_TASK_TIMEOUT = 900
_CLAUDE_MODEL_ALIASES = {
    "sonnet",
    "opus",
    "haiku",
    "fable",
    "claude/default",
    "claude/sonnet",
    "claude/opus",
}


def _grok_model_id(model: str | None) -> str:
    raw = (model or "").strip()
    if not raw or raw.casefold() in _CLAUDE_MODEL_ALIASES or raw.casefold().startswith("claude/"):
        return grok_bridge.DEFAULT_GROK_MODEL
    return raw


_HTML_FENCE = re.compile(r"```(?:html|HTML)?\s*\r?\n(?P<body>.*?)```", re.DOTALL)
_EXISTING_INDEX_CAP = 120_000
_ONE_FILE_SPEC = "Build ONE self-contained file, index.html, at the project root."
_REPAIR_MARKER = "a check that has to pass before this project can be delivered is failing"


def _is_repair_prompt(spec: str) -> bool:
    return _REPAIR_MARKER in (spec or "").casefold()


def _existing_index_html(workspace: Path) -> str:
    """Put the page Grok is repairing in the prompt. Tools are off, so the file has
    to travel with the QA findings. A full rebuild must not carry the broken page:
    the 2026-09-02 retry stuffed 64KB of dead HTML into the brief and Grok exited
    after 'I'll read the full prompt first'.
    """
    path = workspace / "index.html"
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > _EXISTING_INDEX_CAP:
        text = text[:_EXISTING_INDEX_CAP] + "\n<!-- truncated for the repair prompt -->\n"
    return (
        "The workspace already contains this file. Rewrite the whole file with FILE markers. "
        "Do not explore the directory. Do not invent another path.\n\n"
        f"{text}"
    )


def _user_prompt_for_workspace(spec: str, workspace: Path) -> str:
    existing = _existing_index_html(workspace) if _is_repair_prompt(spec) else ""
    preamble_spec = _ONE_FILE_SPEC if existing or is_one_file_static_page_task(spec) else spec
    parts = [coder_preamble_for_spec(preamble_spec)]
    if existing:
        parts.append(existing)
    parts.append(spec)
    return "\n\n".join(parts)


def _html_from_loose_output(text: str) -> str:
    """Accept a full HTML document when Grok ignores the FILE marker format."""
    fenced = _HTML_FENCE.search(text or "")
    if fenced:
        body = fenced.group("body").strip()
        if "<html" in body.casefold() or "<!doctype" in body.casefold():
            return body if body.endswith("\n") else body + "\n"
    lowered = (text or "").casefold()
    start = lowered.find("<!doctype html")
    if start < 0:
        start = lowered.find("<html")
    if start < 0:
        return ""
    snippet = text[start:]
    end = snippet.casefold().rfind("</html>")
    if end >= 0:
        snippet = snippet[: end + len("</html>")]
    snippet = snippet.strip()
    return snippet + "\n" if snippet else ""


class ConfiguredGrokExecutionClient:
    def check_readiness(self) -> ReadinessResult:
        status = grok_bridge.test_grok_readiness()
        if status.get("ready"):
            return ReadinessResult.ready_result()
        return ReadinessResult.blocked(GROK_CLI_UNAVAILABLE)

    def execute_project_prompt(
        self,
        prompt: str,
        workspace_path: Path,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        model: str | None = None,
        timeout: int | None = None,
    ) -> OpenCodeExecutionResult:
        started = time.monotonic()
        limit = timeout or GROK_TASK_TIMEOUT
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Grok execution was cancelled before invocation.")
        if not self.check_readiness().ready:
            return OpenCodeExecutionResult(
                success=False,
                summary="Grok CLI is not installed or not logged in.",
                errors=("grok_cli_unavailable",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )

        event_sink.emit(
            stage="implementation",
            agent="Grok",
            progress=40,
            message="Grok is writing project files.",
        )
        spec = prompt or ""
        user = _user_prompt_for_workspace(spec, workspace)
        one_file = bool(_existing_index_html(workspace)) or is_one_file_static_page_task(spec)
        system = (
            "You write complete project files for AI Freelance Studio. "
            "The first line of the reply must be <<<FILE index.html. "
            "No preamble, no plan, no 'I'll read the prompt'. "
            "Emit only FILE markers. Do not write a spec for another model. "
            "Do not emit a rotating cube when the brief asked for a living scene."
            if one_file
            else "You write complete project files for AI Freelance Studio. The first line of the reply must be a <<<FILE marker. No preamble. Emit only FILE markers. Do not write a spec for another model."
        )
        try:
            output = grok_bridge.ask_grok_cli(
                system,
                user,
                model=_grok_model_id(model),
                timeout=limit,
                on_chunk=lambda piece: emit_coding(event_sink, piece, agent="Grok"),
                cancel_check=cancellation.is_cancelled,
                working_directory=str(workspace),
            )
        except Exception as exc:
            return OpenCodeExecutionResult(
                success=False,
                summary=f"Grok request failed: {exc}",
                errors=("grok_execution_failed",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )

        elapsed = time.monotonic() - started
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Grok execution was cancelled.", elapsed_seconds=elapsed, timeout_seconds=limit)
        if (output or "").casefold().startswith("grok cli "):
            event_sink.emit(stage="implementation", agent="Grok", progress=45, message=output[:240], level=EventLevel.ERROR)
            return OpenCodeExecutionResult(
                success=False,
                summary=output[:400],
                errors=("grok_execution_failed",),
                elapsed_seconds=elapsed,
                timeout_seconds=limit,
            )

        try:
            (workspace / "grok_raw_output.log").write_text(output or "", encoding="utf-8")
        except OSError:
            pass
        files = parse_emitted_files(output)
        if not files:
            recovered = _html_from_loose_output(output or "")
            if recovered:
                files = [("index.html", recovered)]
        written = write_emitted_files(workspace, files)
        left_running = stop_processes_left_in_workspace(workspace)
        if left_running:
            event_sink.emit(stage="implementation", agent="Grok", progress=70, message=f"Stopped leftover process(es): {', '.join(left_running)}", level=EventLevel.WARNING)
        if not written:
            event_sink.emit(stage="implementation", agent="Grok", progress=70, message="Grok returned no writable files.", level=EventLevel.ERROR)
            return OpenCodeExecutionResult(
                success=False,
                summary="Grok returned no writable files.",
                errors=("grok_empty_output",),
                elapsed_seconds=elapsed,
                timeout_seconds=limit,
            )
        event_sink.emit(stage="implementation", agent="Grok", progress=70, message=f"Grok wrote {len(written)} file(s).")
        return OpenCodeExecutionResult(
            success=True,
            summary=f"Grok wrote {len(written)} file(s).",
            outcome="generated",
            meaningful_artifacts=written,
            elapsed_seconds=elapsed,
            timeout_seconds=limit,
        )


_SECOND_OPINION_TIMEOUT = 240
_SECOND_OPINION_CONTENT_CAP = 60_000
_SECOND_OPINION_SYSTEM = (
    "You are reviewing a project that has already been delivered to a client. Every "
    "automated QA gate it had to pass -- build, tests, functional smoke check, visual "
    "check -- has already passed. Nothing you say will change the delivery or trigger any "
    "repair; this is a second, independent pass, purely advisory. Read the files below and "
    "write 3 to 6 concrete, specific findings as plain bullet points: accessibility gaps, "
    "missing error handling, unhandled edge cases, or real code-quality problems. Name the "
    "file and, where useful, the exact element or function. Do not ask a question, do not "
    "propose a plan, do not emit file markers or code changes -- only the bullet list. If you "
    "genuinely find nothing worth flagging, say so in one sentence instead of inventing filler."
)


def _second_opinion_looks_like_error(text: str) -> bool:
    return (text or "").casefold().startswith("grok cli ")


def build_second_opinion(
    workspace_path: Path,
    *,
    goal: str,
    files: tuple[str, ...],
    timeout: int = _SECOND_OPINION_TIMEOUT,
) -> str | None:
    """A report-only extra read of an already-delivered, already-QA-passed project.

    Every failure mode here -- Grok not ready, the CLI call itself failing, nothing left to
    show after the size cap -- degrades to None rather than raising: _finalize_success()'s
    delivery must never fail because this advisory pass could not run. `files` is the
    caller's already-curated, noise-filtered list (scan_meaningful_generated_artifacts()'s
    output, passed in rather than recomputed) so this function makes no filesystem-shape
    decisions of its own about what counts as source.
    """
    if not grok_bridge.test_grok_readiness().get("ready"):
        return None

    workspace = Path(workspace_path)
    blocks: list[str] = []
    total = 0
    for relative in files:
        path = workspace / relative
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not text.strip():
            continue
        block = f"--- {relative} ---\n{text}"
        if total + len(block) > _SECOND_OPINION_CONTENT_CAP:
            blocks.append(f"-- remaining files omitted, over the {_SECOND_OPINION_CONTENT_CAP}-char review budget --")
            break
        blocks.append(block)
        total += len(block)
    if not blocks:
        return None

    user_prompt = f"Project goal: {goal}\n\n" + "\n\n".join(blocks)
    try:
        output = grok_bridge.ask_grok_cli(
            _SECOND_OPINION_SYSTEM,
            user_prompt,
            model=grok_bridge.DEFAULT_GROK_MODEL,
            timeout=timeout,
        )
    except Exception:
        return None
    output = (output or "").strip()
    if not output or _second_opinion_looks_like_error(output):
        return None
    return output
