import json
from pathlib import Path

import grok_bridge
import main
from test_security_support import authorized_test_client
import config_storage


def test_normalize_grok_model_id_strips_studio_prefixes():
    assert grok_bridge.normalize_grok_model_id("grok/grok-4.6") == "grok-4.6"
    assert grok_bridge.normalize_grok_model_id("xai/grok-4.5") == "grok-4.5"
    assert grok_bridge.normalize_grok_model_id("grok/default") == grok_bridge.DEFAULT_GROK_MODEL
    assert grok_bridge.normalize_grok_model_id("") == grok_bridge.DEFAULT_GROK_MODEL


def test_extract_grok_text_accepts_result_and_nested_content():
    assert grok_bridge.extract_grok_text(json.dumps({"result": "hello"})) == "hello"
    assert grok_bridge.extract_grok_text(json.dumps({"message": {"content": "nested"}})) == "nested"
    assert grok_bridge.extract_grok_text("plain text") == "plain text"


def test_ask_grok_cli_uses_plan_mode_and_prompt_file(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, timeout, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return 0, json.dumps({"result": "spec-ok"}), ""

    monkeypatch.setattr(grok_bridge, "_discover_grok", lambda explicit="": r"C:\Tools\grok.exe")
    monkeypatch.setattr(grok_bridge, "_run_capture", fake_run)

    result = grok_bridge.ask_grok_cli("system", "write the brief", model="grok/grok-4.6")

    assert result == "spec-ok"
    assert captured["cmd"][0] == r"C:\Tools\grok.exe"
    assert "--prompt-file" in captured["cmd"]
    assert "--permission-mode" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--permission-mode") + 1] == "plan"
    assert "-m" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "grok-4.6"
    assert "--always-approve" not in captured["cmd"]


def _client(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    return authorized_test_client(main.app), config_path


def test_grok_detect_and_save_use_cli_owned_login(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        main,
        "get_grok_onboarding_dependencies",
        lambda path="": {
            "components": {"grok": {"installed": True, "version": "1.0.0", "path": r"C:\Tools\grok.exe", "path_refresh_recommended": False}},
            "restart_recommended": False,
            "restart_message": "",
        },
    )
    monkeypatch.setattr(
        main,
        "test_grok_readiness",
        lambda path="": {
            "ready": True,
            "error_code": "",
            "message": "Grok CLI is installed and logged in.",
            "executable_path": r"C:\Tools\grok.exe",
            "account": "You are logged in with grok.com.",
        },
    )

    detected = client.post("/api/provider-connections/grok/detect", json={}).json()
    assert detected["status"] == "detected"
    assert detected["executable_path"] == r"C:\Tools\grok.exe"

    saved = client.post("/api/provider-connections/grok", json={"configured_model": "grok/grok-4.6"}).json()
    assert saved["status"] == "saved"
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["_system"]["global_provider"] == "grok"
    assert on_disk["_global_ai"]["connection_type"] == "grok_subscription"
    assert "xai_key" not in on_disk
    assert "api_key" not in json.dumps(saved).casefold()
