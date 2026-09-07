"""Per-run coding provider/model choice is saved and offered again on the next launch."""

from __future__ import annotations

from order_workflow.api_models import StartExecutionRequest
from order_workflow.service import OrderWorkflowService


def test_start_execution_request_keeps_automated_callers_unspecified():
    payload = StartExecutionRequest(mode="production", live=True)
    assert payload.attended is False
    assert payload.coding_backend is None
    assert payload.connection_id is None
    assert payload.model is None


def test_start_execution_request_accepts_a_saved_choice():
    payload = StartExecutionRequest(
        mode="production",
        live=True,
        attended=True,
        coding_backend="claude_code",
        connection_id="claude-subscription",
        model="claude/sonnet",
    )
    assert payload.coding_backend == "claude_code"
    assert payload.connection_id == "claude-subscription"
    assert payload.model == "claude/sonnet"


def test_remember_execution_choice_pins_the_coding_backend(monkeypatch):
    import system_settings

    monkeypatch.delenv("FREELANCERSTUDIO_CODING_BACKEND", raising=False)
    monkeypatch.setitem(system_settings.SYSTEM_SETTINGS, "coding_backend", "")
    saved = {}

    def fake_update(payload):
        saved.update(payload)
        system_settings.SYSTEM_SETTINGS["coding_backend"] = payload["coding_backend"]
        return {"status": "saved"}

    monkeypatch.setattr("system_settings.update_system_config", fake_update)
    OrderWorkflowService._remember_execution_choice(
        coding_backend="grok",
        connection_id=None,
        model=None,
    )
    assert saved["coding_backend"] == "grok"
    assert system_settings.SYSTEM_SETTINGS["coding_backend"] == "grok"
    import os

    assert os.environ.get("FREELANCERSTUDIO_CODING_BACKEND") == "grok"


def test_grok_client_forwards_streamed_chunks_into_the_coding_pane(tmp_path, monkeypatch):
    from order_workflow.executors import CancellationToken
    from order_workflow.grok_code_client import ConfiguredGrokExecutionClient

    class Ready:
        ready = True

    class _Sink:
        def __init__(self):
            self.chunks = []

        def emit(self, **_kwargs):
            pass

        def coding(self, chunk, *, agent="", files=()):
            self.chunks.append(chunk)

    def fake_ask(system, user, **kwargs):
        on_chunk = kwargs.get("on_chunk")
        assert kwargs["model"] != "opus"
        if on_chunk:
            on_chunk("<<<FILE index.html\n")
            on_chunk("<html></html>\nFILE>>>\n")
        return "<<<FILE index.html\n<html></html>\nFILE>>>\n"

    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fake_ask)
    sink = _Sink()
    result = ConfiguredGrokExecutionClient().execute_project_prompt(
        "Build ONE self-contained file, index.html, at the project root.",
        tmp_path,
        sink,
        CancellationToken(),
        model="opus",
    )
    assert result.success is True
    assert sink.chunks == ["<<<FILE index.html\n", "<html></html>\nFILE>>>\n"]
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<html>")


def test_grok_client_ignores_claude_phase_model_ids():
    from order_workflow.grok_code_client import _grok_model_id
    import grok_bridge

    assert _grok_model_id("opus") == grok_bridge.DEFAULT_GROK_MODEL
    assert _grok_model_id("claude/sonnet") == grok_bridge.DEFAULT_GROK_MODEL
    assert _grok_model_id("grok/grok-4.6") == "grok/grok-4.6"


def test_grok_client_recovers_a_bare_html_document():
    from order_workflow.grok_code_client import _html_from_loose_output

    html = _html_from_loose_output("Sure.\n<!DOCTYPE html><html><body><canvas></canvas></body></html>\n")
    assert html.startswith("<!DOCTYPE html>")
    fenced = _html_from_loose_output("```html\n<!DOCTYPE html><html></html>\n```\n")
    assert "<html></html>" in fenced


