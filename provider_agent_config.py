"""Agent-level AI configuration resolution.

Decides which provider/model/connection each agent (codex, elena,
product_judge, ...) actually uses, built on top of provider_config.py's
connection storage layer.

`agent_configs` is main.py's own in-memory cache of the "_agent_configs"
disk section, and main.py REBINDS it wholesale in 2 places (not just
mutates it in place: `global agent_configs; agent_configs = ...`) -- so
the functions here that need live access to it cannot take a captured
dict reference, it would go stale after a rebind. Instead,
`build_agent_ai_resolution()` is a factory main.py calls once at startup,
passing `get_agent_configs`/`set_agent_configs` callables that always
read from and write to main.py's actual current global -- the same
factory-injection pattern already used for build_tasks_router's
active_projects. Everything else in this module is a pure function or a
constant and is imported back into main.py directly, like the rest of
this session's extracted modules.

Import note: `agent_contracts.py` is force-loaded specially by main.py
(`agent_contracts_module = force_import_local_module(...)`), but under
the alias name "agent_contracts_module" rather than "agent_contracts", so
this module cannot rely on main.py's sys.modules registration the way
provider_config.py does for ai_utils. A plain `from agent_contracts
import recommended_defaults_for` is used instead -- safe in both dev and
packaged/frozen mode, since force_import_local_module's own frozen-mode
branch resolves modules by that same real file-stem name internally.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict

import ai_utils
from fastapi import HTTPException

from agent_contracts import recommended_defaults_for
from execution_config import build_effective_execution_config, validate_native_model_id
from opencode_provider import OpenCodeBridgeConnection, bridge_effective_capabilities
from provider_contracts import AgentEventType, ConnectionType, ProviderConnection
from provider_registry import provider_registry
from system_settings import SYSTEM_SETTINGS
from workflow_contracts import ExecutionBrief

from provider_config import (
    AI_PROVIDER_MODELS,
    _canonical_connection_type,
    _connection_for_id,
    _connection_uses_opencode_transport,
    _ensure_provider_connection_records,
    _filter_supported_generation,
    _general_provider_connection,
    _load_global_ai_config,
    _load_provider_connections,
    _migrate_legacy_opencode_model_references,
    _model_capabilities,
    _normalize_model_for_connection,
    _opencode_model_registry,
    _provider_connection_id,
    _provider_key_name,
    _save_global_ai_config,
    _universal_provider_connections,
    PRODUCT_JUDGE_STATUS_REASONS,
)
from config_storage import load_studio_keys, save_studio_keys
from system_settings import _get_saved_system_settings


AGENT_MODELS = {
    "nvidia": "meta/llama-3.3-70b-instruct",
    "openai": "gpt-4",
    "ollama": "dolphin-mistral:7b",
    "anthropic": "claude-sonnet-4-20250514",
}


DEFAULT_AGENTS = {
    "alex": {"name": "Alex", "role": "project_manager", "emoji": "👔", "color": "#6366f1", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("alex")},
    "maya": {"name": "Maya", "role": "analyst", "emoji": "🎯", "color": "#a855f7", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("maya")},
    "elena": {"name": "Elena", "role": "designer", "emoji": "🎨", "color": "#ec4899", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("elena")},
    "codex": {"name": "Codex", "role": "developer", "emoji": "💻", "color": "#0ea5e9", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("codex")},
    "bugcatcher": {"name": "BugCatcher", "role": "tester", "emoji": "🐛", "color": "#10b981", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("bugcatcher")},
    "sentinel": {"name": "Sentinel", "role": "security", "emoji": "🛡️", "color": "#ef4444", "enabled": False, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("sentinel")},
    "lupa": {"name": "Lupa", "role": "code_reviewer", "emoji": "🔍", "color": "#8b5cf6", "enabled": False, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("lupa")},
    "goldie": {"name": "Goldie", "role": "sales", "emoji": "💰", "color": "#f59e0b", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, **recommended_defaults_for("goldie")},
    "product_judge": {"name": "Product Judge", "role": "product_judge", "emoji": "⚖", "color": "#f97316", "enabled": True, "builtin": True, "status": "idle", "provider": "", "model": "", "use_global": False, **recommended_defaults_for("product_judge"), "top_k": None, "auto_select_independent": True},
}


OPENCODE_MODEL_ALIASES = {
    "nvidia/llama-3.3-70b-instruct": "nvidia/meta/llama-3.3-70b-instruct",
}


_CODING_CAPABLE_CONNECTION_TYPES = {
    ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value,
    ConnectionType.CLAUDE_SUBSCRIPTION.value,
    ConnectionType.OPENCODE_PROVIDER.value,
}


def load_agent_configs() -> Dict[str, Dict[str, Any]]:
    data = load_studio_keys()
    return data.get("_agent_configs", {})


def save_agent_configs(configs: Dict[str, Dict[str, Any]]):
    data = load_studio_keys()
    data["_agent_configs"] = configs
    save_studio_keys(data)


def _agent_required_capabilities(agent_id: str) -> dict[str, bool]:
    if agent_id == "product_judge":
        return {"text_input": True, "image_input": True}
    return {"text_input": True}


def _validate_agent_capabilities(agent_id: str, connection: dict[str, Any], model: str) -> dict[str, Any]:
    metadata = connection.get("capability_metadata", {}) if isinstance(connection, dict) else {}
    available_model_ids = {str(item.get("id")) for item in connection.get("available_models", []) if isinstance(item, dict)}
    model_in_connection = not available_model_ids or model in available_model_ids
    capabilities = metadata.get(model) if isinstance(metadata.get(model), dict) else _model_capabilities(str(connection.get("provider") or ""), model)
    missing = []
    unknown = []
    for name, required in _agent_required_capabilities(agent_id).items():
        if not required:
            continue
        value = capabilities.get(name)
        if value is False:
            missing.append(name)
        elif value is not True:
            unknown.append(name)
    valid = model_in_connection and not missing and not unknown
    reason = "compatible" if valid else f"Missing capability: {', '.join(missing)}" if missing else f"Unknown capability: {', '.join(unknown)}"
    if not model_in_connection:
        reason = "Model is not available on the selected connection."
    if agent_id == "product_judge":
        opencode_bridge_tested = connection.get("connection_type") == "opencode_oauth_bridge" and connection.get("capabilities", {}).get("text_input", {}).get("status") == "supported"
        if connection.get("tested_status") != "passed" and not opencode_bridge_tested:
            valid = False
            reason = "Selected model is not eligible for Product Judge."
        elif not valid:
            reason = "Selected model is not eligible for Product Judge."
    return {"valid": valid, "required": _agent_required_capabilities(agent_id), "capabilities": capabilities, "missing": missing, "unknown": unknown, "model_in_connection": model_in_connection, "reason": reason}


def _agent_config_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _parse_optional_float(value: Any, field: str, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise _agent_config_error("agent_parameters_invalid", f"{field} must be a number.")
    if number < minimum or number > maximum:
        raise _agent_config_error("agent_parameters_invalid", f"{field} must be between {minimum:g} and {maximum:g}.")
    return number


def _parse_optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(",", ".")
        number = float(text)
    except (TypeError, ValueError):
        raise _agent_config_error("agent_parameters_invalid", f"{field} must be a positive integer.")
    if number <= 0 or not number.is_integer():
        raise _agent_config_error("agent_parameters_invalid", f"{field} must be a positive integer.")
    return int(number)


def _reset_agent_ai_config(current: dict[str, Any]) -> dict[str, Any]:
    preserved = {key: current[key] for key in ("enabled", "custom_prompt", "save_path") if key in current}
    preserved.update({
        "use_global": True,
        "use_global_connection": True,
        "use_global_model": True,
        "use_global_generation_parameters": True,
        "connection_id": None,
        "connection_type": None,
        "provider": None,
        "model": None,
        "temperature": None,
        "top_p": None,
        "top_k": None,
        "max_tokens": None,
        "primary_connection": None,
        "preferred_model": None,
        "fallbacks": [],
        "fallback_mode": None,
        "allow_paid_api": False,
        "required_capabilities": [],
        "locality_policy": None,
    })
    return preserved


def _validated_agent_ai_patch(agent_id: str, current: dict[str, Any], raw_patch: dict[str, Any]) -> dict[str, Any]:
    if raw_patch.get("reset_to_defaults"):
        return _reset_agent_ai_config(current)

    patch = {key: value for key, value in raw_patch.items() if key != "reset_to_defaults"}
    next_cfg = {**current, **patch}
    if next_cfg.get("use_global_connection") is None and "use_global_connection" not in next_cfg:
        next_cfg["use_global_connection"] = True
    if next_cfg.get("use_global_model") is None and "use_global_model" not in next_cfg:
        next_cfg["use_global_model"] = True
    if next_cfg.get("use_global_generation_parameters") is None and "use_global_generation_parameters" not in next_cfg:
        next_cfg["use_global_generation_parameters"] = True

    if next_cfg.get("use_global_connection") is False:
        connection_id = str(next_cfg.get("connection_id") or "").strip()
        if not connection_id:
            raise _agent_config_error("agent_connection_required", "Select a connection for this agent.")
        connection = _connection_for_id(connection_id)
        if not connection:
            raise _agent_config_error("agent_connection_not_found", "Selected agent connection was not found.")
        next_cfg["connection_id"] = connection_id
        next_cfg["provider"] = connection.get("provider") or next_cfg.get("provider")
        next_cfg["connection_type"] = connection.get("connection_type") or next_cfg.get("connection_type")
    else:
        next_cfg["connection_id"] = None
        next_cfg["connection_type"] = None
        next_cfg["provider"] = None

    if next_cfg.get("use_global_model") is False:
        model = str(next_cfg.get("model") or "").strip()
        normalized_model = _normalize_model_for_connection(model, str(next_cfg.get("connection_id") or _load_global_ai_config().get("connection_id") or ""))
        model = str(normalized_model.get("model_id") or model)
        if normalized_model.get("migrated") and not next_cfg.get("connection_id"):
            next_cfg["connection_id"] = normalized_model.get("connection_id")
        if not model:
            raise _agent_config_error("agent_model_required", "Select a model for this agent.")
        effective_connection_id = str(next_cfg.get("connection_id") or _load_global_ai_config().get("connection_id") or "")
        connection = _connection_for_id(effective_connection_id)
        available_model_ids = {str(item.get("id")) for item in connection.get("available_models", []) if isinstance(item, dict)}
        if available_model_ids and model not in available_model_ids:
            raise _agent_config_error("agent_model_unavailable", "Selected model is not available for this agent connection.")
        validation = _validate_agent_capabilities(agent_id, connection, model)
        if not validation.get("valid"):
            raise _agent_config_error("agent_configuration_incompatible", str(validation.get("reason") or "Agent configuration is incompatible."))
        next_cfg["model"] = model
    elif "model" in patch and not str(patch.get("model") or "").strip():
        next_cfg["model"] = None

    if next_cfg.get("use_global_generation_parameters") is False:
        if "temperature" in patch:
            next_cfg["temperature"] = _parse_optional_float(patch.get("temperature"), "Temperature", 0, 2)
        if "top_p" in patch:
            next_cfg["top_p"] = _parse_optional_float(patch.get("top_p"), "Top_p", 0, 1)
        if "top_k" in patch:
            next_cfg["top_k"] = _parse_optional_positive_int(patch.get("top_k"), "Top_k")
        if "max_tokens" in patch:
            next_cfg["max_tokens"] = _parse_optional_positive_int(patch.get("max_tokens"), "Max tokens")
    else:
        for key in ("temperature", "top_p", "top_k", "max_tokens"):
            if key in patch and patch[key] in (None, ""):
                next_cfg[key] = None

    return {key: value for key, value in next_cfg.items() if key in {
        "use_global", "use_global_connection", "use_global_model", "use_global_generation_parameters",
        "connection_id", "connection_type", "provider", "model", "temperature", "top_p", "top_k", "max_tokens",
        "enabled", "custom_prompt", "save_path", "primary_connection", "preferred_model", "fallbacks", "fallback_mode",
        "allow_paid_api", "required_capabilities", "locality_policy",
    }}


def _identity_for_product_judge(connection: dict[str, Any], model: str, use_global: bool = False) -> tuple[str, str]:
    if use_global:
        return SYSTEM_SETTINGS["global_provider"], SYSTEM_SETTINGS["global_model"]
    provider = str(connection.get("provider") or "")
    if provider == "opencode_bridge" and "/" in model:
        return model.split("/", 1)[0], model
    return provider, model


def _compatible_opencode_models_for_agent(agent_id: str, connection: dict[str, Any], registry: list[dict[str, Any]]) -> list[str]:
    compatible = []
    for item in registry:
        model = str(item.get("native_model_id") or "")
        if not model:
            continue
        validation_connection = {**connection, "available_models": [{"id": model, "capabilities": item.get("capabilities", {})}], "capability_metadata": {model: item.get("capabilities", {})}}
        if _validate_agent_capabilities(agent_id, validation_connection, model).get("valid"):
            compatible.append(model)
    return compatible


def _select_opencode_fallback_model(agent_id: str, connection: dict[str, Any], configured_model: str, registry: list[dict[str, Any]]) -> dict[str, Any]:
    available = {str(item.get("native_model_id")) for item in registry if item.get("available") and item.get("native_model_id")}
    provider_id = configured_model.split("/", 1)[0] if "/" in configured_model else ""

    alias = OPENCODE_MODEL_ALIASES.get(configured_model)
    if alias and alias in available and (not provider_id or alias.startswith(f"{provider_id}/")):
        return {"possible": True, "selected_model": alias, "reason": "exact_legacy_alias"}

    last_successful = str(connection.get("last_successful_model") or "")
    if last_successful and last_successful in available and (not provider_id or last_successful.startswith(f"{provider_id}/")):
        return {"possible": True, "selected_model": last_successful, "reason": "last_successful_model"}

    for candidate in connection.get("model_fallback_chain", []) if isinstance(connection.get("model_fallback_chain"), list) else []:
        model = str(candidate or "")
        if model in available and (not provider_id or model.startswith(f"{provider_id}/")):
            return {"possible": True, "selected_model": model, "reason": "configured_fallback_chain"}

    compatible = _compatible_opencode_models_for_agent(agent_id, connection, registry)
    if len(compatible) == 1:
        return {"possible": True, "selected_model": compatible[0], "reason": "single_compatible_model"}

    return {"possible": False, "reason": "no_unambiguous_fallback"}


def _run_provider_adapter_coding_task(connection: ProviderConnection, brief: ExecutionBrief, log_callback, cancel_check) -> dict[str, Any]:
    """Drive a ProviderAdapter.execute() coding run to completion, synchronously.

    Mirrors opencode_bridge.execute_coding_task's result shape ({success,
    cancelled, session_id, summary, error}) so the rest of the pipeline does
    not need to know which backend actually generated the code.
    """
    async def _drive() -> dict[str, Any]:
        adapter = provider_registry.create(connection)
        execution_id = ""
        summary_parts: list[str] = []
        cancelled = False
        success = False
        error_message = ""
        async for event in adapter.execute(brief):
            execution_id = event.execution_id or execution_id
            if event.type == AgentEventType.COMPLETED.value:
                success = True
            elif event.type == AgentEventType.CANCELLED.value:
                cancelled = True
            elif event.type == AgentEventType.ERROR.value:
                error_message = event.message or "Unknown provider error"
            elif event.message:
                summary_parts.append(event.message)
            if event.message:
                log_callback(f"[{connection.connection_type}]: {event.message}")
            if cancel_check and cancel_check() and not cancelled:
                await adapter.cancel(execution_id)
        return {
            "success": success,
            "cancelled": cancelled,
            "session_id": execution_id,
            "summary": " ".join(summary_parts)[-2000:] if summary_parts else error_message,
            "error": error_message,
        }

    return asyncio.run(_drive())


def build_agent_ai_resolution(
    get_agent_configs: Callable[[], Dict[str, Any]],
    set_agent_configs: Callable[[Dict[str, Any]], None],
    has_opencode: bool,
    get_test_opencode_readiness: Callable[[], Callable] | None = None,
) -> SimpleNamespace:
    """Bind the functions that need live access to main.py's agent_configs.

    agent_configs is rebound (not just mutated) by main.py in 2 places, so
    every function below reads it through get_agent_configs() fresh on
    every call rather than closing over a value, and rebinds happen through
    set_agent_configs() instead of `global agent_configs`.

    get_test_opencode_readiness is also a lazy getter rather than a
    captured function, for the same reason: existing tests monkeypatch
    `main.test_opencode_readiness` at test time, after this factory has
    already run, so a value captured at factory-call time would not see
    the monkeypatch. A lambda closing over main.py's own module-level name
    (e.g. `lambda: test_opencode_readiness`) resolves it fresh on every call.
    """

    def _agent_default_config(agent_id: str) -> dict[str, Any]:
        if agent_id in DEFAULT_AGENTS:
            return DEFAULT_AGENTS[agent_id]
        return get_agent_configs().get("_custom_agents", {}).get(agent_id, {})

    def _all_agent_ids() -> set:
        return set(DEFAULT_AGENTS.keys()) | set(get_agent_configs().get("_custom_agents", {}).keys())

    def _agent_meta(agent_id: str) -> dict:
        if agent_id in DEFAULT_AGENTS:
            return DEFAULT_AGENTS[agent_id]
        return get_agent_configs().get("_custom_agents", {}).get(agent_id, {})

    def resolve_effective_agent_ai_config(agent_id: str) -> dict[str, Any]:
        data = load_studio_keys()
        _ensure_provider_connection_records(data)
        migrated = _migrate_legacy_opencode_model_references(data)
        if migrated:
            save_studio_keys(data)
            SYSTEM_SETTINGS.update(_get_saved_system_settings(data))
            set_agent_configs(load_agent_configs())
        agent_configs = get_agent_configs()
        global_cfg = _load_global_ai_config(data)
        agent = agent_configs.get(agent_id, {})
        default = _agent_default_config(agent_id)
        legacy_use_global = agent.get("use_global", default.get("use_global", True)) is not False
        use_global_connection = bool(agent.get("use_global_connection", legacy_use_global))
        use_global_model = bool(agent.get("use_global_model", legacy_use_global))
        use_global_generation = bool(agent.get("use_global_generation_parameters", legacy_use_global))
        connection_id = global_cfg.get("connection_id", "") if use_global_connection else str(agent.get("connection_id") or "")
        provider = str(global_cfg.get("provider") or SYSTEM_SETTINGS["global_provider"]) if use_global_connection else str(agent.get("provider") or default.get("provider") or SYSTEM_SETTINGS["global_provider"])
        if not connection_id:
            connection_id = _provider_connection_id(provider)
        connection = _connection_for_id(connection_id, data)
        if not connection and provider:
            connection = _general_provider_connection(data, provider, agent.get("model") or default.get("model") or global_cfg.get("model", ""))
        model = str(global_cfg.get("model") or SYSTEM_SETTINGS["global_model"]) if use_global_model else str(agent.get("model") or default.get("model") or global_cfg.get("model") or SYSTEM_SETTINGS["global_model"])
        model = str(_normalize_model_for_connection(model, connection_id, data).get("model_id") or model)
        available_model_ids = {str(item.get("id")) for item in connection.get("available_models", []) if isinstance(item, dict)}
        stale_model = bool(available_model_ids and model not in available_model_ids)
        if use_global_generation:
            generation = {key: global_cfg.get(key) for key in ("temperature", "top_p", "top_k", "max_tokens")}
            generation_source = "global"
        else:
            generation = {
                "temperature": agent.get("temperature", default.get("temperature", global_cfg.get("temperature", 0.2))),
                "top_p": agent.get("top_p", default.get("top_p", global_cfg.get("top_p"))),
                "top_k": agent.get("top_k", default.get("top_k", global_cfg.get("top_k"))),
                "max_tokens": agent.get("max_tokens", default.get("max_tokens", global_cfg.get("max_tokens"))),
            }
            generation_source = "agent"
        sendable, unsupported = _filter_supported_generation(provider, model, generation)
        validation = _validate_agent_capabilities(agent_id, connection, model)
        return {
            "agent_id": agent_id,
            "connection_id": connection_id,
            "connection_type": connection.get("connection_type", _canonical_connection_type("api_provider", provider)),
            "provider": provider,
            "model": model,
            "stale_model": stale_model,
            "connection_display_name": connection.get("display_name") or connection.get("name") or provider,
            "temperature": generation.get("temperature"),
            "top_p": generation.get("top_p"),
            "top_k": generation.get("top_k"),
            "max_tokens": generation.get("max_tokens"),
            "generation_parameters": generation,
            "sendable_generation_parameters": sendable,
            "unsupported_generation_parameters": unsupported,
            "use_global_connection": use_global_connection,
            "use_global_model": use_global_model,
            "use_global_generation_parameters": use_global_generation,
            "inherited_fields": {
                "connection": use_global_connection,
                "model": use_global_model,
                "generation_parameters": use_global_generation,
            },
            "overridden_fields": {
                "connection": not use_global_connection,
                "model": not use_global_model,
                "generation_parameters": not use_global_generation,
            },
            "configuration_source": {
                "connection": "global" if use_global_connection else "agent",
                "model": "global" if use_global_model else "agent",
                "generation_parameters": generation_source,
            },
            "capability_validation": validation,
            "capability_status": "compatible" if validation.get("valid") else "incompatible",
            "recommended_defaults": recommended_defaults_for(agent_id),
        }

    def get_agent_provider_model(agent_id: str) -> tuple:
        effective = resolve_effective_agent_ai_config(agent_id)
        return effective["provider"], effective["model"]

    def agent_opencode_model_override(agent_id: str) -> str:
        effective = resolve_effective_agent_ai_config(agent_id)
        model = str(effective.get("model") or "")
        provider = str(effective.get("provider") or "")
        if not model:
            return ""
        if "/" in model:
            return model
        return f"{provider}/{model}" if provider else model

    def resolve_agent_provider_connection(agent_id: str) -> ProviderConnection | None:
        """Return a real coding-capable ProviderConnection explicitly configured for this
        agent via the new provider layer, or None to keep the existing mandatory-OpenCode path.

        Only an agent with a primary_connection pointing at a CLI-subscription connection
        (file editing + command execution capability, not a plain chat/API connection) is
        routed through the provider layer -- everyone else's behavior is unchanged.
        """
        agent_cfg = get_agent_configs().get(agent_id, {})
        if not agent_cfg.get("enabled", True):
            return None
        connection_id = str(agent_cfg.get("primary_connection") or "").strip()
        if not connection_id:
            return None
        record = next((item for item in _universal_provider_connections() if item.get("connection_id") == connection_id), None)
        if not record or not record.get("enabled", True):
            return None
        if record.get("connection_type") not in _CODING_CAPABLE_CONNECTION_TYPES:
            return None
        try:
            return ProviderConnection(**{key: value for key, value in record.items() if key in ProviderConnection.__dataclass_fields__})
        except Exception:
            return None

    def _product_judge_independence(provider: str, model: str) -> str:
        implementation_provider, implementation_model = get_agent_provider_model("elena")
        if not provider or not model:
            return "unavailable"
        if provider != implementation_provider:
            return "different_provider"
        if model != implementation_model:
            return "same_provider_different_model"
        return "same_model_separate_role"

    def _product_judge_status(config: dict[str, Any] | None = None, data: dict | None = None) -> dict[str, Any]:
        source = data if data is not None else load_studio_keys()
        cfg = config if config is not None else {**DEFAULT_AGENTS["product_judge"], **get_agent_configs().get("product_judge", {})}
        if not cfg.get("enabled", True):
            return {"status": "configured", "available": False, "reason": "Product Judge is disabled", "independence_level": "unavailable"}
        connection = {}
        model = str(cfg.get("model") or "")
        if cfg.get("use_global"):
            global_cfg = _load_global_ai_config(source)
            provider = str(global_cfg.get("provider") or SYSTEM_SETTINGS["global_provider"])
            connection = _connection_for_id(str(global_cfg.get("connection_id") or _provider_connection_id(provider)), source)
            model = model or str(global_cfg.get("model") or SYSTEM_SETTINGS["global_model"])
        else:
            connection = _connection_for_id(str(cfg.get("connection_id") or ""), source)
        if not connection:
            return {"status": "not_configured", "available": False, "reason": PRODUCT_JUDGE_STATUS_REASONS["not_configured"], "independence_level": "unavailable"}
        provider, identity_model = _identity_for_product_judge(connection, model, bool(cfg.get("use_global")))
        independence = _product_judge_independence(provider, identity_model)
        if connection.get("configured_status") == "credential_missing":
            return {"status": "credential_missing", "available": False, "reason": PRODUCT_JUDGE_STATUS_REASONS["credential_missing"], "connection": connection, "independence_level": independence}
        if connection.get("tested_status") == "failed":
            return {"status": "connection_failed", "available": False, "reason": connection.get("last_test_result", {}).get("message") or PRODUCT_JUDGE_STATUS_REASONS["connection_failed"], "connection": connection, "independence_level": independence}
        if connection.get("tested_status") != "passed":
            return {"status": "provider_not_tested", "available": False, "reason": PRODUCT_JUDGE_STATUS_REASONS["provider_not_tested"], "connection": connection, "independence_level": independence}
        capability = connection.get("capability_metadata", {}).get(model, {})
        if capability.get("image_input") is not True:
            return {"status": "no_vision_model", "available": False, "reason": PRODUCT_JUDGE_STATUS_REASONS["no_vision_model"], "connection": connection, "independence_level": independence}
        return {"status": "available", "available": True, "reason": f"Available - {provider} / {model}", "connection": connection, "model": model, "provider": provider, "independence_level": independence}

    def _configured_product_judge_runtime() -> dict[str, Any]:
        """Resolve a read-only judge identity, preferring an independently configured vision model."""
        agent_configs = get_agent_configs()
        cfg = {**DEFAULT_AGENTS["product_judge"], **agent_configs.get("product_judge", {})}
        effective = resolve_effective_agent_ai_config("product_judge")
        implementation_provider, implementation_model = get_agent_provider_model("elena")
        provider = ""
        model = ""
        connection = {}
        status = _product_judge_status(cfg)
        configured_connection = _connection_for_id(str(cfg.get("connection_id") or ""))
        if configured_connection and configured_connection.get("connection_type") == "api_provider":
            provider = str(configured_connection.get("provider") or "")
            model = str(cfg.get("model") or configured_connection.get("configured_model") or "")
        elif str(cfg.get("provider") or "") == "opencode_bridge":
            connection_id = str(cfg.get("connection_id") or "")
            connection = next((item for item in _load_provider_connections() if item.get("connection_id") == connection_id and item.get("connection_type") == "opencode_bridge"), {})
            provider = "opencode_bridge" if connection else ""
            model = str(cfg.get("model") or connection.get("configured_model") or "")
        elif cfg.get("use_global") or cfg.get("use_global_model") or cfg.get("use_global_connection"):
            provider = effective["provider"]
            model = effective["model"]
        elif cfg.get("provider") and cfg.get("model"):
            provider = str(cfg["provider"])
            model = str(cfg["model"])
        elif cfg.get("auto_select_independent", True):
            keys = load_studio_keys()
            candidates = [
                name for name in AI_PROVIDER_MODELS
                if (name == "ollama" or bool(keys.get(_provider_key_name(name)) or keys.get(f"{name}_api_key")))
                and ai_utils.provider_capabilities(name).get("image_input")
            ]
            provider = next((name for name in candidates if name != implementation_provider), "")
            if not provider and implementation_provider in candidates:
                provider = implementation_provider
            if provider:
                models = AI_PROVIDER_MODELS.get(provider, [])
                model = next((item for item in models if provider != implementation_provider or item != implementation_model), models[0] if models else "")
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "provider": provider,
            "model": model,
            "temperature": effective.get("temperature", cfg.get("temperature", 0.3)),
            "top_p": effective.get("top_p", cfg.get("top_p", 0.9)),
            "top_k": effective.get("top_k", cfg.get("top_k")),
            "connection": connection,
            "connection_id": connection.get("connection_id", ""),
            "effective_image_input": bridge_effective_capabilities(OpenCodeBridgeConnection.from_dict(connection)).get("image_input", False) if connection else False,
            "use_global": bool(cfg.get("use_global")),
            "implementation_identity": {"agent_id": "elena", "provider": implementation_provider, "model": implementation_model},
            "readiness_status": status.get("status"),
            "readiness_reason": status.get("reason"),
            "independence_level": status.get("independence_level", "unavailable"),
        }

    def _save_repaired_opencode_model(agent_id: str, selected_model: str, effective: dict[str, Any]) -> None:
        data = load_studio_keys()
        if effective.get("use_global_model") is not False:
            _save_global_ai_config({
                "connection_id": effective.get("connection_id", ""),
                "connection_type": effective.get("connection_type", ""),
                "provider": effective.get("provider", "opencode_bridge"),
                "model": selected_model,
                "temperature": effective.get("temperature"),
                "top_p": effective.get("top_p"),
                "top_k": effective.get("top_k"),
                "max_tokens": effective.get("max_tokens"),
                "enabled": True,
            })
            return
        configs = data.get("_agent_configs", {}) if isinstance(data.get("_agent_configs"), dict) else {}
        current = configs.get(agent_id, {}) if isinstance(configs.get(agent_id), dict) else {}
        current["model"] = selected_model
        current["use_global_model"] = False
        configs[agent_id] = current
        data["_agent_configs"] = configs
        save_studio_keys(data)
        set_agent_configs(configs)

    def _opencode_preflight_for_agent(agent_id: str, project_dir: str | None = None, *, repair_attempted: bool = False) -> dict[str, Any]:
        if not has_opencode:
            return {"ready": False, "blocking_reason": "opencode_executable_not_found", "message": "OpenCode bridge is not available.", "server_required": False, "checks": {}}
        execution_config = build_effective_execution_config(load_studio_keys(), project_dir or "")
        if not execution_config.live_execution_enabled:
            return {"ready": False, "blocking_reason": "live_execution_opt_in_required", "message": "Live coding backend execution is disabled in the effective execution configuration.", "server_required": False, "checks": {"live_execution": {"status": "failed", "error_code": "live_execution_opt_in_required"}}, "effective_execution_config": execution_config.to_dict()}
        effective = resolve_effective_agent_ai_config(agent_id)
        connection_id = str(effective.get("connection_id") or execution_config.connection_id or "")
        model_id = agent_opencode_model_override(agent_id) or execution_config.model_id
        normalized = _normalize_model_for_connection(model_id, connection_id)
        model_id = str(normalized.get("model_id") or model_id)
        valid_model, invalid_reason = validate_native_model_id(model_id, "opencode")
        if not model_id or _normalize_model_for_connection(model_id, connection_id).get("migrated") or not valid_model:
            return {"ready": False, "blocking_reason": "opencode_model_reference_invalid", "message": "The selected OpenCode model is invalid.", "connection_id": connection_id, "model_id": model_id, "server_required": False, "checks": {}}
        if project_dir:
            path = Path(project_dir)
            if not path.is_absolute():
                return {"ready": False, "blocking_reason": "opencode_workspace_invalid", "message": "Project workspace must be an absolute path.", "connection_id": connection_id, "model_id": model_id, "server_required": False, "checks": {"workspace": {"status": "failed"}}}
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".freelancerstudio-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            except OSError:
                return {"ready": False, "blocking_reason": "opencode_workspace_invalid", "message": "Project workspace is not writable.", "connection_id": connection_id, "model_id": model_id, "server_required": False, "checks": {"workspace": {"status": "failed"}}}
        connection = _connection_for_id(connection_id)
        executable = str(connection.get("executable_path") or "")
        readiness = get_test_opencode_readiness()(executable, model_id, "")
        if readiness.get("ready"):
            return {"ready": True, "connection_id": connection_id, "transport": "opencode", "provider_id": model_id.split("/", 1)[0] if "/" in model_id else "", "model_id": model_id, "server_required": False, "checks": {**readiness.get("checks", {}), "workspace": {"status": "passed" if project_dir else "not_checked"}, "live_execution": {"status": "passed"}}, "readiness": readiness, "effective_execution_config": execution_config.to_dict()}
        code = str(readiness.get("error_code") or "opencode_connection_not_ready")
        provider_id = model_id.split("/", 1)[0] if "/" in model_id else str(effective.get("provider") or "")
        if code == "selected_model_unavailable":
            registry = _opencode_model_registry(readiness, provider_id)
            automatic_repair = _select_opencode_fallback_model(agent_id, connection, model_id, registry)
            if automatic_repair.get("possible") and not repair_attempted:
                selected = str(automatic_repair.get("selected_model") or "")
                _save_repaired_opencode_model(agent_id, selected, effective)
                repaired = _opencode_preflight_for_agent(agent_id, project_dir, repair_attempted=True)
                return {**repaired, "automatic_repair": {**automatic_repair, "applied": bool(repaired.get("ready"))}, "previous_model_id": model_id}
            model_ids = {item.get("native_model_id") for item in registry}
            connection["available_models"] = [{"id": item["native_model_id"], "capabilities": item.get("capabilities", {})} for item in registry]
            return {
                "ready": False,
                "connection_id": connection_id,
                "transport": "opencode" if _connection_uses_opencode_transport(connection, effective) else "unknown",
                "provider_id": provider_id,
                "configured_model": model_id,
                "model_id": model_id,
                "model_count": len(registry),
                "available_models": registry,
                "server_required": False,
                "blocking_reason": "selected_model_unavailable",
                "message": readiness.get("message", "The selected OpenCode model is not available."),
                "checks": {**readiness.get("checks", {}), "workspace": {"status": "passed" if project_dir else "not_checked"}},
                "readiness": readiness,
                "automatic_repair": automatic_repair,
                "actions": ["refresh_models", "choose_model"],
                "recoverable": True,
                "selector_required": True,
                "saved_model_still_visible": model_id not in model_ids,
            }
        mapped = {
            "opencode_not_installed": "opencode_executable_not_found",
            "selected_model_unavailable": "opencode_model_not_found",
            "selected_provider_not_authenticated": "opencode_authentication_failure",
            "provider_not_authenticated": "opencode_authentication_failure",
        }.get(code, code)
        return {"ready": False, "connection_id": connection_id, "transport": "opencode", "provider_id": provider_id, "model_id": model_id, "server_required": False, "blocking_reason": mapped, "message": readiness.get("message", "OpenCode readiness failed."), "checks": {**readiness.get("checks", {}), "workspace": {"status": "passed" if project_dir else "not_checked"}, "live_execution": {"status": "passed"}}, "readiness": readiness, "effective_execution_config": execution_config.to_dict(), "recoverable": mapped in {"opencode_model_not_found", "opencode_executable_not_found", "opencode_authentication_failure"}}

    return SimpleNamespace(
        _agent_default_config=_agent_default_config,
        _all_agent_ids=_all_agent_ids,
        _agent_meta=_agent_meta,
        resolve_effective_agent_ai_config=resolve_effective_agent_ai_config,
        get_agent_provider_model=get_agent_provider_model,
        agent_opencode_model_override=agent_opencode_model_override,
        resolve_agent_provider_connection=resolve_agent_provider_connection,
        _product_judge_independence=_product_judge_independence,
        _product_judge_status=_product_judge_status,
        _configured_product_judge_runtime=_configured_product_judge_runtime,
        _save_repaired_opencode_model=_save_repaired_opencode_model,
        _opencode_preflight_for_agent=_opencode_preflight_for_agent,
    )
