import urllib.error
from pathlib import Path

import pytest

from order_workflow.claude_code_client import select_coding_execution_client
from order_workflow.ollama_code_client import (
    ConfiguredOllamaExecutionClient,
    coder_preamble_for_spec,
    parse_emitted_files,
    select_ollama_coding_model,
    write_emitted_files,
)


def test_select_ollama_coding_model_prefers_qwen_14b_and_skips_base():
    installed = (
        "dolphin-mistral:7b-v2.8-q4_K_M",
        "qwen2.5-coder:1.5b-base",
        "starcoder2:3b",
        "qwen2.5-coder:14b",
    )
    assert select_ollama_coding_model(installed) == "qwen2.5-coder:14b"
    assert select_ollama_coding_model(installed, requested="qwen2.5-coder:14b") == "qwen2.5-coder:14b"


def test_coding_paths_from_partial_include_an_unfinished_file():
    from order_workflow.ollama_code_client import coding_paths_from_partial

    text = "noise\n<<<FILE index.html\n<!DOCTYPE html><html>"
    assert coding_paths_from_partial(text) == ("index.html",)
    assert coding_paths_from_partial("<<<FILE index.\n<<<FILE index.html\n") == ("index.html",)


def test_ollama_stream_forwards_chunks_to_the_callback(monkeypatch):
    from order_workflow.ollama_code_client import generate_ollama_completion

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(
                [
                    b'{"response":"<<<FILE index.html\\n"}\n',
                    b'{"response":"<html></html>\\nFILE>>>","done":true}\n',
                ]
            )

    monkeypatch.setattr("order_workflow.ollama_code_client.urllib.request.urlopen", lambda *_args, **_kwargs: _FakeResponse())
    seen = []
    text = generate_ollama_completion("prompt", model="qwen2.5-coder:14b", on_chunk=seen.append)
    assert "".join(seen) == text
    assert "<<<FILE index.html" in text


def test_ollama_retries_once_after_a_cold_start_500(monkeypatch):
    """Found live 2026-09-03: the first call after a backend restart got HTTP 500 while the
    model was still loading, twice in one bench sequence, both recovered on a bare retry a
    few seconds later. See OLLAMA_COLD_START_RETRY_DELAY_SECONDS."""
    from order_workflow.ollama_code_client import generate_ollama_completion

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter([b'{"response":"<<<FILE index.html\\nok\\nFILE>>>","done":true}\n'])

    calls: list[int] = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("http://ollama/api/generate", 500, "Internal Server Error", None, None)
        return _FakeResponse()

    monkeypatch.setattr("order_workflow.ollama_code_client.urllib.request.urlopen", fake_urlopen)
    slept: list[float] = []
    text = generate_ollama_completion("prompt", model="qwen2.5-coder:14b", sleeper=slept.append)

    assert len(calls) == 2, "expected exactly one retry, not zero and not a retry loop"
    assert slept == [8.0], "the retry must wait for the model to finish loading, not fire immediately"
    assert "<<<FILE index.html" in text


def test_ollama_gives_up_after_a_second_500(monkeypatch):
    """The retry is a one-shot: a genuinely dead Ollama must still fail, not loop forever."""
    from order_workflow.ollama_code_client import generate_ollama_completion

    calls: list[int] = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("http://ollama/api/generate", 500, "Internal Server Error", None, None)

    monkeypatch.setattr("order_workflow.ollama_code_client.urllib.request.urlopen", fake_urlopen)
    slept: list[float] = []
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        generate_ollama_completion("prompt", model="qwen2.5-coder:14b", sleeper=slept.append)

    assert excinfo.value.code == 500
    assert len(calls) == 2, "one original attempt plus exactly one retry, then give up"
    assert slept == [8.0]


def test_ollama_does_not_retry_a_non_500_error(monkeypatch):
    """A 500 means the model is loading; any other status is a different, real problem
    (bad request, not found, ...) that a retry would not fix -- fail fast, no delay spent."""
    from order_workflow.ollama_code_client import generate_ollama_completion

    calls: list[int] = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("http://ollama/api/generate", 400, "Bad Request", None, None)

    monkeypatch.setattr("order_workflow.ollama_code_client.urllib.request.urlopen", fake_urlopen)
    slept: list[float] = []
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        generate_ollama_completion("prompt", model="qwen2.5-coder:14b", sleeper=slept.append)

    assert excinfo.value.code == 400
    assert len(calls) == 1, "a non-500 must not be retried"
    assert slept == []


def test_parse_and_write_file_markers_stay_inside_the_workspace(tmp_path):
    text = (
        "noise\n"
        "<<<FILE src/App.jsx\n"
        "export default function App() { return <h1>Hi</h1>; }\n"
        "FILE>>>\n"
        "<<<FILE ../escape.txt\n"
        "nope\n"
        "FILE>>>\n"
        "<<<FILE C:/Windows/bad.js\n"
        "nope\n"
        "FILE>>>\n"
    )
    parsed = parse_emitted_files(text)
    written = write_emitted_files(tmp_path, parsed)
    assert "src/App.jsx" in written
    assert (tmp_path / "src" / "App.jsx").read_text(encoding="utf-8").startswith("export default")
    assert not (tmp_path / "escape.txt").exists()