def test_grok_repair_prompt_includes_the_existing_index(tmp_path, monkeypatch):
    """Live Alethia repair of 2026-09-01: Grok's cwd was an empty scratch (prompt.txt
    only), so it talked about finding the project instead of emitting FILE markers.
    A repair must see the current page in the prompt and still be told to emit one file.
    """
    from order_workflow.executors import CancellationToken
    from order_workflow.grok_code_client import ConfiguredGrokExecutionClient

    class Ready:
        ready = True

    class _Sink:
        def emit(self, **_kwargs):
            pass

        def coding(self, chunk, *, agent="", files=()):
            pass

    (tmp_path / "index.html").write_text("<html>THREE.P CFSoftShadowMap</html>\n", encoding="utf-8")
    captured = {}

    def fake_ask(system, user, **kwargs):
        captured["user"] = user
        captured["working_directory"] = kwargs.get("working_directory")
        return "<<<FILE index.html\n<html>fixed</html>\nFILE>>>\n"

    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fake_ask)
    result = ConfiguredGrokExecutionClient().execute_project_prompt(
        "A check that has to pass before this project can be delivered is failing. Fix the code so it passes.\nUnexpected identifier 'CFSoftShadowMap'",
        tmp_path,
        _Sink(),
        CancellationToken(),
        model="opus",
    )
    assert result.success is True
    assert "THREE.P CFSoftShadowMap" in captured["user"]
    assert "<<<FILE index.html" in captured["user"]
    assert captured["working_directory"] == str(tmp_path)
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<html>fixed")


def test_grok_rebuild_prompt_does_not_carry_the_broken_page(tmp_path, monkeypatch):
    """A full static-page retry already has the brief. Stuffing the dead 64KB page
    into that prompt made Grok say 'I'll read the full prompt first' and exit.
    """
    from order_workflow.executors import CancellationToken
    from order_workflow.grok_code_client import ConfiguredGrokExecutionClient

    class Ready:
        ready = True

    class _Sink:
        def emit(self, **_kwargs):
            pass

        def coding(self, chunk, *, agent="", files=()):
            pass

    (tmp_path / "index.html").write_text("<html>THREE.P CFSoftShadowMap</html>\n", encoding="utf-8")
    captured = {}

    def fake_ask(system, user, **kwargs):
        captured["user"] = user
        captured["system"] = system
        return "<<<FILE index.html\n<html>fresh</html>\nFILE>>>\n"

    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fake_ask)
    result = ConfiguredGrokExecutionClient().execute_project_prompt(
        "Build ONE self-contained file, index.html, at the project root. Living WebGL forest.",
        tmp_path,
        _Sink(),
        CancellationToken(),
        model="opus",
    )
    assert result.success is True
    assert "THREE.P CFSoftShadowMap" not in captured["user"]
    assert captured["system"].startswith("You write complete project files")
    assert "first line of the reply must be <<<FILE" in captured["system"]
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<html>fresh")


def test_browser_client_sends_the_launch_choice():
    from pathlib import Path

    api = Path("frontend/src/features/order-workflow/orderWorkflowApi.js").read_text(encoding="utf-8")
    page = Path("frontend/src/features/order-workflow/OrderWorkflowPage.jsx").read_text(encoding="utf-8")
    dashboard = Path("frontend/src/features/order-workflow/ExecutionDashboard.jsx").read_text(encoding="utf-8")
    settings = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    assert "coding_backend: choice.coding_backend" in api
    assert "startExecution(state.order.id, 'production', true, choice)" in page
    assert "Who writes the project files" in dashboard
    assert "Default coding worker" in settings
    assert "id: 'openrouter'" in dashboard
    assert 'value="openrouter"' in settings
    assert "Who fixes QA failures" in settings
    assert "repair_backend" in settings
    assert "Independent second opinion after delivery" in settings
    assert "second_opinion_backend" in settings


def test_emit_coding_is_optional_on_plain_sinks():
    from order_workflow.executors import emit_coding

    class _Sink:
        def emit(self, **_kwargs):
            pass

    emit_coding(_Sink(), "<<<FILE index.html\n")


def test_execution_keeps_a_coding_transcript():
    from datetime import datetime, timezone, timedelta
    from order_workflow.models import ExecutionArtifact, ExecutionResult, ProjectExecution

    now = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)
    artifact = ExecutionArtifact(
        id="artifact_summary",
        execution_id="execution_fixed",
        kind="project_summary",
        name="Generated project summary",
        summary="Simulated project output for UI development.",
        reference="artifact-summary",
        simulated=True,
        created_at=now,
    )
    result = ExecutionResult(
        success=True,
        summary="Fake execution completed successfully.",
        artifact_ids=(artifact.id,),
        completed_at=now + timedelta(minutes=1),
    )
    execution = ProjectExecution(
        id="execution_fixed",
        order_id="order_fixed",
        brief_id="brief_fixed",
        handoff_id="handoff_fixed",
        mode="fake",
        status="succeeded",
        stage="completed",
        progress=100,
        current_activity="Execution completed",
        artifacts=(artifact,),
        result=result,
        coding_transcript="<<<FILE index.html\n<html></html>\nFILE>>>\n",
        coding_files=("index.html",),
        created_at=now,
        updated_at=now + timedelta(minutes=1),
        started_at=now,
        finished_at=now + timedelta(minutes=1),
    )
    payload = execution.to_dict()
    assert payload["coding_transcript"].startswith("<<<FILE index.html")
    assert payload["coding_files"] == ["index.html"]


