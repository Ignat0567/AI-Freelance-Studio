import json
from pathlib import Path

import opencode_provider
import product_judge


def _connection(**overrides):
    value = {"connection_id": "oc-test", "name": "OpenCode test", "configured_model": "openai/gpt-5.5"}
    value.update(overrides)
    return opencode_provider.OpenCodeBridgeConnection.from_dict(value)


def test_connection_never_has_an_opencode_token_field():
    serialized = _connection().to_dict()
    assert serialized["connection_type"] == "opencode_bridge"
    assert serialized["authentication_owner"] == "OpenCode"
    assert serialized["stores_authentication"] is False
    assert not any("token" in key or "cookie" in key or "secret" in key for key in serialized)


def test_image_capability_is_not_inferred_from_model_name():
    connection = _connection(configured_model="openai/gpt-5.5")
    report = connection.capability_report()
    assert report["image_input"]["status"] == "unknown"
    assert not opencode_provider.bridge_effective_capabilities(connection)["image_input"]


def test_effective_capability_requires_every_layer():
    assert not opencode_provider.effective_capabilities(
        {"image_input": "supported"}, {"image_input": "unsupported"}, {"image_input": "supported"},
    )["image_input"]
    assert opencode_provider.effective_capabilities(
        {"image_input": "supported"}, {"image_input": "supported"}, {"image_input": "supported"},
    )["image_input"]


def test_text_health_probe_distinguishes_authentication(monkeypatch):
    connection = _connection()
    monkeypatch.setattr(connection, "execute", lambda _request: {"status": "success", "text": "OPENCODE_BRIDGE_TEXT_OK"})
    monkeypatch.setattr(connection, "available_models", lambda: ["openai/gpt-5.5"])
    result = connection.test_connection()
    assert result["health_status"] == "available_authenticated"
    assert result["capabilities"]["text_input"]["status"] == "supported"


def test_image_probe_requires_marker_from_actual_response(monkeypatch, tmp_path):
    image = tmp_path / "probe.png"
    image.write_bytes(b"image")
    connection = _connection()
    monkeypatch.setattr(connection, "execute", lambda _request: {"status": "success", "text": "I cannot inspect attachments"})
    result = connection.probe_image(str(image), "OPENCODE-BRIDGE-VISION-7391")
    assert result["capabilities"]["image_input"]["status"] == "unsupported"
    assert not result["effective_image_input"]


def test_single_image_proof_does_not_claim_multi_image_support(monkeypatch, tmp_path):
    image = tmp_path / "probe.png"
    image.write_bytes(b"image")
    connection = _connection()
    monkeypatch.setattr(connection, "execute", lambda _request: {"status": "success", "text": "OPENCODE-BRIDGE-VISION-7391"})

    result = connection.probe_image(str(image), "OPENCODE-BRIDGE-VISION-7391")

    assert result["capabilities"]["single_image_input"]["status"] == "supported"
    assert result["capabilities"]["multi_image_input"]["status"] == "unknown"


def test_failed_request_preserves_safe_cli_diagnostics(monkeypatch, tmp_path):
    image = tmp_path / "current screenshot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    connection = _connection(executable_path="opencode")
    monkeypatch.setattr(opencode_provider, "_run_capture", lambda *_args, **_kwargs: (2, "", "error: unknown option --file token=secret-value"))

    result = connection.execute({"user_content": "Describe this", "image_attachments": [str(image)], "timeout": 5})

    assert result["failure_stage"] == "cli_process_exit"
    assert result["error_category"] == "cli_argument_parsing"
    assert result["exit_code"] == 2
    assert result["attachment_count"] == 1
    assert result["attachment_metadata"][0]["format"] == "png"
    assert "secret-value" not in result["stderr_summary"]


def test_workspace_bound_request_runs_in_owned_workspace_with_dir_argument(monkeypatch, tmp_path):
    captured = {}
    connection = _connection(executable_path="opencode")

    def run(command, timeout, cwd=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["cwd"] = cwd
        return 0, '{"part":{"type":"text","text":"ok"}}', ""

    monkeypatch.setattr(opencode_provider, "_run_capture", run)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "success"
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["command"][-2:] == ["--dir", str(tmp_path.resolve())]
    assert result["cli_invocation"][-2:] == ["--dir", "<workspace>"]


def test_timeout_is_not_request_rejection(monkeypatch):
    connection = _connection(executable_path="opencode")
    monkeypatch.setattr(opencode_provider, "_run_capture", lambda *_args, **_kwargs: (None, "", "timeout"))

    result = connection.execute({"user_content": "Describe this", "timeout": 5})

    assert result["error_category"] == "timeout"
    assert result["failure_stage"] == "model_execution"
    assert result["timeout"] is True


def test_text_only_bridge_is_rejected_for_product_judge(tmp_path):
    shots = []
    for category, dimensions in (("laptop", "1366x768"), ("full_hd_desktop", "1920x1080"), ("high_resolution_desktop", "2560x1440"), ("tablet_portrait", "768x1024")):
        path = tmp_path / f"judge_{category}_{dimensions}.png"
        path.write_bytes(b"image")
        shots.append(str(path))
    result = product_judge.run_product_judge(
        {"id": "AC-X", "title": "Modern tidy visual design"}, {"passed": True, "viewport_results": []}, shots,
        {"title": "Demo", "target_path": str(tmp_path)}, {}, "snapshot", {"enabled": True, "provider": "opencode_bridge", "model": "openai/gpt-5.5", "connection": _connection().to_dict()},
    )
    assert result["verdict"] == "insufficient_evidence"
    assert "cannot currently transport images" in result["reason"]


def test_product_judge_uses_fresh_read_only_bridge_request(monkeypatch, tmp_path):
    shots = []
    for category, dimensions in (("laptop", "1366x768"), ("full_hd_desktop", "1920x1080"), ("high_resolution_desktop", "2560x1440"), ("tablet_portrait", "768x1024")):
        path = tmp_path / f"judge_{category}_{dimensions}.png"
        path.write_bytes(b"image")
        shots.append(str(path))
    captured = {}
    connection = _connection(capabilities={"image_input": {"status": "supported", "evidence": "real probe"}})
    monkeypatch.setattr(product_judge, "OpenCodeBridgeConnection", type("FakeConnection", (), {"from_dict": staticmethod(lambda _value: connection)}))
    monkeypatch.setattr(product_judge, "bridge_effective_capabilities", lambda _connection: {"image_input": True})
    monkeypatch.setattr(connection, "execute", lambda request: captured.update(request) or {"status": "success", "text": json.dumps({"verdict": "approved", "findings": []})})
    result = product_judge.run_product_judge(
        {"id": "AC-X", "title": "Modern tidy visual design"}, {"passed": True, "viewport_results": []}, shots,
        {"title": "Demo", "target_path": str(tmp_path)}, {}, "snapshot", {"enabled": True, "provider": "opencode_bridge", "model": "openai/gpt-5.5", "connection": connection.to_dict()},
    )
    assert result["verdict"] == "approved"
    assert captured["session_id"] == "fresh-product-judge-session"
    assert "repair" not in captured["system_instruction"].lower()
