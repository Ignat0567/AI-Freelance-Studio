import json
from pathlib import Path

import opencode_bridge


def _run_capture(command, timeout):
    assert timeout > 0
    if command[-1] == "--version":
        return 0, "1.17.11\n", ""
    if command[-2:] == ["auth", "list"]:
        return 0, "Credentials\nOpenAI oauth\nNVIDIA api\n", ""
    if command[-1] == "models":
        return 0, "openai/gpt-5.5\nnvidia/nemotron\n", ""
    raise AssertionError(command)


def _ready(monkeypatch, model="openai/gpt-5.5"):
    monkeypatch.setattr(opencode_bridge, "_run_capture", _run_capture)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda url: url == "http://127.0.0.1:45123")
    return opencode_bridge.test_opencode_readiness("opencode.cmd", model, "http://127.0.0.1:45123")


def test_readiness_gate_returns_separate_successful_checks(monkeypatch):
    result = _ready(monkeypatch)

    assert result["status"] == "ok"
    assert result["ready"] is True
    assert result["checks"]["server"]["status"] == "not_required"
    assert all(check["status"] == "passed" for name, check in result["checks"].items() if name != "server")
    assert result["checks"]["authentication"] == {"status": "passed", "providers": ["nvidia", "openai"], "auth_types": ["api", "oauth"]}
    assert result["checks"]["selection"]["provider"] == "openai"
    assert result["available_models"] == ["nvidia/nemotron", "openai/gpt-5.5"]


def test_readiness_gate_reports_not_installed(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: None)

    result = opencode_bridge.test_opencode_readiness(selected_model="openai/gpt-5.5")

    assert result["error_code"] == "opencode_not_installed"
    assert result["checks"]["executable"]["status"] == "failed"
    assert result["checks"]["server"]["status"] == "not_required"


def test_readiness_gate_does_not_require_server_for_cli_generation(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_run_capture", lambda *_args, **_kwargs: (0, "1.17.11", ""))
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: False)

    result = opencode_bridge.test_opencode_readiness("opencode.cmd", "openai/gpt-5.5")

    assert result["error_code"] != "server_not_running"
    assert result["checks"]["server"]["status"] == "not_required"
    assert result["checks"]["authentication"]["status"] == "failed"


def test_readiness_gate_reports_provider_not_authenticated(monkeypatch):
    def run(command, timeout):
        assert timeout > 0
        if command[-1] == "--version":
            return 0, "1.17.11", ""
        return 0, "Credentials\n", ""

    monkeypatch.setattr(opencode_bridge, "_run_capture", run)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: True)

    result = opencode_bridge.test_opencode_readiness("opencode.cmd", "openai/gpt-5.5", "http://127.0.0.1:45123")

    assert result["error_code"] == "provider_not_authenticated"


def test_readiness_gate_reports_models_unavailable(monkeypatch):
    def run(command, timeout):
        assert timeout > 0
        if command[-1] == "--version":
            return 0, "1.17.11", ""
        if command[-2:] == ["auth", "list"]:
            return 0, "OpenAI oauth", ""
        return 0, "", ""

    monkeypatch.setattr(opencode_bridge, "_run_capture", run)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: True)

    result = opencode_bridge.test_opencode_readiness("opencode.cmd", "openai/gpt-5.5", "http://127.0.0.1:45123")

    assert result["error_code"] == "models_unavailable"
    assert result["checks"]["authentication"]["status"] == "passed"


def test_readiness_gate_distinguishes_provider_and_model_selection(monkeypatch):
    provider_result = _ready(monkeypatch, "anthropic/claude-sonnet")
    model_result = _ready(monkeypatch, "openai/missing-model")

    assert provider_result["error_code"] == "selected_provider_not_authenticated"
    assert model_result["error_code"] == "selected_model_unavailable"


def test_readiness_gate_distinguishes_timeout_and_cli_failure(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_run_capture", lambda *_args, **_kwargs: (None, "", "timeout"))
    timeout = opencode_bridge.test_opencode_readiness("opencode.cmd", "openai/gpt-5.5")
    monkeypatch.setattr(opencode_bridge, "_run_capture", lambda *_args, **_kwargs: (2, "", "internal detail"))
    failed = opencode_bridge.test_opencode_readiness("opencode.cmd", "openai/gpt-5.5")

    assert timeout["error_code"] == "timeout"
    assert failed["error_code"] == "cli_command_failed"
    assert "internal detail" not in json.dumps(failed)


def test_auth_parser_returns_labels_without_raw_values():
    output = "Credentials\nOpenAI oauth\nNVIDIA api\nAPI_KEY=do-not-return\n"

    parsed = opencode_bridge._parse_opencode_auth_list(output)

    assert parsed == [{"provider": "openai", "auth_type": "oauth"}, {"provider": "nvidia", "auth_type": "api"}]
    assert "do-not-return" not in json.dumps(parsed)


def test_frontend_requires_successful_readiness_and_renders_check_statuses():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")

    assert "disabled={busy || !tested?.ready}" in source
    assert "A successful Test Connection is required before saving." in source
    assert "Object.entries(tested.checks)" in source
    assert "setTested(null)" in source
    assert "Repair automatically" in source
    assert "Local server" in source


def test_opencode_model_normalization_contract():
    native = opencode_bridge.normalize_opencode_model_id("nvidia/deepseek-ai/deepseek-v4-pro", "opencode_bridge", {"opencode_bridge"})
    legacy = opencode_bridge.normalize_opencode_model_id("opencode_bridge/nvidia/deepseek-ai/deepseek-v4-pro", "", {"opencode_bridge"})
    unknown = opencode_bridge.normalize_opencode_model_id("unknown/nvidia/deepseek-ai/deepseek-v4-pro", "", {"opencode_bridge"})

    assert native["model_id"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert native["migrated"] is False
    assert legacy["connection_id"] == "opencode_bridge"
    assert legacy["model_id"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert legacy["migrated"] is True
    assert unknown["model_id"] == "unknown/nvidia/deepseek-ai/deepseek-v4-pro"
    assert unknown["migrated"] is False
