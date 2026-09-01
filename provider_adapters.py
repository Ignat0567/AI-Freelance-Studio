from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from provider_contracts import (
    AgentEvent,
    AgentEventType,
    AuthenticationResult,
    AuthMethod,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionTestResult,
    ConnectionType,
    DetectionResult,
    ModelDescriptor,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConnection,
    ProviderErrorCode,
    ProviderUsage,
)
from provider_credentials import ProviderCredentialStore, default_backend
from provider_artifacts import ProviderArtifactRecorder
from provider_registry import provider_registry
from safe_command_runner import CommandRisk, run_command
from workflow_contracts import ExecutionBrief
from workflow_artifacts import mask_secrets, utc_now


def _cap(**overrides: Any) -> ProviderCapabilities:
    return ProviderCapabilities(**overrides)


TERMINAL_EVENTS = {AgentEventType.COMPLETED.value, AgentEventType.CANCELLED.value, AgentEventType.ERROR.value}


def _event(event_type: AgentEventType | str, message: str = "", execution_id: str = "", data: dict[str, Any] | None = None) -> AgentEvent:
    return AgentEvent(str(event_type), str(mask_secrets(message)), execution_id, mask_secrets(data or {}))


def _brief_prompt(brief: ExecutionBrief) -> str:
    payload = brief.to_dict()
    return (
        f"Task: {brief.title}\n\nObjective:\n{brief.objective}\n\n"
        f"Requirements:\n" + "\n".join(f"- {item}" for item in brief.requirements) + "\n\n"
        f"Constraints:\n" + "\n".join(f"- {item}" for item in brief.constraints) + "\n\n"
        f"Acceptance criteria:\n" + "\n".join(f"- {item}" for item in brief.acceptance_criteria) + "\n\n"
        f"Implementation steps:\n" + "\n".join(f"- {item}" for item in brief.implementation_steps) + "\n\n"
        f"Allowed paths: {', '.join(brief.allowed_paths)}\nForbidden paths: {', '.join(brief.forbidden_paths)}\n"
        "Return a concise implementation plan or patch instructions. Do not assume direct repository access unless tools are explicitly available.\n\n"
        f"Execution brief JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _usage(input_tokens: int | None = None, output_tokens: int | None = None, cached_input_tokens: int | None = None, total_tokens: int | None = None) -> dict[str, Any]:
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return ProviderUsage(input_tokens, output_tokens, cached_input_tokens, total_tokens, None, None, False).to_dict()


def _map_http_error(status: int) -> str:
    if status == 401:
        return ProviderErrorCode.AUTH_REQUIRED.value
    if status == 403:
        return ProviderErrorCode.PLAN_UNSUPPORTED.value
    if status == 404:
        return ProviderErrorCode.MODEL_UNAVAILABLE.value
    if status == 429:
        return ProviderErrorCode.RATE_LIMITED.value
    if status >= 500:
        return ProviderErrorCode.NETWORK_ERROR.value
    return ProviderErrorCode.UNKNOWN.value


def _parse_cli_event(line: str) -> tuple[str, str, dict[str, Any]]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return AgentEventType.TEXT_DELTA.value, line, {}
    if not isinstance(data, dict):
        return AgentEventType.TEXT_DELTA.value, line, {}
    raw_type = str(data.get("type") or data.get("event") or "text_delta").lower()
    mapping = {
        "message": AgentEventType.TEXT_DELTA.value,
        "delta": AgentEventType.TEXT_DELTA.value,
        "text": AgentEventType.TEXT_DELTA.value,
        "tool": AgentEventType.TOOL_CALL.value,
        "tool_call": AgentEventType.TOOL_CALL.value,
        "tool_result": AgentEventType.TOOL_RESULT.value,
        "usage": AgentEventType.USAGE.value,
        "status": AgentEventType.STATUS.value,
    }
    event_type = mapping.get(raw_type, raw_type if raw_type in {item.value for item in AgentEventType} else AgentEventType.TEXT_DELTA.value)
    message = str(data.get("message") or data.get("text") or data.get("delta") or "")
    if not message and event_type == AgentEventType.USAGE.value:
        message = "Usage reported"
    return event_type, message, data


def _which(candidates: list[str], explicit: str = "") -> str:
    if explicit and Path(explicit).is_file():
        return explicit
    for item in candidates:
        found = shutil.which(item)
        if found:
            return found
    return ""


async def _version(binary: str, args: list[str] | None = None) -> str:
    if not binary:
        return ""
    try:
        result = await run_command([binary, *(args or ["--version"])], str(Path.cwd()), timeout_seconds=10)
        return (result.stdout or result.stderr).strip().splitlines()[0] if result.exit_code == 0 and (result.stdout or result.stderr).strip() else ""
    except Exception:
        return ""


class CLISubscriptionAdapter(ProviderAdapter):
    cli_names: list[str] = []
    login_args: list[str] = ["login"]
    logout_args: list[str] = ["logout"]
    status_args: list[str] = ["status"]
    run_args_prefix: list[str] = []
    default_models: list[str] = []
    # Friendly labels for default_models entries; falls back to the raw id when absent.
    model_display_names: dict[str, str] = {}
    capability = _cap(coding=True, chat=True, code_generation=True, tools=True, native_tool_calling=True, repository_access=True, file_editing=True, command_execution=True, streaming=True, model_listing=True, cancellation=True, tool_calling="native")

    def __init__(self, connection: ProviderConnection):
        super().__init__(connection)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    def binary(self) -> str:
        return _which(self.cli_names, self.connection.executable_path)

    async def detect(self) -> DetectionResult:
        binary = self.binary()
        return DetectionResult(status=ConnectionStatus.READY.value if binary else ConnectionStatus.NOT_INSTALLED.value, installed=bool(binary), version=await _version(binary), executable_path=binary)

    async def get_status(self) -> ConnectionHealth:
        binary = self.binary()
        if not binary:
            return ConnectionHealth(ConnectionStatus.NOT_INSTALLED.value, ProviderErrorCode.CLI_NOT_INSTALLED.value, "CLI executable was not found")
        result = await run_command([binary, *self.status_args], str(Path.cwd()), timeout_seconds=20)
        if result.exit_code == 0:
            return ConnectionHealth(ConnectionStatus.READY.value, message="CLI status command succeeded", diagnostics={"version": await _version(binary)})
        text = f"{result.stdout}\n{result.stderr}".lower()
        code = ProviderErrorCode.AUTH_REQUIRED.value if "login" in text or "auth" in text or "sign" in text else ProviderErrorCode.UNKNOWN.value
        return ConnectionHealth(ConnectionStatus.SIGNED_OUT.value if code == ProviderErrorCode.AUTH_REQUIRED.value else ConnectionStatus.ERROR.value, code, "CLI status command did not report ready", {"exit_code": result.exit_code})

    async def authenticate(self) -> AuthenticationResult:
        binary = self.binary()
        if not binary:
            return AuthenticationResult(ConnectionStatus.NOT_INSTALLED.value, "CLI executable was not found", False)
        proc = await asyncio.create_subprocess_exec(binary, *self.login_args, cwd=str(Path.cwd()), shell=False)
        return AuthenticationResult(ConnectionStatus.AUTHENTICATING.value, "Official CLI login was started. Complete the provider-owned flow.", True, {"pid": proc.pid, "command": [binary, *self.login_args]})

    async def logout(self) -> None:
        binary = self.binary()
        if binary:
            await run_command([binary, *self.logout_args], str(Path.cwd()), timeout_seconds=30)

    async def list_models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(model, self.model_display_names.get(model, model), self.capability) for model in self.default_models]

    async def get_capabilities(self) -> ProviderCapabilities:
        return self.capability

    async def test_connection(self) -> ConnectionTestResult:
        status = await self.get_status()
        return ConnectionTestResult(status.status, status.status == ConnectionStatus.READY.value, status.message, status.error_code, status.diagnostics)

    async def execute(self, brief: ExecutionBrief) -> AsyncIterator[AgentEvent]:
        binary = self.binary()
        execution_id = f"cli-{uuid.uuid4().hex[:8]}"
        artifacts = ProviderArtifactRecorder.from_brief(brief)
        artifacts.write_json("connection.json", self.connection.to_dict())
        artifacts.write_json("capability_snapshot.json", self.capability.to_dict())
        if not binary:
            yield _event(AgentEventType.ERROR, "CLI executable was not found", execution_id, {"error_code": ProviderErrorCode.CLI_NOT_INSTALLED.value})
            return
        prompt = json.dumps(brief.to_dict(), ensure_ascii=False)
        command = [binary, *self.run_args_prefix, prompt]
        attempt_record = {"connection_id": self.connection.connection_id, "execution_id": execution_id, "adapter_type": self.connection.connection_type, "model_id": self.connection.model_id, "retry_number": 1, "started_at": utc_now(), "transport": "cli", "command_shape": [binary, *self.run_args_prefix, "<execution_brief>"]}
        yield _event(AgentEventType.STARTED, "CLI execution started", execution_id, {"command_shape": [binary, *self.run_args_prefix, "<execution_brief>"]})
        terminal = False
        proc = await asyncio.create_subprocess_exec(*command, cwd=brief.project_root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, shell=False)
        self._processes[execution_id] = proc
        timeout_seconds = int(self.connection.metadata.get("timeout_seconds", 600) if isinstance(self.connection.metadata, dict) else 600)
        deadline = time.monotonic() + timeout_seconds
        try:
            async def stderr_reader() -> str:
                if not proc.stderr:
                    return ""
                data = await proc.stderr.read()
                return data.decode("utf-8", errors="replace")[-20_000:]

            stderr_task = asyncio.create_task(stderr_reader())
            if proc.stdout:
                while True:
                    if execution_id in self._cancelled:
                        proc.terminate()
                        await proc.wait()
                        yield _event(AgentEventType.CANCELLED, "CLI execution cancelled", execution_id)
                        terminal = True
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        proc.terminate()
                        await proc.wait()
                        yield _event(AgentEventType.ERROR, "CLI execution timeout", execution_id, {"error_code": ProviderErrorCode.TIMEOUT.value})
                        artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "error", "normalized_error": ProviderErrorCode.TIMEOUT.value, "usage_reference": "", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                        terminal = True
                        break
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.1, remaining))
                    except asyncio.TimeoutError:
                        proc.terminate()
                        await proc.wait()
                        yield _event(AgentEventType.ERROR, "CLI execution timeout", execution_id, {"error_code": ProviderErrorCode.TIMEOUT.value})
                        artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "error", "normalized_error": ProviderErrorCode.TIMEOUT.value, "usage_reference": "", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                        terminal = True
                        break
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    parsed = _parse_cli_event(text)
                    yield _event(parsed[0], parsed[1], execution_id, parsed[2])
            if not terminal:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    proc.terminate()
                    yield _event(AgentEventType.ERROR, "CLI execution timeout", execution_id, {"error_code": ProviderErrorCode.TIMEOUT.value})
                    terminal = True
                stderr = await stderr_task
                if not terminal:
                    if proc.returncode == 0:
                        yield _event(AgentEventType.COMPLETED, "CLI execution completed", execution_id, {"exit_code": proc.returncode})
                        artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "completed", "normalized_error": "", "usage_reference": "", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                    else:
                        yield _event(AgentEventType.ERROR, "CLI execution failed", execution_id, {"error_code": ProviderErrorCode.PROCESS_CRASHED.value, "exit_code": proc.returncode, "stderr": stderr})
                        artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "error", "normalized_error": ProviderErrorCode.PROCESS_CRASHED.value, "usage_reference": "", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
        finally:
            self._processes.pop(execution_id, None)
            self._cancelled.discard(execution_id)

    async def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)
        proc = self._processes.get(execution_id)
        if proc and proc.returncode is None:
            proc.terminate()


