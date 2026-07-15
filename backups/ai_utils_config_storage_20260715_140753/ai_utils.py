import os
import json
import subprocess
import sys
import secret_store

PROVIDERS_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    "deepseek": "https://api.deepseek.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
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
}

_AI_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ai_worker.py")
_ANTHROPIC_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_anthropic_worker.py")


def get_api_key(provider: str) -> str:
    provider_lower = provider.lower()
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio_config.json")
    saved_keys = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                saved_keys = json.load(f)
        except Exception:
            pass
    if provider_lower == "ollama":
        return "ollama"
    return secret_store.get_secret(f"{provider_lower}_key", saved_keys)


def provider_capabilities(provider: str) -> dict:
    return dict(PROVIDER_CAPABILITIES.get(provider.lower(), {"top_p": False, "top_k": False, "image_input": False}))


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
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio_config.json")
            connections = []
            try:
                with open(config_path, "r", encoding="utf-8") as source:
                    connections = json.load(source).get("_provider_connections", [])
            except (OSError, ValueError, AttributeError):
                pass
            connection_data = next((item for item in connections if isinstance(item, dict) and item.get("connection_type") in {"opencode_bridge", "opencode_oauth_bridge"} and item.get("enabled", True) and (not model_name or item.get("configured_model") == model_name or any(isinstance(m, dict) and m.get("id") == model_name for m in item.get("available_models", [])))), None)
            if not connection_data:
                return "OpenCode bridge is not configured for this model. Add and test a local OpenCode connection in AI Providers."
            from opencode_provider import OpenCodeBridgeConnection
            text = "\n".join(str(message.get("content", "")) for message in chat_history if message.get("role") == "user")
            response = OpenCodeBridgeConnection.from_dict(connection_data).execute({"system_instruction": system_prompt, "user_content": text, "requested_model": model_name, "timeout": 300})
            if response.get("status") == "success":
                return response.get("text", "")
            return f"OpenCode bridge error: {response.get('error_category', 'request_failed')}"
        base_url = PROVIDERS_URLS.get(provider_lower, PROVIDERS_URLS["nvidia"])
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
