import asyncio
import sys
from pathlib import Path

import provider_adapters
from provider_contracts import AuthMethod, ConnectionStatus, ConnectionType, ProviderConnection, ProviderErrorCode
from workflow_contracts import ExecutionBrief


def _brief(tmp_path: Path) -> ExecutionBrief:
    return ExecutionBrief(
        project_id="fake-cli",
        task_id="fake-cli-task",
        title="Fake CLI",
        objective="Exercise fake CLI transport",
        project_root=str(tmp_path),
        requirements=["stream"],
        constraints=["safe"],
        acceptance_criteria=["terminal event once"],
        allowed_paths=["."],
        forbidden_paths=[".."],
        implementation_steps=["run"],
        test_commands=["python -m pytest -q"],
        validation_commands=[],
        requires_browser_validation=False,
        requires_security_review=False,
        approval_policy="never",
        sandbox_policy="workspace-write",
    )


def _fake_cli(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_cli.py"
    script.write_text(body, encoding="utf-8")
    return script


def _conn(kind: str, tmp_path: Path) -> ProviderConnection:
    ctype = ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value if kind == "codex" else ConnectionType.CLAUDE_SUBSCRIPTION.value
    return ProviderConnection("fake", kind, ctype, AuthMethod.DELEGATED_CLI_LOGIN.value, "Fake", executable_path=sys.executable)


async def _events(adapter, brief):
    return [event async for event in adapter.execute(brief)]


def _adapter(kind: str, tmp_path: Path, script: Path):
    cls = provider_adapters.CodexChatGPTSubscriptionAdapter if kind == "codex" else provider_adapters.ClaudeSubscriptionAdapter
    adapter = cls(_conn(kind, tmp_path))
    adapter.run_args_prefix = [str(script)]
    return adapter


def test_fake_cli_not_found_detection(tmp_path):
    conn = ProviderConnection("missing", "codex", ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value, AuthMethod.DELEGATED_CLI_LOGIN.value, "Missing", executable_path=str(tmp_path / "missing.exe"))
    detected = asyncio.run(provider_adapters.CodexChatGPTSubscriptionAdapter(conn).detect())
    assert detected.status == ConnectionStatus.NOT_INSTALLED.value


def test_fake_cli_jsonl_usage_tool_unknown_text_and_secret_masking(tmp_path):
    script = _fake_cli(tmp_path, """
import json
print(json.dumps({'type':'text_delta','text':'hello'}))
print(json.dumps({'type':'usage','input_tokens':1,'output_tokens':2}))
print(json.dumps({'type':'tool_call','name':'read_file'}))
print(json.dumps({'type':'mystery','api_key':'sk-secret-placeholder'}))
print('{malformed json')
""")
    events = asyncio.run(_events(_adapter("codex", tmp_path, script), _brief(tmp_path)))
    assert events[-1].type == "completed"
    assert any(event.type == "usage" for event in events)
    assert any(event.type == "tool_call" for event in events)
    assert any(event.type == "text_delta" and "malformed" in event.message for event in events)
    assert sum(event.type in {"completed", "cancelled", "error"} for event in events) == 1
    assert "sk-secret-placeholder" not in "\n".join(str(event.to_dict()) for event in events)


def test_fake_cli_text_fallback_stderr_warning_and_nonzero(tmp_path):
    script = _fake_cli(tmp_path, """
import sys
print('plain output')
print('warning on stderr', file=sys.stderr)
sys.exit(9)
""")
    events = asyncio.run(_events(_adapter("claude", tmp_path, script), _brief(tmp_path)))
    assert any(event.type == "text_delta" and event.message == "plain output" for event in events)
    assert events[-1].type == "error"
    assert events[-1].data["error_code"] == ProviderErrorCode.PROCESS_CRASHED.value
    assert "warning" in events[-1].data["stderr"]


def test_fake_cli_timeout_maps_error(tmp_path):
    script = _fake_cli(tmp_path, "import time\ntime.sleep(5)\n")
    adapter = _adapter("codex", tmp_path, script)
    adapter.connection = ProviderConnection(**{**adapter.connection.to_dict(), "metadata": {"timeout_seconds": 1}})
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert events[-1].type == "error"
    assert events[-1].data["error_code"] == ProviderErrorCode.TIMEOUT.value


def test_gemini_account_remains_unsupported(monkeypatch):
    monkeypatch.setattr(provider_adapters, "_which", lambda _names, _explicit="": sys.executable)
    monkeypatch.setattr(provider_adapters, "_version", lambda *_args, **_kwargs: asyncio.sleep(0, result="1.0.0"))
    conn = ProviderConnection("gemini", "google", ConnectionType.GEMINI_GOOGLE_ACCOUNT.value, AuthMethod.DELEGATED_CLI_LOGIN.value, "Gemini")
    result = asyncio.run(provider_adapters.GeminiGoogleAccountAdapter(conn).test_connection())
    assert result.status == ConnectionStatus.UNSUPPORTED.value
    assert "official supported CLI contract" in result.message