def test_select_coding_execution_client_honors_saved_grok_choice(monkeypatch):
    from order_workflow.grok_code_client import ConfiguredGrokExecutionClient

    class Ready:
        ready = True

    monkeypatch.setattr("order_workflow.ollama_code_client.ConfiguredOllamaExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.claude_code_client.ConfiguredClaudeCodeExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.service.ConfiguredOpenCodeExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.openrouter_code_client.ConfiguredOpenRouterExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setenv("FREELANCERSTUDIO_CODING_BACKEND", "grok")

    client = select_coding_execution_client()
    assert isinstance(client, ConfiguredGrokExecutionClient)


def test_select_coding_execution_client_prefers_ollama_over_claude(monkeypatch):
    import system_settings

    class Ready:
        ready = True

    class Blocked:
        ready = False

    monkeypatch.setattr("order_workflow.ollama_code_client.ConfiguredOllamaExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.setattr("order_workflow.claude_code_client.ConfiguredClaudeCodeExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.setattr("order_workflow.service.ConfiguredOpenCodeExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.delenv("FREELANCERSTUDIO_CODING_BACKEND", raising=False)
    monkeypatch.setitem(system_settings.SYSTEM_SETTINGS, "coding_backend", "")

    client = select_coding_execution_client()
    assert isinstance(client, ConfiguredOllamaExecutionClient)


def test_active_repair_backend_defaults_to_the_build_backend(monkeypatch):
    """Empty repair_backend (nobody has touched the new setting) must resolve to exactly
    what active_coding_backend() already returns -- every existing setup is unaffected."""
    from order_workflow.claude_code_client import active_repair_backend
    import system_settings

    class Ready:
        ready = True

    monkeypatch.setattr("order_workflow.ollama_code_client.ConfiguredOllamaExecutionClient.check_readiness", lambda self: Ready())
    monkeypatch.delenv("FREELANCERSTUDIO_CODING_BACKEND", raising=False)
    monkeypatch.delenv("FREELANCERSTUDIO_REPAIR_BACKEND", raising=False)
    monkeypatch.setitem(system_settings.SYSTEM_SETTINGS, "coding_backend", "")
    monkeypatch.setitem(system_settings.SYSTEM_SETTINGS, "repair_backend", "")

    assert active_repair_backend() == "ollama"


def test_active_repair_backend_pin_bypasses_readiness_like_coding_backend(monkeypatch):
    """A pinned repair_backend must win outright even when every worker reports blocked --
    the same rule coding_backend's own pin already relies on, and for the same reason
    (Grok's and Claude's readiness checks only detect a CLI login, not real quota)."""
    from order_workflow.claude_code_client import active_repair_backend
    import system_settings

    class Blocked:
        ready = False

    monkeypatch.setattr("order_workflow.ollama_code_client.ConfiguredOllamaExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.setattr("order_workflow.claude_code_client.ConfiguredClaudeCodeExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.setattr("order_workflow.service.ConfiguredOpenCodeExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.setattr("order_workflow.grok_code_client.ConfiguredGrokExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.setattr("order_workflow.openrouter_code_client.ConfiguredOpenRouterExecutionClient.check_readiness", lambda self: Blocked())
    monkeypatch.delenv("FREELANCERSTUDIO_REPAIR_BACKEND", raising=False)
    monkeypatch.setitem(system_settings.SYSTEM_SETTINGS, "repair_backend", "claude_code")

    assert active_repair_backend() == "claude_code"


def test_select_coding_execution_client_with_explicit_backend_skips_active_resolution(monkeypatch):
    """The repair-client construction path (service.py) passes an already-resolved backend
    string in -- this must dispatch directly to the matching client class without a second
    readiness pass through active_coding_backend()."""
    from order_workflow.claude_code_client import select_coding_execution_client
    from order_workflow.grok_code_client import ConfiguredGrokExecutionClient

    client = select_coding_execution_client(backend="grok")
    assert isinstance(client, ConfiguredGrokExecutionClient)


def test_one_file_spec_preamble_forbids_extra_paths():
    spec = "Build ONE self-contained file, index.html, at the project root."
    preamble = coder_preamble_for_spec(spec)
    assert "<<<FILE index.html" in preamble
    assert "Do not emit src/" in preamble


def test_multi_file_spec_keeps_generic_preamble():
    spec = "Build a React app with package.json and src/App.jsx"
    preamble = coder_preamble_for_spec(spec)
    assert "Do not emit src/" not in preamble