class CodexChatGPTSubscriptionAdapter(CLISubscriptionAdapter):
    cli_names = ["codex.cmd", "codex.exe", "codex"]
    login_args = ["login"]
    status_args = ["auth", "status"]
    run_args_prefix = ["exec"]
    default_models = ["codex/default", "codex/gpt-5.5", "codex/gpt-5.5-codex"]
    model_display_names = {
        "codex/default": "Default (CLI-selected)",
        "codex/gpt-5.5": "GPT-5.5",
        "codex/gpt-5.5-codex": "GPT-5.5 Codex",
    }


class ClaudeSubscriptionAdapter(CLISubscriptionAdapter):
    cli_names = ["claude.cmd", "claude.exe", "claude"]
    login_args = ["auth", "login"]
    logout_args = ["auth", "logout"]
    status_args = ["auth", "status"]
    run_args_prefix = []
    # Bare aliases the official `claude` CLI itself accepts via --model (see `claude --help`);
    # keep the "claude/" prefix for Studio's own connection-scoped model-id convention, and
    # strip it back off before invoking the CLI.
    default_models = ["claude/default", "claude/opus", "claude/sonnet", "claude/fable", "claude/haiku"]
    model_display_names = {
        "claude/default": "Default (CLI-selected)",
        "claude/opus": "Claude Opus (alias)",
        "claude/sonnet": "Claude Sonnet (alias)",
        "claude/fable": "Claude Fable (alias)",
        "claude/haiku": "Claude Haiku (alias)",
    }


