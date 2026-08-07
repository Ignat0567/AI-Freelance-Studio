from pathlib import Path

import opencode_provider


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
    assert result["error_category"] == "flag_rejected"
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
        return 0, '{"type":"step_finish","part":{"reason":"stop"}}', "", False, False

    monkeypatch.setattr(opencode_provider, "_run_owned_capture", run)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "success"
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["command"][-2:] == ["--dir", str(tmp_path.resolve())]
    assert result["cli_invocation"][-2:] == ["--dir", "<workspace>"]
    # Regression test: a real live run hung indefinitely (zero stdout for the full 900s
    # timeout, across two different models) because this flag was missing -- opencode run
    # blocks on an unanswerable interactive permission prompt without it, and this bridge
    # invocation has no TTY/stdin channel for a human to approve one.
    assert "--dangerously-skip-permissions" in captured["command"]


def test_non_workspace_bound_request_does_not_skip_permissions(monkeypatch):
    """The temp-workdir path (e.g. the product judge) isn't a code-writing session -- the
    auto-approve flag is scoped to owned-workspace invocations only, not applied blanket."""
    captured = {}
    connection = _connection(executable_path="opencode")

    def run(command, timeout, cwd=None):
        captured["command"] = command
        return 0, '{"type":"step_finish","part":{"reason":"stop"}}', ""

    monkeypatch.setattr(opencode_provider, "_run_capture", run)

    connection.execute({"user_content": "Judge this", "timeout": 5})

    assert "--dangerously-skip-permissions" not in captured["command"]


def test_timeout_is_not_request_rejection(monkeypatch):
    connection = _connection(executable_path="opencode")
    monkeypatch.setattr(opencode_provider, "_run_capture", lambda *_args, **_kwargs: (None, "", "timeout"))

    result = connection.execute({"user_content": "Describe this", "timeout": 5})

    assert result["error_category"] == "timeout"
    assert result["failure_stage"] == "model_execution"
    assert result["timeout"] is True


