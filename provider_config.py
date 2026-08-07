"""AI provider configuration storage and resolution.

Covers the legacy single-connection config (`_provider_connections`,
`_global_ai`), the connection sanitizers shared by both systems, and the
newer "universal" multi-connection system (`_universal_provider_connections`).

Extracted from main.py as one cohesive unit: these ~40 functions form a
single, tightly interconnected call graph with NO dependency on main.py's
`agent_configs` (which is rebound at runtime via `global agent_configs` and
lives in main.py's agent-AI-resolution layer, a separate, not-yet-extracted
concern). Every function here either takes its state via a `data: dict`
parameter or reads module-level state owned elsewhere (`SYSTEM_SETTINGS` from
system_settings.py, `config_storage`) that main.py never rebinds -- so a
plain import is safe in both directions, unlike the state main.py itself
owns (active_projects, agent_configs), which uses factory injection instead.

Import ordering note: `ai_utils` is force-loaded specially by main.py (works
both in normal and packaged/frozen mode, see main.py's
`force_import_local_module`). main.py imports this module only AFTER that
force-load runs, so the plain `import ai_utils` below resolves through
Python's sys.modules cache to the SAME object main.py uses, in both dev and
frozen mode.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import ai_utils
import secret_store
from config_storage import load_studio_keys, save_studio_keys
from execution_config import (
    is_opencode_connection_type,
    migrate_legacy_execution_config,
    normalize_model_id as normalize_execution_model_id,
    provider_connection_id as execution_provider_connection_id,
)
from provider_contracts import AuthMethod, ConnectionType, ProviderConnection
from provider_migration import migrate_provider_connections
from provider_registry import provider_registry
from system_settings import SYSTEM_SETTINGS, _get_saved_system_settings

try:
    from opencode_bridge import normalize_opencode_model_id
    _HAS_OPENCODE = True
except ImportError:
    _HAS_OPENCODE = False


AI_PROVIDER_MODELS = {
    "openai": ["gpt-4o", "gpt-4.1", "gpt-5", "gpt-5.5"],
    "nvidia": ["meta/llama-3.3-70b-instruct"],
    "anthropic": ["claude-sonnet-4-20250514"],
    "google": ["gemini-2.0-flash-001"],
    "groq": ["llama-3.1-70b-versatile"],
    "mistral": ["mistral-large-latest"],
    "deepseek": ["deepseek-chat"],
    "together": ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"],
    "ollama": ["codellama", "llama3.1", "mistral"],
}


MODEL_CAPABILITY_OVERRIDES = {
    "openai": {
        "gpt-4o": {"text_input": True, "image_input": True, "structured_output": True, "streaming": True, "tool_use": True},
        "gpt-4.1": {"text_input": True, "image_input": True, "structured_output": True, "streaming": True, "tool_use": True},
        "gpt-5": {"text_input": True, "image_input": None, "structured_output": True, "streaming": True, "tool_use": True},
        "gpt-5.5": {"text_input": True, "image_input": None, "structured_output": True, "streaming": True, "tool_use": True},
    },
    "anthropic": {
        "claude-sonnet-4-20250514": {"text_input": True, "image_input": True, "structured_output": False, "streaming": True, "tool_use": True},
    },
}


PRODUCT_JUDGE_STATUS_REASONS = {
    "not_configured": "no provider connection selected",
    "provider_not_tested": "selected provider connection has not passed a test",
    "no_vision_model": "selected model does not support images",
    "credential_missing": "credential is missing",
    "connection_failed": "selected provider connection test failed",
    "configured": "configured but not enabled",
    "available": "ready",
    "temporarily_unavailable": "provider is temporarily unavailable",
}


def _mask_secret(value: str) -> str:
    return secret_store.mask_secret(value)


def _provider_key_name(provider: str) -> str:
    return f"{provider}_key"


def _provider_api_key(provider: str, data: dict | None = None) -> str:
    return secret_store.get_secret(_provider_key_name(provider), data if data is not None else load_studio_keys())


def _legacy_secret_warnings(data: dict | None = None) -> list[dict]:
    return secret_store.collect_legacy_secret_warnings(data if data is not None else load_studio_keys())


def _load_provider_connections(data: dict | None = None) -> list[dict[str, Any]]:
    source = data if data is not None else load_studio_keys()
    connections = source.get("_provider_connections", [])
    return [item for item in connections if isinstance(item, dict)] if isinstance(connections, list) else []


_PROVIDER_CONNECTION_CREDENTIAL_FIELDS = {
    "apikey",
    "token",
    "accesstoken",
    "refreshtoken",
    "secret",
    "clientsecret",
    "password",
    "passwd",
    "cookie",
    "cookies",
    "authorization",
    "credential",
    "credentials",
    "devicecode",
    "privatekey",
}


def _is_provider_connection_credential_field(key: Any) -> bool:
    normalized = "".join(char for char in str(key).lower() if char not in {"_", "-", " "})
    return normalized in _PROVIDER_CONNECTION_CREDENTIAL_FIELDS


def _sanitize_provider_connection_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_provider_connection_for_storage(item)
            for key, item in value.items()
            if not _is_provider_connection_credential_field(key)
        }
    if isinstance(value, list):
        return [_sanitize_provider_connection_for_storage(item) for item in value]
    return value


def _remove_credential_fields(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        cleaned = {}
        removed = 0
        for key, item in value.items():
            if _is_provider_connection_credential_field(key):
                removed += 1
                continue
            cleaned[key], nested_removed = _remove_credential_fields(item)
            removed += nested_removed
        return cleaned, removed
    if isinstance(value, list):
        cleaned = []
        removed = 0
        for item in value:
            nested, nested_removed = _remove_credential_fields(item)
            cleaned.append(nested)
            removed += nested_removed
        return cleaned, removed
    return value, 0


def _store_provider_connections(data: dict, connections: list[dict[str, Any]]) -> None:
    data["_provider_connections"] = _sanitize_provider_connection_for_storage(connections)


def _model_capabilities(provider: str, model: str) -> dict[str, Any]:
    caps = MODEL_CAPABILITY_OVERRIDES.get((provider or "").lower(), {}).get(model)
    if caps is None:
        provider_caps = ai_utils.provider_capabilities(provider or "")
        image_input = True if provider_caps.get("image_input") and model in MODEL_CAPABILITY_OVERRIDES.get((provider or "").lower(), {}) else None
        caps = {"text_input": True, "image_input": image_input, "structured_output": None, "streaming": None, "tool_use": None}
    return dict(caps)


def _models_with_capabilities(provider: str, models: list[str] | None = None) -> list[dict[str, Any]]:
    return [{"id": model, "capabilities": _model_capabilities(provider, model)} for model in (models or AI_PROVIDER_MODELS.get(provider, []))]


def _parameter_support(provider: str, model: str = "") -> dict[str, bool]:
    provider_caps = ai_utils.provider_capabilities(provider or "")
    return {
        "temperature": True,
        "top_p": bool(provider_caps.get("top_p")),
        "top_k": bool(provider_caps.get("top_k")),
        "max_tokens": True,
    }


def _filter_supported_generation(provider: str, model: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    support = _parameter_support(provider, model)
    sendable = {}
    unsupported = {}
    for key, value in params.items():
        if value is None:
            continue
        if support.get(key, False):
            sendable[key] = value
        else:
            unsupported[key] = "Not supported by this model/provider"
    return sendable, unsupported


def _provider_credential_exists(data: dict, provider: str) -> bool:
    return provider == "ollama" or bool(_provider_api_key(provider, data))


def _provider_connection_id(provider: str) -> str:
    return execution_provider_connection_id(provider)


def _canonical_connection_type(value: str, provider: str = "") -> str:
    raw = str(value or "").strip().lower()
    provider = str(provider or "").strip().lower()
    if raw in {"opencode", "opencode_bridge", "opencode_oauth_bridge"}:
        return "opencode_oauth_bridge"
    if raw in {"openai_api", "api_provider"} and provider == "openai":
        return "openai_api"
    if raw in {"api_provider", "other_provider_api"}:
        return "other_provider_api"
    if raw == "local_model" or provider == "ollama":
        return "local_model"
    return raw or ("openai_api" if provider == "openai" else "other_provider_api")


def _connection_uses_opencode_transport(connection: dict[str, Any], effective: dict[str, Any] | None = None) -> bool:
    """Detect OpenCode transport from structured metadata, not a literal connection ID."""
    values = {
        str(connection.get("connection_type") or "").lower(),
        str(connection.get("transport") or "").lower(),
        str(connection.get("backend") or "").lower(),
        str(connection.get("execution_backend") or "").lower(),
        str(connection.get("transport_type") or "").lower(),
        str((effective or {}).get("connection_type") or "").lower(),
        str((effective or {}).get("transport") or "").lower(),
        str((effective or {}).get("backend") or "").lower(),
        str((effective or {}).get("transport_type") or "").lower(),
    }
    return any(is_opencode_connection_type(item) for item in values)


def _opencode_model_registry(readiness: dict[str, Any], provider_id: str = "") -> list[dict[str, Any]]:
    models = []
    for item in readiness.get("available_models", []) or []:
        raw = str(item.get("id") if isinstance(item, dict) else item).strip()
        if not raw:
            continue
        model_provider = raw.split("/", 1)[0] if "/" in raw else provider_id
        if provider_id and model_provider != provider_id:
            continue
        models.append({
            "id": raw,
            "native_model_id": raw,
            "provider_id": model_provider,
            "display_name": raw,
            "available": True,
            "source": "opencode_models",
            "capabilities": item.get("capabilities", {}) if isinstance(item, dict) and isinstance(item.get("capabilities"), dict) else {},
        })
    return models


def _general_provider_connection(data: dict, provider: str, model: str = "", tested: bool | None = None, test_result: dict | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    available_models = _models_with_capabilities(provider)
    has_credential = _provider_credential_exists(data, provider)
    status = "configured" if has_credential else "credential_missing"
    if tested is True:
        status = "tested" if has_credential else "credential_missing"
    elif tested is False:
        status = "test_failed" if has_credential else "credential_missing"
    connection_type = _canonical_connection_type("api_provider", provider)
    return {
        "connection_id": _provider_connection_id(provider),
        "display_name": f"{provider.upper()} API key",
        "name": f"{provider.upper()} API key",
        "provider": provider,
        "connection_type": connection_type,
        "credential_reference": _provider_key_name(provider),
        "configured_status": status,
        "tested_status": "passed" if tested is True else "failed" if tested is False else "not_tested",
        "last_test_result": test_result or {},
        "available_models": available_models,
        "capability_metadata": {item["id"]: item["capabilities"] for item in available_models},
        "configured_model": model or (AI_PROVIDER_MODELS.get(provider, [""])[0] if AI_PROVIDER_MODELS.get(provider) else ""),
        "updated_at": now,
        "stores_authentication": False,
        "configured": has_credential,
        "authenticated": has_credential,
        "tested": tested is True,
        "last_test_status": "passed" if tested is True else "failed" if tested is False else "not_tested",
    }


def _upsert_provider_connection(data: dict, connection: dict[str, Any]) -> None:
    connections = _load_provider_connections(data)
    existing = next((i for i, item in enumerate(connections) if item.get("connection_id") == connection.get("connection_id")), None)
    if existing is None:
        connections.append(connection)
    else:
        merged = {**connections[existing], **connection}
        connections[existing] = merged
    _store_provider_connections(data, connections)


def _ensure_provider_connection_records(data: dict) -> bool:
    changed = False
    connections = _load_provider_connections(data)
    for item in connections:
        if not isinstance(item, dict):
            continue
        canonical = _canonical_connection_type(str(item.get("connection_type") or ""), str(item.get("provider") or ""))
        if item.get("connection_type") != canonical:
            item["connection_type"] = canonical
            changed = True
    existing_ids = {item.get("connection_id") for item in connections}
    system = _get_saved_system_settings(data)
    candidate_providers = {str(system.get("global_provider") or "").lower()}
    for provider in AI_PROVIDER_MODELS:
        if provider == "ollama" and system.get("global_provider") != "ollama":
            continue
        if provider != "ollama" and _provider_credential_exists(data, provider):
            candidate_providers.add(provider)
    for provider in sorted(item for item in candidate_providers if item in AI_PROVIDER_MODELS):
        connection_id = _provider_connection_id(provider)
        if connection_id in existing_ids:
            continue
        model = system.get("global_model", "") if system.get("global_provider") == provider else ""
        connections.append(_general_provider_connection(data, provider, model))
        existing_ids.add(connection_id)
        changed = True
    if changed:
        _store_provider_connections(data, connections)
    return changed


def _sanitize_provider_connection(connection: dict[str, Any]) -> dict[str, Any]:
    safe = _sanitize_provider_connection_for_storage(connection)
    safe["connection_type"] = _canonical_connection_type(str(safe.get("connection_type") or ""), str(safe.get("provider") or ""))
    if _connection_uses_opencode_transport(safe):
        safe["connection_type"] = "opencode_oauth_bridge"
        configured = safe.get("configured_model") or ""
        raw_models = safe.get("available_models") if isinstance(safe.get("available_models"), list) else []
        bridge_capabilities = safe.get("capabilities", {}) if isinstance(safe.get("capabilities"), dict) else {}
        readiness_passed = safe.get("readiness_status") == "ready"
        model_capabilities = {
            "text_input": readiness_passed or bridge_capabilities.get("text_input", {}).get("status") == "supported",
            "image_input": bridge_capabilities.get("image_input", {}).get("status") == "supported",
            "structured_output": None,
            "streaming": False,
            "tool_use": None,
        }
        models = []
        for item in raw_models:
            if isinstance(item, dict):
                item_id = str(item.get("id") or "")
                capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else model_capabilities
                if item_id:
                    models.append({**item, "id": item_id, "capabilities": {**model_capabilities, **capabilities}})
            elif item:
                item_id = str(item)
                models.append({"id": item_id, "capabilities": model_capabilities, "provider": item_id.split("/", 1)[0] if "/" in item_id else "opencode", "source_connection": safe.get("connection_id", "")})
        if not models and configured:
            models = [{"id": configured, "capabilities": model_capabilities, "provider": configured.split("/", 1)[0] if "/" in configured else "opencode", "source_connection": safe.get("connection_id", "")}]
        safe["provider"] = "opencode_bridge"
        safe["display_name"] = safe.get("display_name") or safe.get("name") or "OpenCode"
        safe["tested_status"] = "passed" if model_capabilities["text_input"] else "not_tested"
        safe["configured_status"] = "tested" if safe["tested_status"] == "passed" else "configured"
        safe["available_models"] = models
        safe["capability_metadata"] = {item["id"]: item["capabilities"] for item in safe["available_models"]}
        safe["last_test_result"] = {"status": "ok" if safe["tested_status"] == "passed" else "not_tested", "message": safe.get("last_checked_at", "")}
        safe["updated_at"] = safe.get("last_checked_at", "")
        safe["stores_authentication"] = False
        safe["authentication_owner"] = "OpenCode"
        safe["configured"] = True
        safe["authenticated"] = safe["tested_status"] == "passed"
        safe["tested"] = safe["tested_status"] == "passed"
        safe["last_test_status"] = safe["tested_status"]
        if readiness_passed:
            safe["capabilities"] = {"text_input": {"status": "supported", "evidence": "Backend readiness gate verified executable, server, provider authentication, and selected model."}}
    return safe


# Mirrors provider_adapters.py's CLISubscriptionAdapter subclasses' default_models /
# model_display_names. Duplicated here because this module is synchronous and can't
# await the real adapter's list_models(); without this, the synthesized connection
# below would only ever expose the single model_id last saved on the connection,
# so picking any other real alias (e.g. claude/sonnet) would fail capability
# validation with "Model is not available on the selected connection."
_CLI_SUBSCRIPTION_MODEL_CATALOG: dict[str, dict[str, str]] = {
    "claude_subscription": {
        "claude/default": "Default (CLI-selected)",
        "claude/opus": "Claude Opus (alias)",
        "claude/sonnet": "Claude Sonnet (alias)",
        "claude/fable": "Claude Fable (alias)",
        "claude/haiku": "Claude Haiku (alias)",
    },
    "codex_chatgpt_subscription": {
        "codex/default": "Default (CLI-selected)",
        "codex/gpt-5.5": "GPT-5.5",
        "codex/gpt-5.5-codex": "GPT-5.5 Codex",
    },
    "gemini_google_account": {
        "gemini/default": "Default (CLI-selected)",
        "gemini/pro": "Gemini Pro",
        "gemini/flash": "Gemini Flash",
    },
}


def _frontend_provider_connections(data: dict | None = None) -> list[dict[str, Any]]:
    source = data if data is not None else load_studio_keys()
    _ensure_provider_connection_records(source)
    legacy = [_sanitize_provider_connection(item) for item in _load_provider_connections(source)]
    existing_ids = {item.get("connection_id") for item in legacy}
    universal = []
    for item in _universal_provider_connections(source):
        cid = item.get("connection_id")
        if not cid or cid in existing_ids:
            continue
        model_id = str(item.get("model_id") or "")
        provider = str(item.get("provider_id") or item.get("provider") or "")
        ctype = str(item.get("connection_type") or "")
        display = str(item.get("display_name") or cid)
        catalog = _CLI_SUBSCRIPTION_MODEL_CATALOG.get(ctype)
        if catalog:
            model_ids = list(catalog.keys()) if model_id in catalog or not model_id else [model_id, *catalog.keys()]
        else:
            model_ids = [model_id] if model_id else []
        default_caps = {"text_input": True, "image_input": None, "structured_output": None, "streaming": None, "tool_use": None}
        universal.append(_sanitize_provider_connection({
            **item,
            "name": display,
            "display_name": display,
            "provider": provider,
            "configured_model": model_id,
            "configured_provider": provider,
            "configured_status": "configured" if item.get("enabled", True) else "disabled",
            "tested_status": "not_tested",
            "available_models": [{"id": mid, "display_name": catalog.get(mid, mid) if catalog else mid, "capabilities": default_caps} for mid in model_ids],
            "capability_metadata": {mid: default_caps for mid in model_ids},
            "authentication_owner": "official_cli" if item.get("auth_method") == "delegated_cli_login" else item.get("auth_method", ""),
            "supports_provider_auth": item.get("auth_method") == "delegated_cli_login" or ctype == "opencode_provider",
        }))
    return legacy + universal


def _default_global_ai_config(data: dict | None = None) -> dict[str, Any]:
    source = data if data is not None else load_studio_keys()
    system = _get_saved_system_settings(source)
    provider = str(system.get("global_provider") or "nvidia").lower()
    model = str(system.get("global_model") or (AI_PROVIDER_MODELS.get(provider, [""])[0] if AI_PROVIDER_MODELS.get(provider) else ""))
    return {
        "connection_id": _provider_connection_id(provider),
        "connection_type": _canonical_connection_type("api_provider", provider),
        "provider": provider,
        "model": model,
        "temperature": 0.2,
        "top_p": None,
        "top_k": None,
        "max_tokens": None,
        "enabled": True,
        "updated_at": "",
    }


def _load_global_ai_config(data: dict | None = None) -> dict[str, Any]:
    source = data if data is not None else load_studio_keys()
    if data is None and _migrate_legacy_opencode_model_references(source):
        save_studio_keys(source)
    saved = source.get("_global_ai", {}) if isinstance(source.get("_global_ai"), dict) else {}
    return {**_default_global_ai_config(source), **saved}


def _save_global_ai_config(config: dict[str, Any]) -> dict[str, Any]:
    data = load_studio_keys()
    _migrate_legacy_opencode_model_references(data)
    current = _load_global_ai_config(data)
    normalized = _normalize_model_for_connection(str(config.get("model") or ""), str(config.get("connection_id") or current.get("connection_id") or ""), data)
    if normalized.get("model_id"):
        config = {**config, "model": normalized["model_id"]}
        if normalized.get("migrated") and not config.get("connection_id"):
            config["connection_id"] = normalized.get("connection_id")
    current.update({key: value for key, value in config.items() if key in {"connection_id", "connection_type", "provider", "model", "temperature", "top_p", "top_k", "max_tokens", "enabled"}})
    current["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    data["_global_ai"] = current
    system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
    system["global_provider"] = current["provider"]
    system["global_model"] = current["model"]
    data["_system"] = system
    SYSTEM_SETTINGS["global_provider"] = current["provider"]
    SYSTEM_SETTINGS["global_model"] = current["model"]
    if current.get("connection_type") != "opencode_oauth_bridge":
        _upsert_provider_connection(data, _general_provider_connection(data, current["provider"], current["model"]))
    save_studio_keys(data)
    return current


def _vision_models_for_connection(connection: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in connection.get("available_models", []) if isinstance(item, dict) and item.get("capabilities", {}).get("image_input") is True]


def _connection_for_id(connection_id: str, data: dict | None = None) -> dict[str, Any]:
    return next((item for item in _frontend_provider_connections(data) if item.get("connection_id") == connection_id), {})


def _opencode_connection_ids(data: dict | None = None) -> set[str]:
    return {str(item.get("connection_id")) for item in _load_provider_connections(data) if isinstance(item, dict) and _connection_uses_opencode_transport(item) and item.get("connection_id")}


def _normalize_model_for_connection(model: str, connection_id: str = "", data: dict | None = None) -> dict[str, Any]:
    known_ids = _opencode_connection_ids(data)
    if connection_id:
        known_ids.add(str(connection_id))
    if _HAS_OPENCODE:
        return normalize_opencode_model_id(model, connection_id, known_ids)
    return normalize_execution_model_id(model, connection_id, known_ids)


def _migrate_legacy_opencode_model_references(data: dict) -> bool:
    changed = migrate_legacy_execution_config(data)
    global_cfg = data.get("_global_ai", {}) if isinstance(data.get("_global_ai"), dict) else {}
    if changed and global_cfg.get("connection_type") == "opencode_oauth_bridge" and global_cfg.get("model"):
        system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
        system["global_provider"] = "opencode_bridge"
        system["global_model"] = global_cfg["model"]
        data["_system"] = system
    global_connection_id = str(global_cfg.get("connection_id") or "")
    normalized = _normalize_model_for_connection(str(global_cfg.get("model") or ""), global_connection_id, data)
    if normalized.get("migrated"):
        global_cfg["connection_id"] = normalized.get("connection_id") or global_connection_id
        global_cfg["model"] = normalized["model_id"]
        global_cfg["provider"] = "opencode_bridge"
        global_cfg["connection_type"] = "opencode_oauth_bridge"
        data["_global_ai"] = global_cfg
        system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
        system["global_provider"] = "opencode_bridge"
        system["global_model"] = normalized["model_id"]
        data["_system"] = system
        changed = True
    configs = data.get("_agent_configs", {}) if isinstance(data.get("_agent_configs"), dict) else {}
    for agent_id, cfg in list(configs.items()):
        if not isinstance(cfg, dict) or agent_id.startswith("_"):
            continue
        connection_id = str(cfg.get("connection_id") or global_cfg.get("connection_id") or "")
        normalized = _normalize_model_for_connection(str(cfg.get("model") or ""), connection_id, data)
        if normalized.get("migrated"):
            cfg["connection_id"] = cfg.get("connection_id") or normalized.get("connection_id")
            cfg["model"] = normalized["model_id"]
            configs[agent_id] = cfg
            changed = True
    if changed:
        data["_agent_configs"] = configs
    return changed


def _universal_provider_connections(data: dict | None = None) -> list[dict[str, Any]]:
    source = data if data is not None else load_studio_keys()
    if migrate_provider_connections(source):
        save_studio_keys(source)
    connections = source.get("_universal_provider_connections", []) if isinstance(source.get("_universal_provider_connections"), list) else []
    safe = []
    for item in connections:
        if not isinstance(item, dict):
            continue
        clean = _sanitize_provider_connection_for_storage(item)
        clean.pop("api_key", None)
        clean.pop("token", None)
        clean.pop("secret", None)
        safe.append(clean)
    return safe


def _save_universal_provider_connections(data: dict, connections: list[dict[str, Any]]) -> None:
    data["_universal_provider_connections"] = [_sanitize_provider_connection_for_storage(item) for item in connections]


def _universal_connection_index(connections: list[dict[str, Any]], connection_id: str) -> int | None:
    for index, item in enumerate(connections):
        if item.get("connection_id") == connection_id:
            return index
    return None


def _connection_cost_mode(connection: ProviderConnection) -> str:
    if connection.auth_method == AuthMethod.LOCAL.value or "local" in connection.connection_type or connection.connection_type == ConnectionType.OLLAMA_LOCAL.value:
        return "local_compute"
    if "api_key" in connection.connection_type:
        return "separately_billed_api"
    return "subscription_limits"


async def _provider_connection_state(connection: ProviderConnection) -> dict[str, Any]:
    adapter = provider_registry.create(connection)
    capabilities = await adapter.get_capabilities()
    result = await adapter.test_connection()
    credential_state = "none"
    if connection.credential_reference:
        credential_state = "reference_configured"
        if "api_key" in connection.connection_type and result.error_code == "auth_required":
            credential_state = "credential_missing"
    return {
        "connection": connection.to_dict(),
        "result": result.to_dict(),
        "capabilities": capabilities.to_dict(),
        "cost_mode": _connection_cost_mode(connection),
        "privacy_locality": "local" if _connection_cost_mode(connection) == "local_compute" else "cloud",
        "credential_state": credential_state,
        "diagnostics": result.diagnostics,
        "last_validation": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _connection_used_by_agents(data: dict, connection_id: str) -> list[str]:
    configs = data.get("_agent_configs", {}) if isinstance(data.get("_agent_configs"), dict) else {}
    used = []
    for agent_id, cfg in configs.items():
        if not isinstance(cfg, dict):
            continue
        refs = {str(cfg.get("connection_id") or ""), str(cfg.get("primary_connection") or "")}
        refs.update(str(item) for item in cfg.get("fallbacks", []) if isinstance(cfg.get("fallbacks"), list))
        if connection_id in refs:
            used.append(str(agent_id))
    return used


def _cleanup_connection_assignments(data: dict, connection_id: str) -> None:
    configs = data.get("_agent_configs", {}) if isinstance(data.get("_agent_configs"), dict) else {}
    for cfg in configs.values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("connection_id") == connection_id:
            cfg["connection_id"] = None
            cfg["use_global_connection"] = True
        if cfg.get("primary_connection") == connection_id:
            cfg["primary_connection"] = None
        if isinstance(cfg.get("fallbacks"), list):
            cfg["fallbacks"] = [item for item in cfg["fallbacks"] if item != connection_id]


def _test_provider_key(provider: str, key: str) -> tuple[bool, str]:
    if provider == "ollama":
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
                return resp.status == 200, "Ollama is reachable" if resp.status == 200 else f"HTTP {resp.status}"
        except Exception as e:
            return False, f"Ollama is not reachable: {e}"
    if not key:
        return False, f"No API key saved for {provider}"
    endpoints = {
        "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}),
        "nvidia": ("https://integrate.api.nvidia.com/v1/models", {"Authorization": f"Bearer {key}"}),
        "groq": ("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"}),
        "mistral": ("https://api.mistral.ai/v1/models", {"Authorization": f"Bearer {key}"}),
        "deepseek": ("https://api.deepseek.com/models", {"Authorization": f"Bearer {key}"}),
        "together": ("https://api.together.xyz/v1/models", {"Authorization": f"Bearer {key}"}),
        "google": (f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", {}),
        "anthropic": ("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    }
    url, headers = endpoints.get(provider, ("", {}))
    if not url:
        return False, "Unsupported provider test"
    try:
        req = urllib.request.Request(url, headers={**headers, "User-Agent": "FreelancerStudio"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300, "Provider key accepted" if 200 <= resp.status < 300 else f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"Provider rejected key or request: HTTP {e.code}"
    except Exception as e:
        return False, f"Connection test failed: {e}"
