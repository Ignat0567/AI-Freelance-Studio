import json
from pathlib import Path

import config_storage
import ai_utils


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_provider_key_is_read_from_config_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"openai_key": "sk-from-config"})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert ai_utils.get_api_key("openai") == "sk-from-config"


def test_legacy_api_key_fallback_is_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"openai_api_key": "sk-legacy-config"})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert ai_utils.get_api_key("openai") == "sk-legacy-config"


def test_opencode_provider_connections_are_read_from_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {
        "_provider_connections": [{
            "connection_type": "opencode_bridge",
            "enabled": True,
            "configured_model": "openai/gpt-5.5",
        }],
    })
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    captured = {}

    class FakeOpenCodeBridgeConnection:
        @classmethod
        def from_dict(cls, data):
            captured["connection"] = data
            return cls()

        def execute(self, payload):
            captured["payload"] = payload
            return {"status": "success", "text": "bridge-ok"}

    monkeypatch.setattr("opencode_provider.OpenCodeBridgeConnection", FakeOpenCodeBridgeConnection)

    result = ai_utils.ask_studio_ai_with_history(
        "opencode_bridge",
        "openai/gpt-5.5",
        "system",
        [{"role": "user", "content": "hello"}],
    )

    assert result == "bridge-ok"
    assert captured["connection"]["connection_type"] == "opencode_bridge"
    assert captured["payload"]["user_content"] == "hello"


def test_missing_or_invalid_config_returns_existing_empty_key_behavior(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    missing_path = tmp_path / "missing.json"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{invalid", encoding="utf-8")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(missing_path))
    assert ai_utils.get_api_key("openai") == ""

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(invalid_path))
    assert ai_utils.get_api_key("openai") == ""
    assert ai_utils.ask_studio_ai_with_history("opencode_bridge", "openai/gpt-5.5", "system", []) == "OpenCode OAuth is not configured yet. Open Settings -> AI Provider and select Authenticate Provider."


def test_opencode_direct_oauth_uses_requested_model_when_saved_connection_is_stale(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {
        "_provider_connections": [{
            "connection_id": "opencode-live",
            "connection_type": "opencode_oauth_bridge",
            "enabled": True,
            "configured_model": "nvidia/old-model",
        }],
        "_global_ai": {"provider": "opencode_bridge", "model": "openai/gpt-5.5"},
    })
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    captured = {}

    class FakeOpenCodeBridgeConnection:
        @classmethod
        def from_dict(cls, data):
            captured["connection"] = data
            return cls()

        def execute(self, payload):
            captured["payload"] = payload
            return {"status": "success", "text": "direct-ok"}

    monkeypatch.setattr("opencode_provider.OpenCodeBridgeConnection", FakeOpenCodeBridgeConnection)
    result = ai_utils.ask_studio_ai_with_history("opencode_bridge", "openai/gpt-5.5", "system", [{"role": "user", "content": "hi"}])
    assert result == "direct-ok"
    assert captured["connection"]["configured_model"] == "openai/gpt-5.5"
    assert captured["payload"]["requested_model"] == "openai/gpt-5.5"


def test_ai_utils_uses_config_storage_without_direct_studio_config_reads():
    source = Path(ai_utils.__file__).read_text(encoding="utf-8")

    assert "studio_config.json" not in source
    assert "load_studio_keys()" in source
    assert "with open(" not in source


def _capture_worker_invocation(monkeypatch) -> list:
    """Record argv passed to the AI worker subprocess without performing any network call."""
    calls = []

    class _Result:
        returncode = 0
        stdout = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(ai_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(ai_utils, "get_api_key", lambda provider: f"SECRET-{provider}-KEY")
    return calls


def test_every_selectable_provider_has_its_own_endpoint():
    """A provider offered in Settings but missing from PROVIDERS_URLS used to fall back to
    NVIDIA's endpoint, transmitting that provider's API key to NVIDIA."""
    import provider_config

    missing = [name for name in provider_config.AI_PROVIDER_MODELS if not ai_utils._resolve_base_url(name)]

    assert missing == [], f"providers reachable in Settings with no endpoint of their own: {missing}"


def test_google_requests_go_to_google_and_never_to_another_vendor(monkeypatch):
    calls = _capture_worker_invocation(monkeypatch)

    result = ai_utils.ask_studio_ai_with_history(
        "google", "gemini-2.0-flash-001", "system", [{"role": "user", "content": "hi"}]
    )

    assert result == "ok"
    url, api_key = calls[0][2], calls[0][3]
    assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert api_key == "SECRET-google-KEY"
    assert "nvidia" not in url.lower()


def test_unknown_provider_fails_closed_without_transmitting_the_key(monkeypatch):
    calls = _capture_worker_invocation(monkeypatch)

    result = ai_utils.ask_studio_ai_with_history(
        "totally-unknown", "some-model", "system", [{"role": "user", "content": "hi"}]
    )

    assert "no configured endpoint" in result
    assert calls == [], "an unrecognized provider must not reach any vendor endpoint"


def test_google_advertises_openai_compatible_sampling_support():
    """Reached through Gemini's OpenAI-compatibility endpoint, so top_k -- not an OpenAI
    parameter -- must stay off even though Gemini itself supports topK."""
    capabilities = ai_utils.provider_capabilities("google")

    assert capabilities["top_p"] is True
    assert capabilities["top_k"] is False
    assert capabilities["image_input"] is True


def test_claude_code_provider_routes_through_the_cli_not_a_missing_endpoint(monkeypatch):
    """Regression guard: "claude_code" is order_workflow's CLI-routing identity (see
    execution_config.py's provider normalization), not a real API-key provider, so it has
    no entry in PROVIDERS_URLS. Any caller resolving the global provider through
    ExecutionConfigurationProvider (chat, presentations, proposals -- anything using
    ask_studio_ai_with_history with the current global provider/model) must reach the CLI,
    not fall into the generic "no configured endpoint" path, which returns a plain English
    sentence that then fails JSON parsing wherever the caller expects structured output."""
    monkeypatch.setattr("claude_bridge._discover_claude", lambda: "claude.cmd")

    captured = {}

    class _FakeProc:
        returncode = 0

        def communicate(self, input, timeout):
            captured["prompt"] = input
            captured["timeout"] = timeout
            return json.dumps({"is_error": False, "result": "hello from claude"}), ""

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(ai_utils.subprocess, "Popen", fake_popen)

    result = ai_utils.ask_studio_ai_with_history("claude_code", "claude/sonnet", "system prompt", [{"role": "user", "content": "hi"}])

    assert result == "hello from claude"
    assert "no configured endpoint" not in result
    assert captured["cmd"][0] == "claude.cmd"
    assert "-p" in captured["cmd"]
    assert "system prompt" in captured["prompt"] and "hi" in captured["prompt"]


def test_claude_code_provider_reports_a_readable_error_when_cli_is_missing(monkeypatch):
    monkeypatch.setattr("claude_bridge._discover_claude", lambda: None)

    result = ai_utils.ask_studio_ai_with_history("claude_code", "claude/sonnet", "system", [])

    assert "Claude Code CLI is not available" in result
