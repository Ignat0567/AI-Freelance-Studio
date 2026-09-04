"""Local Ollama coding backend: Grok writes the spec, Qwen writes the files.

Ollama has no Claude-style file tools. The contract is therefore:

1. Optionally expand the phase prompt via Grok (coding_prompt.expand_coding_prompt).
2. Ask the local coder to emit complete files in a rigid marker format.
3. Parse those markers and write only paths inside the workspace.

Preferred model on this machine: qwen2.5-coder:14b (instruct, tools, 32k).
Base/FIM-only models such as qwen2.5-coder:1.5b-base are skipped.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .coding_prompt import expand_coding_prompt, is_one_file_static_page_task
from .executors import CancellationToken, ExecutionEventSink, emit_coding
from .models import EventLevel
from .production_adapter import OpenCodeExecutionResult
from .readiness import OLLAMA_UNAVAILABLE, ReadinessResult
from .workspace_processes import stop_processes_left_in_workspace

DEFAULT_OLLAMA_ENDPOINT = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_TASK_TIMEOUT = 1500
OLLAMA_REPAIR_TIMEOUT = 900
# Ollama unloads the model between orders on memory-constrained hosts and reloading it takes
# longer than the request that triggered the load waits for -- observed live 2026-09-03, twice
# in one bench sequence: the first call after a backend restart got HTTP 500 in ~15s, and a
# bare retry a few seconds later (once the daemon had finished loading) succeeded. The failure
# happens on urlopen() itself, before any token is streamed, so nothing has reached on_chunk
# yet and a retry cannot duplicate output.
OLLAMA_COLD_START_RETRY_DELAY_SECONDS = 8.0

CODING_MODEL_PREFERENCE = (
    "qwen2.5-coder:14b",
    "qwen2.5-coder:14b-instruct",
    "qwen2.5-coder:32b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder",
    "codestral",
    "deepseek-coder-v2",
    "deepseek-coder",
    "codellama",
    "dolphin-mistral:7b-v2.8-q4_K_M",
)

_SKIP_MODEL_MARKERS = (":1.5b-base", ":base", "starcoder2:3b")

_FILE_MARKER = re.compile(
    r"<<<FILE\s+(?P<path>[^\r\n]+)\s*\r?\n(?P<body>.*?)\r?\nFILE>>>",
    re.DOTALL,
)
_ALT_FILE_MARKER = re.compile(
    r"^FILE:\s+(?P<path>\S+)\s*\r?\n```(?:[\w.+-]+)?\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL | re.MULTILINE,
)

_CODER_PREAMBLE = """You are a local coding model writing a complete project onto disk.
You cannot use tools. Emit every file using this exact marker format and nothing else:

<<<FILE relative/path/from/project/root
complete file contents
FILE>>>

Rules:
- POSIX paths only (forward slashes). No absolute paths, no '..'.
- Every file must be complete, not a patch and not a snippet.
- Do not skip tests, config, or HTML/CSS the spec asked for.
- Do not wrap the whole response in one Markdown fence.
- Do not invent APIs, fake passing tests, or leave TODO stubs.
- If a file already exists conceptually, overwrite it with the full new contents.
"""

_ONE_FILE_EMIT_RULE = """
This task is a single-file static page. Emit ONLY this one marker and no other paths:

<<<FILE index.html
complete html including inline css and javascript
FILE>>>