class GrokSubscriptionAdapter(CLISubscriptionAdapter):
    cli_names = ["grok.exe", "grok"]
    login_args = ["login"]
    logout_args = ["logout"]
    status_args = ["models"]
    run_args_prefix = ["--permission-mode", "plan", "--no-subagents", "--disable-web-search", "--output-format", "json", "-p"]
    default_models = ["grok/default", "grok/grok-4.6", "grok/grok-4.5"]
    model_display_names = {
        "grok/default": "Default (CLI-selected)",
        "grok/grok-4.6": "Grok 4.6",
        "grok/grok-4.5": "Grok 4.5",
    }

    def binary(self) -> str:
        explicit = self.connection.executable_path
        if explicit and Path(explicit).is_file():
            return explicit
        home_cli = Path.home() / ".grok" / "bin" / "grok.exe"
        if home_cli.is_file():
            return str(home_cli)
        return super().binary()


class GeminiGoogleAccountAdapter(CLISubscriptionAdapter):
    cli_names = ["gemini.cmd", "gemini.exe", "gemini"]
    login_args = ["auth", "login"]
    status_args = ["auth", "status"]
    run_args_prefix = []
    default_models = ["gemini/default", "gemini/pro", "gemini/flash"]
    model_display_names = {
        "gemini/default": "Default (CLI-selected)",
        "gemini/pro": "Gemini Pro",
        "gemini/flash": "Gemini Flash",
    }

    async def test_connection(self) -> ConnectionTestResult:
        detected = await self.detect()
        if not detected.installed:
            return ConnectionTestResult(ConnectionStatus.NOT_INSTALLED.value, False, "Gemini CLI was not found", ProviderErrorCode.CLI_NOT_INSTALLED.value)
        return ConnectionTestResult(ConnectionStatus.UNSUPPORTED.value, False, "Google account Gemini execution requires an official supported CLI contract; Studio will not emulate OAuth.", ProviderErrorCode.UNSUPPORTED.value)


