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


def test_stream_event_text_keeps_assistant_text_and_drops_thoughts():
    assert grok_bridge.stream_event_text({"type": "text", "data": "<<<FILE index.html\n"}) == "<<<FILE index.html\n"
    assert grok_bridge.stream_event_text({"type": "thought", "data": "planning the scene"}) == ""
    assert grok_bridge.stream_event_text({"sessionUpdate": "agent_thought_chunk", "content": {"type": "thought", "text": "PCFSoftShadowMap"}}) == ""
    assert grok_bridge.stream_event_text({"type": "content_block_delta", "delta": {"text": "<html>"}}) == "<html>"
    assert grok_bridge.stream_event_text({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "body"}}) == "body"
    assert grok_bridge.stream_event_text({"params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "nested"}}}}) == "nested"


def test_ask_grok_cli_streams_text_events_to_on_chunk(monkeypatch, tmp_path):
    captured = {}

    def fake_stream(cmd, timeout, cwd=None, on_line=None, cancel_check=None, **_kwargs):
        captured["cmd"] = cmd
        for line in (
            json.dumps({"type": "thought", "data": "planning"}) + "\n",
            json.dumps({"sessionUpdate": "agent_thought_chunk", "content": {"type": "thought", "text": "PCF"}}) + "\n",
            json.dumps({"type": "text", "data": "<<<FILE index.html\n"}) + "\n",
            json.dumps({"type": "text", "data": "<html></html>\nFILE>>>\n"}) + "\n",
            json.dumps({"type": "end", "stopReason": "end_turn"}) + "\n",
        ):
            if on_line:
                on_line(line)
        return 0, "ignored", ""

    monkeypatch.setattr(grok_bridge, "_discover_grok", lambda explicit="": r"C:\Tools\grok.exe")
    monkeypatch.setattr(grok_bridge, "_run_capture_stream", fake_stream)
    seen = []
    result = grok_bridge.ask_grok_cli("system", "write files", on_chunk=seen.append)
    assert captured["cmd"][captured["cmd"].index("--output-format") + 1] == "streaming-json"
    assert "--leader-socket" in captured["cmd"]
    assert seen == ["<<<FILE index.html\n", "<html></html>\nFILE>>>\n"]
    assert result == "<<<FILE index.html\n<html></html>\nFILE>>>\n"


def test_ask_grok_cli_recovers_file_markers_from_thoughts_when_text_is_empty(monkeypatch):
    """The 2026-09-02 Alethia retry thought for 11 minutes and the only visible
    sentence was 'Building...'. The page lived in agent_thought_chunk events.
    """

    def fake_stream(cmd, timeout, cwd=None, on_line=None, cancel_check=None, **_kwargs):
        for line in (
            json.dumps({"sessionUpdate": "agent_thought_chunk", "content": {"type": "thought", "text": "<<<FILE index.html\n<html>ok</html>\nFILE>>>\n"}}) + "\n",
            json.dumps({"type": "text", "data": "Building a single self-contained index.html.\n"}) + "\n",
            json.dumps({"type": "end", "stopReason": "end_turn"}) + "\n",
        ):
            if on_line:
                on_line(line)
        return 0, "ignored", ""

    monkeypatch.setattr(grok_bridge, "_discover_grok", lambda explicit="": r"C:\Tools\grok.exe")
    monkeypatch.setattr(grok_bridge, "_run_capture_stream", fake_stream)
    seen = []
    result = grok_bridge.ask_grok_cli("system", "write files", on_chunk=seen.append)
    assert seen == ["Building a single self-contained index.html.\n"]
    assert "<<<FILE index.html" in result
    assert "<html>ok</html>" in result


def test_ask_grok_cli_uses_plan_mode_and_prompt_file(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, timeout, cwd=None, **_kwargs):
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


def test_ask_grok_cli_does_not_copy_index_html_into_scratch(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "index.html").write_text("<html>current</html>\n", encoding="utf-8")
    captured = {}

    def fake_run(cmd, timeout, cwd=None, **_kwargs):
        captured["cwd"] = cwd
        captured["has_index"] = bool(cwd) and (Path(cwd) / "index.html").is_file()
        return 0, json.dumps({"result": "ok"}), ""

    monkeypatch.setattr(grok_bridge, "_discover_grok", lambda explicit="": r"C:\Tools\grok.exe")
    monkeypatch.setattr(grok_bridge, "_run_capture", fake_run)
    grok_bridge.ask_grok_cli("system", "fix the page", working_directory=str(workspace))
    assert captured["has_index"] is False


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
