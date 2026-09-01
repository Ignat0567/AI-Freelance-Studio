import os
import json
import subprocess
import sys
import config_storage
import secret_store

# Every provider offered in provider_config.AI_PROVIDER_MODELS must have an entry here.
# A missing entry is a credential-disclosure bug, not a cosmetic gap: the request is sent
# with Authorization: Bearer <that provider's key>, so routing it anywhere else hands the
# key to a third party that was never meant to receive it. See _resolve_base_url().
PROVIDERS_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    "deepseek": "https://api.deepseek.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    # Gemini's OpenAI-compatibility layer, so the shared f"{base_url}/chat/completions"
    # request shape and Bearer auth used by _ai_worker.py both apply unchanged.
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "xai": "https://api.x.ai/v1",
}

# Sampling support is explicitly filtered per transport instead of sending
# provider-specific fields to every model request.
PROVIDER_CAPABILITIES = {
    "openai": {"top_p": True, "top_k": False, "image_input": True},
    "anthropic": {"top_p": True, "top_k": False, "image_input": True},
    "nvidia": {"top_p": True, "top_k": True, "image_input": False},
    "groq": {"top_p": True, "top_k": False, "image_input": False},
    "mistral": {"top_p": True, "top_k": False, "image_input": False},
    "deepseek": {"top_p": True, "top_k": False, "image_input": False},
    "together": {"top_p": True, "top_k": True, "image_input": False},
    "ollama": {"top_p": True, "top_k": True, "image_input": False},
    # top_k is deliberately False: Gemini itself supports topK, but it is not an OpenAI
    # parameter and this provider is reached through the OpenAI-compatibility endpoint.
    "google": {"top_p": True, "top_k": False, "image_input": True},
    "xai": {"top_p": True, "top_k": False, "image_input": True},
}

_AI_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ai_worker.py")
_ANTHROPIC_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_anthropic_worker.py")


def get_api_key(provider: str) -> str:
    provider_lower = provider.lower()
    saved_keys = config_storage.load_studio_keys()
    if provider_lower == "ollama":
        return "ollama"
    return secret_store.get_secret(f"{provider_lower}_key", saved_keys)


def provider_capabilities(provider: str) -> dict:
    return dict(PROVIDER_CAPABILITIES.get(provider.lower(), {"top_p": False, "top_k": False, "image_input": False}))


def _resolve_base_url(provider_lower: str) -> str:
    """Return the endpoint for a provider, or "" if it has none.

    Fails closed on purpose. This previously defaulted to NVIDIA's endpoint for any
    unrecognized provider, which meant an unlisted provider's API key was transmitted to
    NVIDIA -- "google" was selectable in Settings and passed its own key-validation check,
    yet every actual request sent the Google key to NVIDIA. Returning "" lets the caller
    surface a clear error instead of silently misrouting a credential.
    """
    return PROVIDERS_URLS.get(provider_lower, "")