class APIAdapter(ProviderAdapter):
    base_url = ""
    api_path = "/v1/chat/completions"
    models_path = "/v1/models"
    default_models: list[str] = []
    provider_name = "api"
    capability = _cap(chat=True, code_generation=True, streaming=True, structured_output=True, model_listing=True, cancellation=True, repository_access=False, file_editing=False, command_execution=False, tool_calling="disabled")

    def __init__(self, connection: ProviderConnection, credential_store: ProviderCredentialStore | None = None):
        super().__init__(connection)
        self.credential_store = credential_store or ProviderCredentialStore(default_backend())
        self._cancelled: set[str] = set()

    def api_key(self) -> str:
        ref = self.connection.credential_reference
        if not ref:
            return ""
        value = self.credential_store.read_api_key(ref).strip()
        if not value and os.environ.get("FREELANCERSTUDIO_ALLOW_ENV_CREDENTIAL_READ") == "1":
            value = os.environ.get(ref, "").strip()
        return value

    def endpoint(self) -> str:
        return (self.connection.endpoint or self.base_url).rstrip("/")

    async def detect(self) -> DetectionResult:
        return DetectionResult(ConnectionStatus.READY.value if self.endpoint() else ConnectionStatus.NOT_CONFIGURED.value, bool(self.endpoint()), diagnostics={"endpoint": self.endpoint()})

    async def get_status(self) -> ConnectionHealth:
        if not self.endpoint():
            return ConnectionHealth(ConnectionStatus.NOT_CONFIGURED.value, ProviderErrorCode.UNSUPPORTED.value, "Endpoint is not configured")
        if not self.api_key():
            return ConnectionHealth(ConnectionStatus.NOT_CONFIGURED.value, ProviderErrorCode.AUTH_REQUIRED.value, "API key credential reference is not available")
        return ConnectionHealth(ConnectionStatus.READY.value, message="API credential reference is configured")

    async def authenticate(self) -> AuthenticationResult:
        return AuthenticationResult(ConnectionStatus.UNSUPPORTED.value, "API connections authenticate by saving an API key reference, not OAuth login.")

    async def logout(self) -> None:
        return None

    async def list_models(self) -> list[ModelDescriptor]:
        endpoint = self.endpoint()
        key = self.api_key()
        if endpoint and key and self.models_path:
            try:
                data = await asyncio.to_thread(self._request_json, "GET", f"{endpoint}{self.models_path}", None, key, 20)
                items = data.get("data") if isinstance(data, dict) else []
                models = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
                if models:
                    return [ModelDescriptor(item, item, self.capability) for item in models]
            except Exception:
                pass
        if self.default_models:
            return [ModelDescriptor(item, item, self.capability) for item in self.default_models]
        return []

    async def get_capabilities(self) -> ProviderCapabilities:
        return self.capability

    async def test_connection(self) -> ConnectionTestResult:
        health = await self.get_status()
        return ConnectionTestResult(health.status, health.status == ConnectionStatus.READY.value, health.message, health.error_code, health.diagnostics)

    async def execute(self, brief: ExecutionBrief) -> AsyncIterator[AgentEvent]:
        execution_id = f"api-{uuid.uuid4().hex[:8]}"
        artifacts = ProviderArtifactRecorder.from_brief(brief)
        artifacts.write_json("connection.json", self.connection.to_dict())
        artifacts.write_json("capability_snapshot.json", self.capability.to_dict())
        health = await self.get_status()
        if health.status != ConnectionStatus.READY.value:
            yield _event(AgentEventType.ERROR, health.message, execution_id, {"error_code": health.error_code})
            return
        yield _event(AgentEventType.STARTED, "API execution started", execution_id, {"provider": self.provider_name, "model": self.model_id()})
        terminal = False
        attempt = 0
        started = time.monotonic()
        max_attempts = int(self.connection.metadata.get("max_attempts", 3) if isinstance(self.connection.metadata, dict) else 3)
        try:
            while attempt < max_attempts:
                attempt += 1
                attempt_record = {"connection_id": self.connection.connection_id, "execution_id": execution_id, "adapter_type": self.connection.connection_type, "model_id": self.model_id(), "retry_number": attempt, "started_at": utc_now(), "transport": "api", "fallback_reason": ""}
                if execution_id in self._cancelled:
                    yield _event(AgentEventType.CANCELLED, "API execution cancelled", execution_id, {"attempts": attempt - 1})
                    terminal = True
                    break
                meaningful = False
                try:
                    async for event in self._execute_once(brief, execution_id, attempt):
                        if event.type in (AgentEventType.TEXT_DELTA.value, AgentEventType.TOOL_CALL.value, AgentEventType.USAGE.value):
                            meaningful = True
                        yield event
                        if event.type in TERMINAL_EVENTS:
                            terminal = True
                            break
                    if terminal:
                        artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": event.type if 'event' in locals() else "terminal", "normalized_error": event.data.get("error_code", "") if 'event' in locals() else "", "usage_reference": "provider/usage.json", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                        break
                    yield _event(AgentEventType.COMPLETED, "API execution completed", execution_id, {"attempts": attempt, "duration_seconds": round(time.monotonic() - started, 3)})
                    artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "completed", "normalized_error": "", "usage_reference": "provider/usage.json", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                    terminal = True
                    break
                except _ProviderHTTPError as exc:
                    retryable = exc.status in {408, 429, 500, 502, 503, 504} and not meaningful and attempt < max_attempts
                    if retryable:
                        yield _event(AgentEventType.STATUS, "Retrying transient provider error", execution_id, {"attempt": attempt, "status_code": exc.status, "error_code": _map_http_error(exc.status)})
                        await asyncio.sleep(min(2.0, 0.2 * (2 ** (attempt - 1))) + random.random() * 0.05)
                        continue
                    yield _event(AgentEventType.ERROR, exc.message, execution_id, {"error_code": _map_http_error(exc.status), "status_code": exc.status, "attempts": attempt})
                    artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "error", "normalized_error": _map_http_error(exc.status), "usage_reference": "provider/usage.json", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                    artifacts.write_json("sanitized_diagnostics.json", {"last_error": exc.message, "status_code": exc.status, "attempts": attempt})
                    terminal = True
                    break
                except asyncio.CancelledError:
                    yield _event(AgentEventType.CANCELLED, "API execution cancelled", execution_id, {"attempts": attempt})
                    artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "cancelled", "normalized_error": "", "usage_reference": "provider/usage.json", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                    terminal = True
                    break
                except Exception as exc:
                    yield _event(AgentEventType.ERROR, "Provider response was malformed or unavailable", execution_id, {"error_code": ProviderErrorCode.MALFORMED_RESPONSE.value, "detail": str(exc)[:300], "attempts": attempt})
                    artifacts.append_attempt({**attempt_record, "finished_at": utc_now(), "result": "error", "normalized_error": ProviderErrorCode.MALFORMED_RESPONSE.value, "usage_reference": "provider/usage.json", "diagnostics_reference": "provider/sanitized_diagnostics.json"})
                    artifacts.write_json("sanitized_diagnostics.json", {"last_error": str(exc)[:300], "attempts": attempt})
                    terminal = True
                    break
            if not terminal:
                yield _event(AgentEventType.ERROR, "Provider retry limit exceeded", execution_id, {"error_code": ProviderErrorCode.NETWORK_ERROR.value, "attempts": attempt})
        finally:
            self._cancelled.discard(execution_id)

    async def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)

    def model_id(self) -> str:
        raw = self.connection.model_id or (self.default_models[0] if self.default_models else "")
        prefix = f"{self.provider_name}/"
        return raw[len(prefix):] if raw.startswith(prefix) else raw

    async def _execute_once(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> AsyncIterator[AgentEvent]:
        events = await asyncio.to_thread(self._collect_events, brief, execution_id, attempt)
        for event in events:
            if execution_id in self._cancelled:
                yield _event(AgentEventType.CANCELLED, "API execution cancelled", execution_id, {"attempt": attempt})
                return
            yield event

    def _collect_events(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> list[AgentEvent]:
        raise NotImplementedError

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _request_json(self, method: str, url: str, payload: dict[str, Any] | None, api_key: str, timeout: int) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method, headers=self._headers(api_key))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise _ProviderHTTPError(exc.code, detail or exc.reason) from exc

    def _request_sse(self, url: str, payload: dict[str, Any], api_key: str, timeout: int) -> list[dict[str, Any]]:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={**self._headers(api_key), "Accept": "text/event-stream"})
        chunks: list[dict[str, Any]] = []
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        text = line[5:].strip()
                        if text == "[DONE]":
                            break
                        chunks.append(json.loads(text))
            return chunks
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise _ProviderHTTPError(exc.code, detail or exc.reason) from exc

    def _chat_payload(self, brief: ExecutionBrief, stream: bool) -> dict[str, Any]:
        return {"model": self.model_id(), "stream": stream, "messages": [{"role": "system", "content": "Follow the ExecutionBrief exactly."}, {"role": "user", "content": _brief_prompt(brief)}]}