def test_timeout_without_meaningful_files_is_execution_timeout(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    (tmp_path / "execution_prompt.md").write_text("metadata", encoding="utf-8")

    def run_owned(command, timeout, cwd):
        return None, "", "token=secret-value timeout", True, True

    monkeypatch.setattr(opencode_provider, "_run_owned_capture", run_owned)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "error"
    assert result["classification"] == "opencode_timeout_without_artifact"
    assert result["timed_out"] is True
    assert result["files_detected"] is False
    assert result["meaningful_artifacts"] == []
    assert "secret-value" not in "\n".join(result["errors"])
    assert "secret-value" not in result["stderr_summary"]


def test_timeout_with_readme_is_generated_needs_review(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")

    def run_owned(command, timeout, cwd):
        Path(cwd, "README.md").write_text("Hello", encoding="utf-8")
        return None, "created", "timeout", True, False

    monkeypatch.setattr(opencode_provider, "_run_owned_capture", run_owned)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "partial"
    assert result["classification"] == "opencode_usable_but_nonterminating"
    assert result["files_detected"] is True
    assert result["meaningful_artifacts"] == ["README.md"]
    assert result["terminated_owned_process"] is True
    assert result["terminated_gracefully"] is False


def test_timeout_with_package_and_src_is_generated_needs_review(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")

    def run_owned(command, timeout, cwd):
        Path(cwd, "package.json").write_text("{}", encoding="utf-8")
        Path(cwd, "src").mkdir()
        return None, "", "timeout", True, True

    monkeypatch.setattr(opencode_provider, "_run_owned_capture", run_owned)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["classification"] == "opencode_usable_but_nonterminating"
    assert "package.json" in result["meaningful_artifacts"]
    assert "src/" in result["meaningful_artifacts"]


def test_nonzero_exit_with_files_requires_review_and_keeps_sanitized_error(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")

    def run_owned(command, timeout, cwd):
        Path(cwd, "README.md").write_text("Hello", encoding="utf-8")
        return 2, "", "failed token=secret-value", False, False

    monkeypatch.setattr(opencode_provider, "_run_owned_capture", run_owned)

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "partial"
    assert result["classification"] == "opencode_process_failed_with_artifacts"
    assert result["files_detected"] is True
    assert "secret-value" not in "\n".join(result["errors"])
    assert "secret-value" not in result["stderr_summary"]


def test_ndjson_success_stream_is_not_request_rejection(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    stdout = '\n'.join([
        '{"type":"session_start"}',
        '{"type":"step_start"}',
        '{"type":"step_finish","part":{"reason":"stop"}}',
    ])
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (0, stdout, "", False, False))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["status"] == "success"
    assert result["error_category"] == "" if "error_category" in result else True
    assert result["opencode_json_valid_lines"] == 3
    assert result["opencode_terminal_event"] is True


def test_multiple_json_objects_are_parsed_as_ndjson():
    stream = opencode_provider._parse_json_event_stream('{"type":"one"}\n{"type":"two"}\n')

    assert stream["valid_event_lines"] == 2
    assert stream["event_types"] == ["one", "two"]
    assert stream["non_json_line_count"] == 0


def test_explicit_request_rejection_is_the_only_request_rejection():
    assert opencode_provider._error_category("Error: request rejected by policy", "") == "request_rejected"
    assert opencode_provider._error_category("Error: unexpected server error", "") == "process_failed"


def test_unknown_model_maps_to_model_rejection():
    assert opencode_provider._error_category("Error: unknown model meta/llama-3.3-70b-instruct", "") == "model_rejected"


def test_provider_resource_exhaustion_maps_to_provider_error():
    assert opencode_provider._error_category('Error: "ResourceExhausted: Worker local total request limit reached (18/16)"', "") == "provider_error"


def test_provider_too_many_requests_maps_to_provider_error():
    stderr = 'message="stream error" providerID=nvidia modelID=deepseek-ai/deepseek-v4-pro error.error="AI_APICallError: Too Many Requests"'

    assert opencode_provider._error_category(stderr, "") == "provider_error"
    diagnostics = opencode_provider._provider_error_diagnostics(stderr, "")
    assert diagnostics["provider_id"] == "nvidia"
    assert diagnostics["model_id"] == "deepseek-ai/deepseek-v4-pro"
    assert diagnostics["provider_error"] is True


def test_provider_retry_error_maps_to_provider_error():
    stderr = 'error.error="AI_RetryError: Failed after 3 attempts. Last error: Too Many Requests" providerID=nvidia modelID=deepseek-ai/deepseek-v4-pro'

    assert opencode_provider._error_category(stderr, "") == "provider_error"
    assert opencode_provider._provider_error_diagnostics(stderr, "")["retry_observed"] is True


def test_rate_limit_and_quota_messages_map_to_provider_error():
    assert opencode_provider._error_category("provider rate limit reached", "") == "provider_error"
    assert opencode_provider._error_category("quota exceeded for provider", "") == "provider_error"


def test_artifact_validation_failure_is_not_request_rejection(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    Path(tmp_path, "README.md").write_text("wrong", encoding="utf-8")
    stdout = '{"type":"step_finish","part":{"reason":"stop"}}'
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (0, stdout, "", False, False))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5, "expected_artifact_path": "README.md", "expected_artifact_text": "right"})

    assert result["classification"] == "opencode_artifact_validation_failed"
    assert result["error_category"] == "artifact_validation_failed"


def test_missing_terminal_event_is_json_stream_failure(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    stdout = '{"type":"step_start"}\n{"type":"step_finish","part":{"reason":"tool-calls"}}'
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (0, stdout, "", False, False))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["classification"] == "opencode_json_stream_failure"
    assert result["error_category"] == "json_stream_failure"


def test_timeout_without_artifact_is_not_request_rejection(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (None, "", "timeout", True, True))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["classification"] == "opencode_timeout_without_artifact"


def test_timeout_with_provider_rate_limit_stderr_maps_to_provider_error(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    stderr = 'message="stream error" providerID=nvidia modelID=deepseek-ai/deepseek-v4-pro error.error="AI_APICallError: Too Many Requests"'
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (None, "", stderr, True, True))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["classification"] == "provider_error"
    assert result["error_category"] == "provider_error"
    assert result["provider_id"] == "nvidia"
    assert result["model_id"] == "deepseek-ai/deepseek-v4-pro"
    assert result["timed_out"] is True


def test_empty_timeout_without_artifact_remains_timeout_without_artifact(monkeypatch, tmp_path):
    connection = _connection(executable_path="opencode")
    monkeypatch.setattr(opencode_provider, "_run_owned_capture", lambda *_args, **_kwargs: (None, "", "", True, True))

    result = connection.execute({"user_content": "Build this", "workspace_path": str(tmp_path), "timeout": 5})

    assert result["classification"] == "opencode_timeout_without_artifact"
    assert result["error_category"] == "timeout"


def test_unsupported_flag_maps_to_flag_rejection():
    assert opencode_provider._error_category("Error: unknown option --auto", "") == "flag_rejected"
