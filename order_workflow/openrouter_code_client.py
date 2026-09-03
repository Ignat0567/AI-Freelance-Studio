"""OpenRouter coding backend: the aggregator writes project files via FILE markers.

Same emit-and-write contract as the local coder and Grok. Studio streams
OpenAI-compatible chat completions at https://openrouter.ai/api/v1, parses
<<<FILE ... FILE>>> markers, and writes only paths inside the workspace.

The API key comes from secret_store (OPENROUTER_API_KEY) or the saved
openrouter-api-key connection in the credential store. Studio never prints it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import config_storage
import secret_store

from .coding_prompt import is_one_file_static_page_task
from .executors import CancellationToken, ExecutionEventSink, emit_coding
from .models import EventLevel
from .ollama_code_client import coder_preamble_for_spec, parse_emitted_files, write_emitted_files
from .production_adapter import OpenCodeExecutionResult
from .readiness import OPENROUTER_UNAVAILABLE, ReadinessResult
from .workspace_processes import stop_processes_left_in_workspace

OPENROUTER_CHAT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_TASK_TIMEOUT = 1500

_STUDIO_INTERNAL_MODELS = {
    "sonnet",
    "opus",
    "haiku",
    "fable",
    "claude/default",
    "claude/sonnet",
    "claude/opus",
    "grok-4.6",
    "grok/grok-4.6",
    "grok-4.5",
    "grok/grok-4.5",
}

_HTML_FENCE = re.compile(r"```(?:html|HTML)?\s*\r?\n(?P<body>.*?)```", re.DOTALL)


def _openrouter_model_id(model: str | None) -> str:
    raw = (model or "").strip()
    if raw.lower().startswith("openrouter/"):
        raw = raw[len("openrouter/") :]
    if not raw:
        return DEFAULT_OPENROUTER_MODEL
    folded = raw.casefold()
    if folded in _STUDIO_INTERNAL_MODELS or folded.startswith("claude/") or ":" in raw:
        return DEFAULT_OPENROUTER_MODEL
    if "/" in raw:
        return raw
    return DEFAULT_OPENROUTER_MODEL


def resolve_openrouter_api_key(
    *,
    config: dict | None = None,
    credential_store=None,
) -> str:
    try:
        loaded = config if config is not None else config_storage.load_studio_keys()
    except Exception:
        loaded = {}
    env_key = secret_store.get_secret("openrouter_key", loaded if isinstance(loaded, dict) else None)
    if env_key:
        return env_key
    connections = loaded.get("_provider_connections") if isinstance(loaded, dict) else None
    if not isinstance(connections, list):
        return ""
    try:
        from provider_credentials import ProviderCredentialStore, default_backend

        store = credential_store or ProviderCredentialStore(default_backend())
    except Exception:
        return ""
    for item in connections:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider_id") or "").strip().lower()
        connection_type = str(item.get("connection_type") or "").strip().lower()
        if provider != "openrouter" and connection_type != "openrouter_api_key":
            continue
        ref = str(item.get("credential_reference") or "").strip()
        if not ref:
            continue
        try:
            value = store.read_api_key(ref).strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _html_from_loose_output(text: str) -> str:
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


def _sanitize_provider_error(detail: str) -> str:
    text = (detail or "").replace("\n", " ").strip()
    if len(text) > 240:
        text = text[:240]
    return text


def generate_openrouter_completion(
    prompt: str,
    *,
    model: str,
    api_key: str,
    timeout: int = OPENROUTER_TASK_TIMEOUT,
    cancellation: CancellationToken | None = None,
    endpoint: str | None = None,
    system: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    host = (endpoint or OPENROUTER_CHAT_BASE).rstrip("/")
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0.15,
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": system or "You write complete project files. Emit only FILE markers."},
            {"role": "user", "content": prompt},
        ],
    }
    request = urllib.request.Request(
        f"{host}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    chunks: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                if cancellation is not None and cancellation.is_cancelled():
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                error = event.get("error")
                if isinstance(error, dict) and error.get("message"):
                    raise RuntimeError(_sanitize_provider_error(str(error.get("message"))))
                choice = (event.get("choices") or [{}])[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                piece = delta.get("content") if isinstance(delta, dict) else None
                if piece:
                    text = str(piece)
                    chunks.append(text)
                    if on_chunk is not None:
                        on_chunk(text)
                message = choice.get("message") or {}
                if isinstance(message, dict) and message.get("content") and not delta:
                    text = str(message["content"])
                    chunks.append(text)
                    if on_chunk is not None:
                        on_chunk(text)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {_sanitize_provider_error(body or exc.reason)}") from exc
    except TimeoutError as exc:
        raise TimeoutError(f"OpenRouter execution timed out after {timeout}s.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).casefold():
            raise TimeoutError(f"OpenRouter execution timed out after {timeout}s.") from exc
        raise RuntimeError(f"OpenRouter request failed: {_sanitize_provider_error(str(reason))}") from exc
    return "".join(chunks)


class ConfiguredOpenRouterExecutionClient:
    def __init__(
        self,
        *,
        api_key_lookup: Callable[[], str] | None = None,
        completer: Callable[..., str] | None = None,
        endpoint: str | None = None,
    ) -> None:
        self._api_key_lookup = api_key_lookup or resolve_openrouter_api_key
        self._completer = completer or generate_openrouter_completion
        self._endpoint = (endpoint or OPENROUTER_CHAT_BASE).rstrip("/")

    def check_readiness(self) -> ReadinessResult:
        try:
            present = bool(self._api_key_lookup())
        except Exception:
            present = False
        if not present:
            return ReadinessResult.blocked(OPENROUTER_UNAVAILABLE)
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
        limit = timeout or OPENROUTER_TASK_TIMEOUT
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="OpenRouter execution was cancelled before invocation.")
        api_key = self._api_key_lookup()
        if not api_key:
            return OpenCodeExecutionResult(
                success=False,
                summary="OpenRouter API key is not configured.",
                errors=("openrouter_unavailable",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )

        selected = _openrouter_model_id(model)
        event_sink.emit(
            stage="implementation",
            agent="OpenRouter",
            progress=40,
            message="OpenRouter is writing project files.",
        )
        emit_coding(event_sink, "OpenRouter is writing project files...\n", agent="OpenRouter")
        spec = prompt or ""
        user = f"{coder_preamble_for_spec(spec)}\n\n{spec}"
        system = (
            "You write complete project files for AI Freelance Studio. "
            "Emit only FILE markers. Do not write a spec for another model. "
            "Do not emit a rotating cube when the brief asked for a living scene."
            if is_one_file_static_page_task(spec)
            else "You write complete project files for AI Freelance Studio. Emit only FILE markers. Do not write a spec for another model."
        )
        try:
            output = self._completer(
                user,
                model=selected,
                api_key=api_key,
                timeout=limit,
                cancellation=cancellation,
                endpoint=self._endpoint,
                system=system,
                on_chunk=lambda piece: emit_coding(event_sink, piece, agent="OpenRouter"),
            )
        except TimeoutError:
            return OpenCodeExecutionResult(
                success=False,
                summary=f"OpenRouter execution timed out after {limit}s.",
                errors=("openrouter_execution_timeout",),
                timed_out=True,
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )
        except Exception as exc:
            return OpenCodeExecutionResult(
                success=False,
                summary=f"OpenRouter request failed: {exc}",
                errors=("openrouter_execution_failed",),
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=limit,
            )

        elapsed = time.monotonic() - started
        if cancellation.is_cancelled():
            return OpenCodeExecutionResult(success=False, summary="OpenRouter execution was cancelled.", elapsed_seconds=elapsed, timeout_seconds=limit)

        try:
            (workspace / "openrouter_raw_output.log").write_text(output or "", encoding="utf-8")
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
            event_sink.emit(stage="implementation", agent="OpenRouter", progress=70, message=f"Stopped leftover process(es): {', '.join(left_running)}", level=EventLevel.WARNING)
        if not written:
            event_sink.emit(stage="implementation", agent="OpenRouter", progress=70, message="OpenRouter returned no writable files.", level=EventLevel.ERROR)
            return OpenCodeExecutionResult(
                success=False,
                summary="OpenRouter returned no writable files.",
                errors=("openrouter_empty_output",),
                elapsed_seconds=elapsed,
                timeout_seconds=limit,
            )
        event_sink.emit(stage="implementation", agent="OpenRouter", progress=70, message=f"OpenRouter wrote {len(written)} file(s).")
        return OpenCodeExecutionResult(
            success=True,
            summary=f"OpenRouter ({selected}) wrote {len(written)} file(s).",
            outcome="generated",
            meaningful_artifacts=written,
            elapsed_seconds=elapsed,
            timeout_seconds=limit,
        )