class _ProviderHTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class OpenAIAPIAdapter(APIAdapter):
    base_url = "https://api.openai.com"
    provider_name = "openai"
    default_models = ["openai/gpt-5.5", "openai/gpt-5.5-fast"]

    def _collect_events(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> list[AgentEvent]:
        payload = {
            "model": self.model_id(),
            "stream": True,
            "messages": [
                {"role": "system", "content": "You are a coding assistant inside AI Freelancer Studio. Follow the ExecutionBrief exactly. Do not claim repository access unless tools are supplied."},
                {"role": "user", "content": _brief_prompt(brief)},
            ],
        }
        chunks = self._request_sse(f"{self.endpoint()}{self.api_path}", payload, self.api_key(), int(self.connection.metadata.get("timeout_seconds", 120) if isinstance(self.connection.metadata, dict) else 120))
        events: list[AgentEvent] = []
        finish_reason = None
        for chunk in chunks:
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                events.append(_event(AgentEventType.TEXT_DELTA, str(delta["content"]), execution_id, {"attempt": attempt}))
            for tool in delta.get("tool_calls") or []:
                events.append(_event(AgentEventType.TOOL_CALL, "Tool call requested", execution_id, {"tool_call": tool, "unsupported": True}))
            if choice.get("finish_reason"):
                finish_reason = choice.get("finish_reason")
            if chunk.get("usage"):
                u = chunk["usage"]
                events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(u.get("prompt_tokens"), u.get("completion_tokens"), None, u.get("total_tokens"))))
        events.append(_event(AgentEventType.COMPLETED, "OpenAI API execution completed", execution_id, {"finish_reason": finish_reason, "attempt": attempt}))
        return events


class XAIAPIAdapter(OpenAIAPIAdapter):
    base_url = "https://api.x.ai"
    provider_name = "xai"
    default_models = ["xai/grok-4.6", "xai/grok-4.5"]


class OpenRouterAPIAdapter(OpenAIAPIAdapter):
    """OpenRouter is an OpenAI-compatible aggregator, so the inherited SSE event
    normalization applies verbatim. base_url deliberately includes /api so the inherited
    api_path ("/v1/chat/completions") and models_path ("/v1/models") resolve correctly with
    no overrides.

    provider_name is load-bearing: model_id() strips a leading f"{provider_name}/", and
    OpenRouter model IDs carry a real vendor prefix ("anthropic/claude-sonnet-4"). Naming
    this after any actual vendor would silently eat that prefix and request the wrong model.
    """

    base_url = "https://openrouter.ai/api"
    provider_name = "openrouter"
    default_models = [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
        "google/gemini-2.5-pro",
        "meta-llama/llama-3.3-70b-instruct",
    ]

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = super()._headers(api_key)
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        # OpenRouter's optional attribution headers are opt-in rather than hardcoded:
        # HTTP-Referer identifies this app to a third party on every request, which is the
        # user's disclosure to make, not ours.
        for header, key in (("HTTP-Referer", "http_referer"), ("X-Title", "x_title")):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                headers[header] = value.strip()
        return headers