def test_openrouter_is_a_coding_backend():
    from order_workflow.claude_code_client import CODING_BACKENDS

    assert "openrouter" in CODING_BACKENDS


def test_openrouter_client_ignores_studio_internal_model_ids():
    from order_workflow.openrouter_code_client import DEFAULT_OPENROUTER_MODEL, _openrouter_model_id

    assert _openrouter_model_id("opus") == DEFAULT_OPENROUTER_MODEL
    assert _openrouter_model_id("claude/sonnet") == DEFAULT_OPENROUTER_MODEL
    assert _openrouter_model_id("grok-4.6") == DEFAULT_OPENROUTER_MODEL
    assert _openrouter_model_id("qwen2.5-coder:14b") == DEFAULT_OPENROUTER_MODEL
    assert _openrouter_model_id("anthropic/claude-sonnet-4") == "anthropic/claude-sonnet-4"
    assert _openrouter_model_id("openrouter/openai/gpt-4o") == "openai/gpt-4o"


def test_openrouter_client_writes_file_markers(tmp_path):
    from order_workflow.executors import CancellationToken
    from order_workflow.openrouter_code_client import ConfiguredOpenRouterExecutionClient

    class _Sink:
        def emit(self, **kwargs):
            message = str(kwargs.get("message") or "")
            assert "sk-or-" not in message

    output = "<<<FILE index.html\n<!DOCTYPE html><html><body>ok</body></html>\nFILE>>>\n"

    def fake_complete(prompt, **kwargs):
        assert "<<<FILE" in prompt
        assert kwargs["model"] == "anthropic/claude-sonnet-4"
        assert kwargs["api_key"] == "sk-or-test-placeholder"
        return output

    client = ConfiguredOpenRouterExecutionClient(
        api_key_lookup=lambda: "sk-or-test-placeholder",
        completer=fake_complete,
    )
    result = client.execute_project_prompt(
        "Build ONE self-contained file, index.html, at the project root.",
        tmp_path,
        _Sink(),
        CancellationToken(),
        model="opus",
    )
    assert result.success is True
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<body>ok</body>" in html
    assert "sk-or-test-placeholder" not in html


def test_openrouter_stream_parser_joins_sse_deltas(monkeypatch):
    from order_workflow.openrouter_code_client import generate_openrouter_completion

    class _FakeResponse:
        def __init__(self, lines):
            self._lines = [line.encode("utf-8") for line in lines]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(self._lines)

    def fake_urlopen(request, timeout=0):
        assert request.full_url.endswith("/chat/completions")
        assert "openrouter.ai/api/v1" in request.full_url
        return _FakeResponse(
            [
                'data: {"choices":[{"delta":{"content":"<<<FILE index.html\\n"}}]}',
                'data: {"choices":[{"delta":{"content":"<html></html>\\nFILE>>>\\n"}}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr("order_workflow.openrouter_code_client.urllib.request.urlopen", fake_urlopen)
    text = generate_openrouter_completion(
        "prompt",
        model="anthropic/claude-sonnet-4",
        api_key="sk-or-test-placeholder",
        timeout=5,
    )
    assert "<<<FILE index.html" in text
    assert "FILE>>>" in text


def test_select_coding_execution_client_honors_saved_openrouter_choice(monkeypatch):
    from order_workflow.claude_code_client import select_coding_execution_client
    from order_workflow.openrouter_code_client import ConfiguredOpenRouterExecutionClient

    class Ready:
        ready = True

    monkeypatch.setattr("order_workflow.ollama_code_client.ConfiguredOllamaExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.claude_code_client.ConfiguredClaudeCodeExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.service.ConfiguredOpenCodeExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.openrouter_code_client.ConfiguredOpenRouterExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setenv("FREELANCERSTUDIO_CODING_BACKEND", "openrouter")

    client = select_coding_execution_client()
    assert isinstance(client, ConfiguredOpenRouterExecutionClient)
