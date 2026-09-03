from pathlib import Path

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


def test_one_file_spec_preamble_forbids_extra_paths():
    spec = "Build ONE self-contained file, index.html, at the project root."
    preamble = coder_preamble_for_spec(spec)
    assert "<<<FILE index.html" in preamble
    assert "Do not emit src/" in preamble


def test_multi_file_spec_keeps_generic_preamble():
    spec = "Build a React app with package.json and src/App.jsx"
    preamble = coder_preamble_for_spec(spec)
    assert "Do not emit src/" not in preamble