class AnthropicAPIAdapter(APIAdapter):
    base_url = "https://api.anthropic.com"
    api_path = "/v1/messages"
    models_path = ""
    provider_name = "anthropic"
    default_models = ["anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5"]

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def _collect_events(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> list[AgentEvent]:
        payload = {"model": self.model_id(), "max_tokens": int(self.connection.metadata.get("max_tokens", 4096) if isinstance(self.connection.metadata, dict) else 4096), "stream": True, "system": "You are a coding assistant inside AI Freelancer Studio. Follow the ExecutionBrief exactly.", "messages": [{"role": "user", "content": _brief_prompt(brief)}]}
        chunks = self._request_sse(f"{self.endpoint()}{self.api_path}", payload, self.api_key(), int(self.connection.metadata.get("timeout_seconds", 120) if isinstance(self.connection.metadata, dict) else 120))
        events: list[AgentEvent] = []
        stop_reason = None
        for chunk in chunks:
            etype = chunk.get("type")
            if etype == "content_block_delta" and (chunk.get("delta") or {}).get("text"):
                events.append(_event(AgentEventType.TEXT_DELTA, str(chunk["delta"]["text"]), execution_id, {"attempt": attempt}))
            if etype == "content_block_start" and (chunk.get("content_block") or {}).get("type") == "tool_use":
                events.append(_event(AgentEventType.TOOL_CALL, "Tool use requested", execution_id, {"tool_call": chunk.get("content_block"), "unsupported": True}))
            if etype == "message_delta":
                stop_reason = (chunk.get("delta") or {}).get("stop_reason") or stop_reason
                usage = chunk.get("usage") or {}
                if usage:
                    events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(output_tokens=usage.get("output_tokens"))))
            if etype == "message_start" and (chunk.get("message") or {}).get("usage"):
                usage = chunk["message"]["usage"]
                events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))))
        events.append(_event(AgentEventType.COMPLETED, "Anthropic API execution completed", execution_id, {"finish_reason": stop_reason, "attempt": attempt}))
        return events


class GeminiAPIAdapter(APIAdapter):
    base_url = "https://generativelanguage.googleapis.com"
    api_path = "/v1beta/models/{model}:streamGenerateContent?alt=sse"
    models_path = "/v1beta/models"
    provider_name = "google"
    default_models = ["google/gemini-2.5-pro", "google/gemini-2.5-flash"]

    def _headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": api_key}

    def _collect_events(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> list[AgentEvent]:
        model = self.model_id()
        payload = {"systemInstruction": {"parts": [{"text": "You are a coding assistant inside AI Freelancer Studio. Follow the ExecutionBrief exactly."}]}, "contents": [{"role": "user", "parts": [{"text": _brief_prompt(brief)}]}]}
        chunks = self._request_sse(f"{self.endpoint()}{self.api_path.format(model=model)}", payload, self.api_key(), int(self.connection.metadata.get("timeout_seconds", 120) if isinstance(self.connection.metadata, dict) else 120))
        events: list[AgentEvent] = []
        finish_reason = None
        for chunk in chunks:
            candidates = chunk.get("candidates") or []
            for candidate in candidates:
                finish_reason = candidate.get("finishReason") or finish_reason
                for part in ((candidate.get("content") or {}).get("parts") or []):
                    if part.get("text"):
                        events.append(_event(AgentEventType.TEXT_DELTA, str(part["text"]), execution_id, {"attempt": attempt}))
                    if part.get("functionCall"):
                        events.append(_event(AgentEventType.TOOL_CALL, "Function call requested", execution_id, {"tool_call": part.get("functionCall"), "unsupported": True}))
            usage = chunk.get("usageMetadata") or {}
            if usage:
                events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("cachedContentTokenCount"), usage.get("totalTokenCount"))))
        events.append(_event(AgentEventType.COMPLETED, "Gemini API execution completed", execution_id, {"finish_reason": finish_reason, "attempt": attempt}))
        return events


class OpenAICompatibleLocalAdapter(APIAdapter):
    base_url = "http://127.0.0.1:1234"
    provider_name = "local"
    capability = _cap(chat=True, code_generation=True, streaming=True, structured_output=False, model_listing=True, cancellation=True, local_execution=True, repository_access=False, file_editing=False, command_execution=False)

    async def get_status(self) -> ConnectionHealth:
        endpoint = self.endpoint()
        if not _is_loopback(endpoint):
            return ConnectionHealth(ConnectionStatus.ERROR.value, ProviderErrorCode.PRIVACY_POLICY_BLOCK.value, "Local endpoint must use loopback only")
        try:
            with urllib.request.urlopen(f"{endpoint}{self.models_path}", timeout=2) as response:
                return ConnectionHealth(ConnectionStatus.READY.value if response.status < 500 else ConnectionStatus.ERROR.value, message="OpenAI-compatible local endpoint responded", diagnostics={"status_code": response.status})
        except urllib.error.URLError as exc:
            return ConnectionHealth(ConnectionStatus.ERROR.value, ProviderErrorCode.NETWORK_ERROR.value, "Local endpoint did not respond", {"error": str(exc)[:200]})

    def api_key(self) -> str:
        return "local"

    def _collect_events(self, brief: ExecutionBrief, execution_id: str, attempt: int) -> list[AgentEvent]:
        stream = bool(self.connection.metadata.get("stream", True) if isinstance(self.connection.metadata, dict) else True)
        payload = self._chat_payload(brief, stream)
        timeout = int(self.connection.metadata.get("timeout_seconds", 120) if isinstance(self.connection.metadata, dict) else 120)
        if not stream:
            data = self._request_json("POST", f"{self.endpoint()}{self.api_path}", payload, self.api_key(), timeout)
            events: list[AgentEvent] = []
            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            message = choice.get("message") or {}
            text = message.get("content") or choice.get("text")
            if text:
                events.append(_event(AgentEventType.TEXT_DELTA, str(text), execution_id, {"attempt": attempt, "response_mode": "non_stream"}))
            if isinstance(data, dict) and data.get("usage"):
                u = data["usage"]
                events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(u.get("prompt_tokens"), u.get("completion_tokens"), None, u.get("total_tokens"))))
            events.append(_event(AgentEventType.COMPLETED, "OpenAI-compatible local execution completed", execution_id, {"attempt": attempt, "response_mode": "non_stream"}))
            return events
        try:
            chunks = self._request_sse(f"{self.endpoint()}{self.api_path}", payload, self.api_key(), timeout)
        except _ProviderHTTPError:
            raise
        events: list[AgentEvent] = []
        for chunk in chunks:
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or choice.get("text")
            if text:
                events.append(_event(AgentEventType.TEXT_DELTA, str(text), execution_id, {"attempt": attempt}))
            if chunk.get("usage"):
                u = chunk["usage"]
                events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(u.get("prompt_tokens"), u.get("completion_tokens"), None, u.get("total_tokens"))))
        events.append(_event(AgentEventType.COMPLETED, "OpenAI-compatible local execution completed", execution_id, {"attempt": attempt}))
        return events