def _ask_claude_code_cli(system_prompt: str, chat_history: list) -> str:
    """One-shot text completion via the official Claude Code CLI, for callers (chat,
    presentations, proposals) that just want an answer string -- not a project workspace
    execution like order_workflow.claude_code_client.ConfiguredClaudeCodeExecutionClient
    handles. Reuses the same CLI-owned-auth and stdin-not-argv approach (a long prompt
    would otherwise blow cmd.exe's ~8191-char command-line limit on Windows)."""
    import claude_bridge

    binary = claude_bridge._discover_claude()
    if not binary:
        return "Claude Code CLI is not available. Install it or select a supported provider in Settings."

    last_user_msg = ""
    for message in reversed(chat_history):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
                break
    prompt = f"{system_prompt}\n\n{last_user_msg}" if last_user_msg else system_prompt

    try:
        proc = subprocess.Popen(
            [binary, "-p", "--output-format", "json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, stderr = proc.communicate(input=prompt, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Claude Code CLI timed out."
    except OSError as exc:
        return f"Failed to start Claude Code CLI: {exc}"

    if proc.returncode != 0:
        return f"Claude Code CLI error: {(stderr or stdout).strip()[:500]}"
    try:
        payload = json.loads(stdout)
    except ValueError:
        return stdout.strip()
    if payload.get("is_error"):
        return f"Claude Code CLI error: {payload.get('result', '')}"
    return str(payload.get("result") or "")


def ask_studio_ai_with_history(
    provider: str,
    model_name: str,
    system_prompt: str,
    chat_history: list,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    top_p: float | None = None,
    top_k: int | None = None,
) -> str:
    try:
        provider_lower = provider.lower()
        if provider_lower in {"opencode_bridge", "opencode"}:
            cfg = {}
            connections = []
            try:
                cfg = config_storage.load_studio_keys()
                connections = cfg.get("_provider_connections", [])
            except AttributeError:
                pass
            opencode_connections = [
                item for item in connections
                if isinstance(item, dict)
                and item.get("connection_type") in {"opencode_bridge", "opencode_oauth_bridge"}
                and item.get("enabled", True)
            ]
            connection_data = next((item for item in opencode_connections if not model_name or item.get("configured_model") == model_name or any(isinstance(m, dict) and m.get("id") == model_name for m in item.get("available_models", []))), None)
            if not connection_data and opencode_connections:
                # Direct OpenCode OAuth mode: the official CLI owns auth and can run any available
                # native provider/model even if the old saved connection still stores another model.
                connection_data = {**opencode_connections[0], "configured_model": model_name or opencode_connections[0].get("configured_model", "")}
            if not connection_data:
                return "OpenCode OAuth is not configured yet. Open Settings -> AI Provider and select Authenticate Provider."
            from opencode_provider import OpenCodeBridgeConnection
            text = "\n".join(str(message.get("content", "")) for message in chat_history if message.get("role") == "user")
            response = OpenCodeBridgeConnection.from_dict(connection_data).execute({"system_instruction": system_prompt, "user_content": text, "requested_model": model_name, "timeout": 300})
            if response.get("status") == "success":
                return response.get("text", "")
            return f"OpenCode bridge error: {response.get('error_category', 'request_failed')}"
        if provider_lower == "claude_code":
            return _ask_claude_code_cli(system_prompt, chat_history)
        if provider_lower in {"grok", "grok_cli", "xai"}:
            # Same grok.exe / grok.com subscription as PowerShell. Do not send these
            # providers to api.x.ai — that is a separate paid API product.
            last_user = ""
            for message in reversed(chat_history):
                if message.get("role") == "user":
                    content = message.get("content", "")
                    last_user = content if isinstance(content, str) else ""
                    break
            import grok_bridge
            return grok_bridge.ask_grok_cli(system_prompt, last_user, model=model_name)
        base_url = _resolve_base_url(provider_lower)
        if not base_url:
            return f"AI provider '{provider}' has no configured endpoint. Select a supported provider in Settings."
        api_key = get_api_key(provider)

        lang_hint = ""
        last_user_msg = ""
        for m in reversed(chat_history):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    last_user_msg = content
                elif isinstance(content, list):
                    last_user_msg = " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
                break
        if last_user_msg:
            cyrillic_chars = sum(1 for c in last_user_msg if '\u0400' <= c <= '\u04ff')
            if cyrillic_chars > len(last_user_msg) * 0.1:
                lang_hint = "\nIMPORTANT: The user writes in Russian. You MUST respond entirely in Russian language."

        capabilities = provider_capabilities(provider_lower)
        if provider_lower == "anthropic":
            return _ask_anthropic(base_url, api_key, model_name, system_prompt + lang_hint, chat_history, temperature, max_tokens, top_p if capabilities["top_p"] else None)

        url = f"{base_url}/chat/completions"
        messages = [{"role": "system", "content": system_prompt + lang_hint}]
        messages.extend(chat_history)

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None and capabilities["top_p"]:
            payload["top_p"] = top_p
        if top_k is not None and capabilities["top_k"]:
            payload["top_k"] = top_k

        payload_json = json.dumps(payload)

        result = subprocess.run(
            [sys.executable, _AI_WORKER, url, api_key],
            input=payload_json, capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )

        if result.returncode != 0:
            err = result.stderr.strip()
            stdout_err = result.stdout.strip()
            combined = (stdout_err + " " + err).lower()
            if "does not support image" in combined or "cannot read" in combined or "image input" in combined:
                return "This model only supports text. Please send text messages without images or files."
            if "timed out" in err.lower():
                return "AI service timed out. The API was slow. Please try again."
            if "Invalid API key" in err:
                return "Invalid API key. Please update your API key in Settings."
            return f"AI service error: {err[:500]}"

        output = result.stdout.strip()
        if not output:
            return "AI service returned empty response."

        data = json.loads(output)
        if "error" in data:
            err_msg = data["error"].get("message", str(data["error"]))
            if "does not support image" in err_msg.lower() or "cannot read" in err_msg.lower() or "image input" in err_msg.lower():
                return "This model only supports text. Please send text messages without images or files."
            return f"AI error: {err_msg}"
        return data["choices"][0]["message"]["content"].strip()

    except subprocess.TimeoutExpired:
        return "AI service timed out (120s). The API was slow. Please try again."
    except json.JSONDecodeError:
        return "AI service returned invalid response."
    except Exception as e:
        return f"AI response error: {str(e)[:100]}. Please try again later."


def _ask_anthropic(base_url, api_key, model_name, system_text, chat_history, temperature, max_tokens, top_p=None):
    """Convert OpenAI-format request to Anthropic Messages API and call via _anthropic_worker."""
    url = f"{base_url}/messages"

    # Anthropic: system prompt is a separate top-level field, messages have only user/assistant roles
    anthropic_messages = []
    for msg in chat_history:
        role = msg.get("role", "user")
        if role == "system":
            continue
        content = msg.get("content", "")
        if role == "user":
            anthropic_messages.append({"role": "user", "content": content})
        elif role == "assistant":
            anthropic_messages.append({"role": "assistant", "content": content})

    # If first message is not from user, prepend an empty user message
    if anthropic_messages and anthropic_messages[0]["role"] != "user":
        anthropic_messages.insert(0, {"role": "user", "content": "..."})

    payload = {
        "model": model_name,
        "max_tokens": max_tokens if max_tokens > 512 else 4096,
        "system": system_text,
        "messages": anthropic_messages,
        "temperature": temperature,
    }
    if top_p is not None:
        payload["top_p"] = top_p

    payload_json = json.dumps(payload)

    result = subprocess.run(
        [sys.executable, _ANTHROPIC_WORKER, url, api_key],
        input=payload_json, capture_output=True, text=True, timeout=190,
        encoding="utf-8", errors="replace",
    )

    if result.returncode != 0:
        err = result.stderr.strip()
        stdout_err = result.stdout.strip()
        if "does not support image" in stdout_err.lower() or "does not support image" in err.lower() or "cannot read" in stdout_err.lower() or "image input" in stdout_err.lower():
            return "This model only supports text. Please send text messages without images or files."
        if "timed out" in err.lower() or "срок" in err.lower():
            return "AI service timed out. Claude was slow. Please try again."
        if "invalid" in err.lower() and "key" in err.lower():
            return "Invalid Anthropic API key. Please update your API key in Settings."
        return f"AI service error: {err[:500]}"

    output = result.stdout.strip()
    if not output:
        return "AI service returned empty response."

    data = json.loads(output)
    if "error" in data:
        err_msg = data["error"].get("message", str(data["error"]))
        if "does not support image" in err_msg.lower() or "cannot read" in err_msg.lower() or "image input" in err_msg.lower():
            return "This model only supports text. Please send text messages without images or files."
        return f"Claude error: {err_msg}"

    # Anthropic response format: {"content": [{"type": "text", "text": "..."}]}
    content_blocks = data.get("content", [])
    texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "\n".join(texts).strip()
