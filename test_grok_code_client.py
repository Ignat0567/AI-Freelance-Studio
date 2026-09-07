"""build_second_opinion(): a report-only extra read of an already-delivered,
already-QA-passed project. Every failure mode here must degrade to None rather
than raise -- _finalize_success() calls this after every gate has already passed,
and a successful delivery must never turn into a failed one over this.
"""

from __future__ import annotations

import pytest

from order_workflow.grok_code_client import (
    _SECOND_OPINION_CONTENT_CAP,
    build_second_opinion,
)


def _ready_payload():
    return {"ready": True}


def _not_ready_payload():
    return {"ready": False}


def test_second_opinion_skips_when_grok_is_not_ready(tmp_path, monkeypatch):
    (tmp_path / "App.jsx").write_text("export default function App() {}\n", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ask_grok_cli must not be called when Grok is not ready")

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _not_ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fail_if_called)

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("App.jsx",))

    assert result is None


def test_second_opinion_embeds_file_contents_with_boundaries(tmp_path, monkeypatch):
    (tmp_path / "App.jsx").write_text("export default function App() { return null; }\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "app"}\n', encoding="utf-8")
    captured = {}

    def fake_ask(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return "- Looks fine."

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fake_ask)

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("App.jsx", "package.json"))

    assert result == "- Looks fine."
    assert "--- App.jsx ---" in captured["user"]
    assert "export default function App" in captured["user"]
    assert "--- package.json ---" in captured["user"]
    assert '"name": "app"' in captured["user"]
    assert "A tip calculator" in captured["user"]
    assert "advisory" in captured["system"].lower()


def test_second_opinion_skips_missing_and_empty_files_without_calling_grok(tmp_path, monkeypatch):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ask_grok_cli must not be called when nothing readable was found")

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fail_if_called)

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("does-not-exist.txt", "empty.txt"))

    assert result is None


def test_second_opinion_truncates_content_over_the_review_budget(tmp_path, monkeypatch):
    big = "x" * (_SECOND_OPINION_CONTENT_CAP - 200)
    (tmp_path / "first.txt").write_text(big, encoding="utf-8")
    (tmp_path / "second.txt").write_text("y" * 5000, encoding="utf-8")
    captured = {}

    def fake_ask(system, user, **kwargs):
        captured["user"] = user
        return "- Findings."

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", fake_ask)

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("first.txt", "second.txt"))

    assert result == "- Findings."
    assert "--- first.txt ---" in captured["user"]
    assert "y" * 5000 not in captured["user"], "the second file must not fit once the budget is spent"
    assert "omitted" in captured["user"]


def test_second_opinion_returns_none_on_a_grok_cli_error_string(tmp_path, monkeypatch):
    (tmp_path / "App.jsx").write_text("export default function App() {}\n", encoding="utf-8")

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", lambda *a, **k: "Grok CLI error: nonzero exit")

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("App.jsx",))

    assert result is None


def test_second_opinion_returns_none_on_a_grok_cli_timeout_string(tmp_path, monkeypatch):
    (tmp_path / "App.jsx").write_text("export default function App() {}\n", encoding="utf-8")

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", lambda *a, **k: "Grok CLI timed out.")

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("App.jsx",))

    assert result is None


def test_second_opinion_returns_none_when_ask_grok_cli_raises(tmp_path, monkeypatch):
    (tmp_path / "App.jsx").write_text("export default function App() {}\n", encoding="utf-8")

    def raise_it(*_args, **_kwargs):
        raise RuntimeError("subprocess exploded")

    monkeypatch.setattr("grok_bridge.test_grok_readiness", _ready_payload)
    monkeypatch.setattr("grok_bridge.ask_grok_cli", raise_it)

    result = build_second_opinion(tmp_path, goal="A tip calculator", files=("App.jsx",))

    assert result is None