class OllamaLocalAdapter(ProviderAdapter):
    capability = _cap(chat=True, code_generation=True, streaming=True, model_listing=True, cancellation=True, local_execution=True, repository_access=False, file_editing=False, command_execution=False)

    def __init__(self, connection: ProviderConnection):
        super().__init__(connection)
        self._cancelled: set[str] = set()

    def endpoint(self) -> str:
        return (self.connection.endpoint or "http://127.0.0.1:11434").rstrip("/")

    async def detect(self) -> DetectionResult:
        health = await self.get_status()
        return DetectionResult(health.status, health.status == ConnectionStatus.READY.value, diagnostics=health.diagnostics)

    async def get_status(self) -> ConnectionHealth:
        endpoint = self.endpoint()
        if not _is_loopback(endpoint):
            return ConnectionHealth(ConnectionStatus.ERROR.value, ProviderErrorCode.PRIVACY_POLICY_BLOCK.value, "Ollama endpoint must use loopback for LOCAL_ONLY safety")
        try:
            with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=2) as response:
                return ConnectionHealth(ConnectionStatus.READY.value, message="Ollama endpoint responded", diagnostics={"status_code": response.status})
        except Exception as exc:
            return ConnectionHealth(ConnectionStatus.NOT_INSTALLED.value, ProviderErrorCode.NETWORK_ERROR.value, "Ollama endpoint is not available", {"error": str(exc)[:200]})

    async def authenticate(self) -> AuthenticationResult:
        return AuthenticationResult(ConnectionStatus.READY.value, "Ollama is local and does not require Studio credentials.")

    async def logout(self) -> None:
        return None

    async def list_models(self) -> list[ModelDescriptor]:
        endpoint = self.endpoint()
        try:
            with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))
            models = data.get("models", []) if isinstance(data, dict) else []
            return [ModelDescriptor(str(item.get("name") or item.get("model")), str(item.get("name") or item.get("model")), self.capability, {"runtime": "ollama"}) for item in models if isinstance(item, dict) and (item.get("name") or item.get("model"))]
        except Exception:
            return []

    async def get_capabilities(self) -> ProviderCapabilities:
        return self.capability

    async def test_connection(self) -> ConnectionTestResult:
        health = await self.get_status()
        models = await self.list_models() if health.status == ConnectionStatus.READY.value else []
        model_ready = not self.connection.model_id or any(item.id == self.connection.model_id for item in models)
        if health.status == ConnectionStatus.READY.value and not model_ready:
            return ConnectionTestResult(ConnectionStatus.MODEL_UNAVAILABLE.value, False, "Selected Ollama model is not installed", ProviderErrorCode.MODEL_UNAVAILABLE.value)
        return ConnectionTestResult(health.status, health.status == ConnectionStatus.READY.value, health.message, health.error_code, {**health.diagnostics, "models": [item.id for item in models]})

    async def execute(self, brief: ExecutionBrief) -> AsyncIterator[AgentEvent]:
        execution_id = f"ollama-{uuid.uuid4().hex[:8]}"
        artifacts = ProviderArtifactRecorder.from_brief(brief)
        artifacts.write_json("connection.json", self.connection.to_dict())
        artifacts.write_json("capability_snapshot.json", self.capability.to_dict())
        artifacts.append_attempt({"execution_id": execution_id, "provider": "ollama", "transport": "local", "model": self.connection.model_id})
        test = await self.test_connection()
        if not test.ready:
            yield _event(AgentEventType.ERROR, test.message, execution_id, {"error_code": test.error_code})
            return
        yield _event(AgentEventType.STARTED, "Ollama execution started", execution_id, {"model": self.connection.model_id})
        try:
            events = await asyncio.to_thread(self._collect_events, brief, execution_id)
            for event in events:
                if execution_id in self._cancelled:
                    yield _event(AgentEventType.CANCELLED, "Ollama execution cancelled", execution_id)
                    return
                yield event
        except _ProviderHTTPError as exc:
            yield _event(AgentEventType.ERROR, exc.message, execution_id, {"error_code": _map_http_error(exc.status), "status_code": exc.status})
        except Exception as exc:
            yield _event(AgentEventType.ERROR, "Ollama stream was malformed or unavailable", execution_id, {"error_code": ProviderErrorCode.MALFORMED_RESPONSE.value, "detail": str(exc)[:300]})

    async def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)

    def _collect_events(self, brief: ExecutionBrief, execution_id: str) -> list[AgentEvent]:
        payload = {"model": self.connection.model_id, "prompt": _brief_prompt(brief), "stream": True}
        request = urllib.request.Request(f"{self.endpoint()}/api/generate", data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        events: list[AgentEvent] = []
        try:
            with urllib.request.urlopen(request, timeout=int(self.connection.metadata.get("timeout_seconds", 120) if isinstance(self.connection.metadata, dict) else 120)) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("response"):
                        events.append(_event(AgentEventType.TEXT_DELTA, str(data["response"]), execution_id))
                    if data.get("done"):
                        events.append(_event(AgentEventType.USAGE, "Usage reported", execution_id, _usage(input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))))
                        events.append(_event(AgentEventType.COMPLETED, "Ollama execution completed", execution_id, {"finish_reason": data.get("done_reason")}))
                        return events
            events.append(_event(AgentEventType.ERROR, "Ollama stream ended without terminal done event", execution_id, {"error_code": ProviderErrorCode.MALFORMED_RESPONSE.value}))
            return events
        except urllib.error.HTTPError as exc:
            raise _ProviderHTTPError(exc.code, exc.read().decode("utf-8", errors="replace")[:500] or exc.reason) from exc