Do not emit src/, extra .html, .js, .css, package.json, or any other path.
"""


def coder_preamble_for_spec(spec: str) -> str:
    if is_one_file_static_page_task(spec):
        return _CODER_PREAMBLE + "\n" + _ONE_FILE_EMIT_RULE
    return _CODER_PREAMBLE


def ollama_endpoint() -> str:
    return DEFAULT_OLLAMA_ENDPOINT


def _request_json(url: str, payload: dict | None = None, timeout: int = 10) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_ollama_models(endpoint: str | None = None) -> tuple[str, ...]:
    host = (endpoint or ollama_endpoint()).rstrip("/")
    try:
        payload = _request_json(f"{host}/api/tags", timeout=5)
    except Exception:
        return ()
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ()
    names = []
    for item in models:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
    return tuple(names)


def select_ollama_coding_model(installed: tuple[str, ...] | None = None, requested: str | None = None) -> str:
    names = installed if installed is not None else list_ollama_models()
    if requested and requested in names:
        return requested
    by_lower = {name.casefold(): name for name in names}
    for preferred in CODING_MODEL_PREFERENCE:
        if preferred.casefold() in by_lower:
            candidate = by_lower[preferred.casefold()]
            if not any(marker in candidate.casefold() for marker in _SKIP_MODEL_MARKERS):
                return candidate
        for name in names:
            if name.casefold().startswith(preferred.casefold()) and not any(marker in name.casefold() for marker in _SKIP_MODEL_MARKERS):
                return name
    for name in names:
        if not any(marker in name.casefold() for marker in _SKIP_MODEL_MARKERS):
            return name
    return names[0] if names else ""


def coding_paths_from_partial(text: str) -> tuple[str, ...]:
    """FILE paths visible in a still-streaming response, including an unfinished last file."""
    seen: list[str] = []
    for match in _FILE_MARKER.finditer(text or ""):
        rel = match.group("path").strip().strip("`").replace("\\", "/")
        if rel and rel not in seen:
            seen.append(rel)
    for match in _ALT_FILE_MARKER.finditer(text or ""):
        rel = match.group("path").strip().strip("`").replace("\\", "/")
        if rel and rel not in seen:
            seen.append(rel)
    for match in re.finditer(r"<<<FILE\s+(?P<path>[^\r\n]+)", text or ""):
        rel = match.group("path").strip().strip("`").replace("\\", "/")
        if rel and rel not in seen:
            seen.append(rel)
    kept: list[str] = []
    for rel in seen:
        if any(other != rel and other.startswith(rel) for other in seen):
            continue
        kept.append(rel)
    return tuple(kept)


def parse_emitted_files(text: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in (_FILE_MARKER, _ALT_FILE_MARKER):
        for match in pattern.finditer(text or ""):
            rel = match.group("path").strip().strip("`").replace("\\", "/")
            body = match.group("body")
            if not rel or rel in seen:
                continue
            seen.add(rel)
            files.append((rel, body if body.endswith("\n") else body + "\n"))
    return files


def _safe_workspace_file(workspace: Path, relative: str) -> Path | None:
    cleaned = relative.strip().lstrip("/").replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or ":" in cleaned.split("/")[0]:
        return None
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    target = (workspace / Path(*parts)).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return None
    return target


def write_emitted_files(workspace: Path, files: list[tuple[str, str]]) -> tuple[str, ...]:
    written: list[str] = []
    for relative, body in files:
        path = _safe_workspace_file(workspace, relative)
        if path is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        except OSError:
            continue
        written.append(relative.replace("\\", "/"))
    return tuple(written)


def generate_ollama_completion(
    prompt: str,
    *,
    model: str,
    endpoint: str | None = None,
    timeout: int = OLLAMA_TASK_TIMEOUT,
    cancellation: CancellationToken | None = None,
    on_chunk: Callable[[str], None] | None = None,
    # Injected so a test exercising the retry does not spend the real delay waiting --
    # same pattern as preflight.py's probe_coding_cli_credentials.
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    host = (endpoint or ollama_endpoint()).rstrip("/")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.15, "num_predict": 16384, "num_ctx": 32768},
    }
    request = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 500:
            raise
        sleeper(OLLAMA_COLD_START_RETRY_DELAY_SECONDS)
        response = urllib.request.urlopen(request, timeout=timeout)
    chunks: list[str] = []
    with response:
        for raw in response:
            if cancellation is not None and cancellation.is_cancelled():
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("response"):
                piece = str(event["response"])
                chunks.append(piece)
                if on_chunk is not None and piece:
                    on_chunk(piece)
            if event.get("done"):
                break
    return "".join(chunks)


class ConfiguredOllamaExecutionClient:
    def __init__(self, endpoint: str | None = None, expand_prompt: bool = True) -> None:
        self._endpoint = (endpoint or ollama_endpoint()).rstrip("/")
        self._expand_prompt = expand_prompt

    def check_readiness(self) -> ReadinessResult:
        model = select_ollama_coding_model(list_ollama_models(self._endpoint))
        if not model:
            return ReadinessResult.blocked(OLLAMA_UNAVAILABLE)
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
        started = time.monotonic()
        limit = timeout or OLLAMA_TASK_TIMEOUT
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        installed = list_ollama_models(self._endpoint)
        claude_aliases = {"sonnet", "opus", "haiku", "fable", "claude/default", "claude/sonnet", "claude/opus"}
        requested = model if model and model not in claude_aliases else None
        selected = select_ollama_coding_model(installed, requested=requested)
        if not selected:
            return OpenCodeExecutionResult(
                success=False,
                summary="Ollama is not reachable or has no coding model installed. Start Ollama and pull qwen2.5-coder:14b.",
                errors=("ollama_unavailable",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Ollama execution was cancelled before invocation.")

        spec = prompt
        if self._expand_prompt:
            event_sink.emit(stage="implementation", agent="Grok", progress=20, message="Grok is writing a file-by-file coding spec for the local model.")
            spec = expand_coding_prompt(prompt)
            if spec != prompt:
                event_sink.emit(stage="implementation", agent="Grok", progress=30, message="Grok coding spec is ready; handing it to Ollama.")
            else:
                event_sink.emit(
                    stage="implementation",
                    agent="Grok",
                    progress=30,
                    message="Grok spec unavailable; the original phase prompt will be sent to Ollama.",
                    level=EventLevel.WARNING,
                )

        event_sink.emit(
            stage="implementation",
            agent="Ollama",
            progress=40,
            message=f"Local coder {selected} is writing project files.",
        )
        emit_coding(event_sink, f"Local coder {selected} is writing project files...\n", agent="Ollama")
        full_prompt = f"{coder_preamble_for_spec(spec)}\n\n{spec}"
        try:
            output = generate_ollama_completion(
                full_prompt,
                model=selected,
                endpoint=self._endpoint,
                timeout=limit,
                cancellation=cancellation,
                on_chunk=lambda piece: emit_coding(event_sink, piece, agent="Ollama"),
            )
        except urllib.error.URLError as exc:
            return OpenCodeExecutionResult(
                success=False,
                summary=f"Ollama request failed: {exc}",
                errors=("ollama_execution_failed",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )
        except TimeoutError:
            return OpenCodeExecutionResult(
                success=False,
                summary=f"Ollama execution timed out after {limit}s.",
                errors=("ollama_execution_timeout",),
                timed_out=True,
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )

        elapsed = time.monotonic() - started
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="Ollama execution was cancelled.", elapsed_seconds=elapsed, timeout_seconds=limit)

        files = parse_emitted_files(output)
        written = write_emitted_files(workspace, files)
        left_running = stop_processes_left_in_workspace(workspace)
        if left_running:
            event_sink.emit(
                stage="implementation",
                agent="Ollama",
                progress=50,
                message=f"Stopped {len(left_running)} process(es) the call left running in the workspace.",
                level=EventLevel.WARNING,
                details=("\n".join(left_running)[:2000],),
            )
        if not written:
            log_path = workspace / "ollama_raw_output.log"
            log_path.write_text(output[-20000:], encoding="utf-8")
            return OpenCodeExecutionResult(
                success=False,
                summary="Ollama returned no parseable files. The local coder must emit <<<FILE path>>> markers.",
                errors=("ollama_no_files",),
                elapsed_seconds=elapsed,
                timeout_seconds=limit,
            )
        event_sink.emit(
            stage="implementation",
            agent="Ollama",
            progress=70,
            message=f"Ollama wrote {len(written)} file(s) after {elapsed:.0f}s of its {limit}s budget.",
        )
        return OpenCodeExecutionResult(
            success=True,
            summary=f"Ollama ({selected}) wrote {len(written)} files.",
            meaningful_artifacts=written,
            elapsed_seconds=elapsed,
            timeout_seconds=limit,
        )
