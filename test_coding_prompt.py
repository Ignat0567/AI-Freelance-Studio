from order_workflow import coding_prompt
from order_workflow.coding_prompt import expand_coding_prompt, expansion_user_prompt


def test_expand_coding_prompt_keeps_original_when_grok_is_unavailable():
    original = "Build a pricing page with three tiers and WCAG AA contrast 4.5:1."

    def fail(_system, _user):
        return "Grok CLI is not available. Install it or run `grok login`."

    assert expand_coding_prompt(original, ask=fail) == original


def test_expand_coding_prompt_appends_original_as_authoritative():
    original = "Build a pricing page with three tiers and WCAG AA contrast 4.5:1."

    def ok(_system, user):
        assert "FILE:" in user or "file-by-file" in user.casefold()
        return "FILE: index.html\nPURPOSE: page\nMUST CONTAIN:\n- three pricing tiers"

    expanded = expand_coding_prompt(original, ask=ok)
    assert "FILE: index.html" in expanded
    assert "ORIGINAL TASK" in expanded
    assert original in expanded


def test_default_grok_ask_does_not_call_paid_xai_http_api(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "grok_bridge.ask_grok_cli",
        lambda system_prompt, user_prompt, model="", timeout=180: captured.update(model=model, timeout=timeout) or "FILE: index.html\nPURPOSE: page",
    )

    text = coding_prompt._default_grok_ask("system", "write the spec")

    assert text.startswith("FILE:")
    assert captured["model"] == "grok-4.6"
    assert "ask_studio_ai_with_history" not in coding_prompt._default_grok_ask.__code__.co_names


def test_one_file_task_tells_grok_to_list_only_index_html():
    original = "Build ONE self-contained file, index.html, at the project root. Exactly one HTML file."
    captured = {}

    def ok(_system, user):
        captured["user"] = user
        return "FILE: index.html\nPURPOSE: page\nMUST CONTAIN:\n- Mini Card"

    expand_coding_prompt(original, ask=ok)
    assert "FILE: index.html" in captured["user"]
    assert "exactly one file" in captured["user"].casefold()
    assert "Do not list package.json" in captured["user"]
    assert "FILE: index.html" in expansion_user_prompt(original)


def test_web_app_task_tells_grok_to_pin_vite_5_and_mount_app():
    original = "Implement ONLY the UI shell for this project: screens and navigation between them."
    prompt = expansion_user_prompt(original)

    assert "vite preview --host 127.0.0.1 --port 4173" in prompt
    assert "^5.3.1" in prompt
    assert "createRoot" in prompt
    assert "Do not list package.json" not in prompt