class OpenCodeProviderAdapter(ProviderAdapter):
    async def detect(self) -> DetectionResult:
        from opencode_bridge import _discover_opencode
        binary = _discover_opencode() or self.connection.executable_path
        return DetectionResult(ConnectionStatus.READY.value if binary else ConnectionStatus.NOT_INSTALLED.value, bool(binary), await _version(binary), binary)

    async def get_status(self) -> ConnectionHealth:
        from opencode_bridge import test_opencode_readiness
        result = test_opencode_readiness(self.connection.executable_path, self.connection.model_id, "")
        return ConnectionHealth(ConnectionStatus.READY.value if result.get("ready") else ConnectionStatus.ERROR.value, result.get("error_code", ""), result.get("message", ""), result)

    async def authenticate(self) -> AuthenticationResult:
        return AuthenticationResult(ConnectionStatus.UNSUPPORTED.value, "OpenCode authentication is owned by OpenCode. Use the existing OpenCode setup/auth flow.")

    async def logout(self) -> None:
        return None

    async def list_models(self) -> list[ModelDescriptor]:
        from opencode_provider import OpenCodeBridgeConnection
        connection = OpenCodeBridgeConnection(connection_id=self.connection.connection_id, name=self.connection.display_name, configured_model=self.connection.model_id, executable_path=self.connection.executable_path)
        caps = await self.get_capabilities()
        return [ModelDescriptor(item, item, caps) for item in connection.available_models()]

    async def get_capabilities(self) -> ProviderCapabilities:
        return _cap(coding=True, chat=True, tools=True, repository_access=True, model_listing=True, cancellation=True, tool_calling="native")

    async def test_connection(self) -> ConnectionTestResult:
        status = await self.get_status()
        return ConnectionTestResult(status.status, status.status == ConnectionStatus.READY.value, status.message, status.error_code, status.diagnostics)

    async def execute(self, brief: ExecutionBrief) -> AsyncIterator[AgentEvent]:
        from opencode_bridge import get_bridge
        execution_id = f"opencode-{uuid.uuid4().hex[:8]}"
        artifacts = ProviderArtifactRecorder.from_brief(brief)
        artifacts.write_json("connection.json", self.connection.to_dict())
        caps = await self.get_capabilities()
        artifacts.write_json("capability_snapshot.json", caps.to_dict())
        artifacts.append_attempt({"execution_id": execution_id, "provider": "opencode", "transport": "opencode", "model": self.connection.model_id})
        yield AgentEvent("started", "OpenCode execution started", execution_id)
        result = get_bridge().execute_coding_task(brief.project_root, json.dumps(brief.to_dict(), ensure_ascii=False), model_override=self.connection.model_id)
        yield AgentEvent("completed" if result.get("success") else "error", result.get("summary") or result.get("error") or "OpenCode finished", execution_id, result)

    async def cancel(self, execution_id: str) -> None:
        from opencode_bridge import get_bridge
        get_bridge().cancel_session(execution_id)


def _is_loopback(endpoint: str) -> bool:
    value = endpoint.lower().replace("[::1]", "localhost")
    return value.startswith("http://127.0.0.1") or value.startswith("http://localhost") or value.startswith("http://::1")


def register_default_adapters() -> None:
    registrations = {
        ConnectionType.CODEX_CHATGPT_SUBSCRIPTION: CodexChatGPTSubscriptionAdapter,
        ConnectionType.CLAUDE_SUBSCRIPTION: ClaudeSubscriptionAdapter,
        ConnectionType.GROK_SUBSCRIPTION: GrokSubscriptionAdapter,
        ConnectionType.GEMINI_GOOGLE_ACCOUNT: GeminiGoogleAccountAdapter,
        ConnectionType.OPENAI_API_KEY: OpenAIAPIAdapter,
        ConnectionType.XAI_API_KEY: XAIAPIAdapter,
        ConnectionType.ANTHROPIC_API_KEY: AnthropicAPIAdapter,
        ConnectionType.GEMINI_API_KEY: GeminiAPIAdapter,
        ConnectionType.OPENROUTER_API_KEY: OpenRouterAPIAdapter,
        ConnectionType.OPENCODE_PROVIDER: OpenCodeProviderAdapter,
        ConnectionType.OLLAMA_LOCAL: OllamaLocalAdapter,
        ConnectionType.LM_STUDIO_LOCAL: OpenAICompatibleLocalAdapter,
        ConnectionType.LLAMA_CPP_SERVER: OpenAICompatibleLocalAdapter,
        ConnectionType.LOCALAI_LOCAL: OpenAICompatibleLocalAdapter,
        ConnectionType.VLLM_LOCAL: OpenAICompatibleLocalAdapter,
        ConnectionType.OPENAI_COMPATIBLE_LOCAL: OpenAICompatibleLocalAdapter,
    }
    for ctype, cls in registrations.items():
        if ctype.value not in provider_registry.registered_types():
            provider_registry.register(ctype, cls)


register_default_adapters()
