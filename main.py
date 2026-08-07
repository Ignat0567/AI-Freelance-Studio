import json
import os
import sys
import time
import asyncio
import uuid
import shutil
import zipfile
import tarfile
import mimetypes
import importlib.util
import importlib
import re
import glob
import subprocess
import urllib.request
import urllib.error
import hmac
import secrets
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, UploadFile, File as FastAPIFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse
from pydantic import ConfigDict
from typing import List, Dict, Any, Optional
import threading as _threading
from backend_security import (
    LocalSecurityContext,
    LocalSecurityMiddleware,
    StrictRequestModel as BaseModel,
    authorize_websocket,
    set_app_security_context,
)
import config_storage
import project_state
import secret_store
from repair_scope import SUPPORTED_LOCK_FILES, walk_repairable_files
from opencode_provider import OpenCodeBridgeConnection, PROVIDER_REGISTRY, bridge_effective_capabilities
from execution_config import (
    build_effective_execution_config,
    is_opencode_connection_type,
    migrate_legacy_execution_config,
    normalize_model_id as normalize_execution_model_id,
    provider_connection_id as execution_provider_connection_id,
    validate_native_model_id,
)
from workflow_contracts import ExecutionBrief, WorkflowState, validate_transition
import provider_adapters  # registers provider adapters
from provider_contracts import AgentEventType, AgentProviderPolicy, AuthMethod, ConnectionType, ProviderConnection
from provider_credentials import ProviderCredentialStore, credential_reference, default_backend
from provider_migration import migrate_provider_connections
from provider_registry import provider_registry
from api.accounts import router as accounts_router
from api.android import router as android_router
from api.sandbox_test_lab import install_sandbox_test_lab_api, router as sandbox_test_lab_router
from api.system import router as system_router
from api.github_integration import build_github_router
from api.discovery import build_discovery_router
from api.opencode_routes import build_opencode_status_router
from pipeline_stage_metadata import AGENT_STAGE_METADATA
from api.request_models import (
    AISettingsPayload,
    AgentAIConfigPayload,
    ClaudeCodeConnectionPayload,
    GlobalAIConfigPayload,
    KeysUpdatePayload,
    OpenCodeConnectionPayload,
    ProductJudgeConfigPayload,
    ProviderCredentialPayload,
    ProviderDeletePayload,
    ProviderTestPayload,
    UniversalProviderConnectionPatch,
    UniversalProviderConnectionPayload,
)
from sandbox_test_lab.production_bridge import (
    PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS,
    create_production_sandbox_runtime,
)
from system_settings import (
    ALLOWED_SYSTEM_KEYS,
    DEFAULT_SYSTEM_SETTINGS,
    SYSTEM_SETTINGS,
    _get_saved_system_settings,
    configure_system_settings_storage,
    reload_system_settings,
)

# OpenCode bridge (optional — for real AI-assisted code generation)
try:
    from opencode_bridge import ensure_opencode, stop_opencode, sync_opencode_config, get_opencode_status, get_opencode_onboarding_dependencies, start_opencode_web, start_opencode_auth_terminal, test_opencode_readiness, normalize_opencode_model_id, get_bridge as _get_oc_bridge
    _HAS_OPENCODE = True
except ImportError:
    _HAS_OPENCODE = False

from claude_bridge import get_claude_onboarding_dependencies as get_claude_code_onboarding_dependencies, test_claude_readiness as test_claude_code_readiness

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FREELANCERSTUDIO_USER_DATA") or os.environ.get("FREELANCERSTUDIO_HOME") or BASE_DIR
RUNTIME_DIR = os.environ.get("FREELANCERSTUDIO_RUNTIME_DIR") or DATA_DIR
FFMPEG_PATH = os.environ.get("FREELANCERSTUDIO_FFMPEG_PATH")
GENERATED_PROJECTS_ROOT = os.path.join(DATA_DIR, "generated_projects")
FRONTEND_DIST = os.environ.get("FREELANCERSTUDIO_FRONTEND_DIR") or os.path.join(BASE_DIR, "frontend", "dist")

# ─── Shared critical rules injected into ALL agent prompts ─────────────────
# Все тонкости, нюансы и грабли, выявленные в процессе разработки.
_CRITICAL_RULES = (
    "CRITICAL RULES — VIOLATING ANY WILL CAUSE PROJECT REJECTION:\n"
    "- Do not hardcode secrets or credentials; use configuration, environment variables, or explicit placeholders.\n"
    "- ALL file paths use forward slash (/) not backslash (\\) even on Windows.\n"
    "- Import names MUST exactly match the exports of the dependency file — verify before importing.\n"
    "- Test assertions MUST use the exact field names/types from actual models/schemas.\n"
    "- Tests MUST only test endpoints that actually exist in the API code — cross-reference before writing.\n"
    "- Do not leave placeholder/stub implementation, fake output, or skipped/falsified tests.\n"
    "- Container names in docker-compose.yml must NOT start with a digit. Use underscores or letters.\n"
    "- ALL dependencies used in code MUST appear in the project's dependency manifest.\n"
)


def force_import_local_module(module_name, filename):
    if getattr(sys, "frozen", False):
        module = importlib.import_module(Path(filename).stem)
        sys.modules[module_name] = module
        return module
    full_path = os.path.join(BASE_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ai_utils = force_import_local_module("ai_utils", "ai_utils.py")
search_utils = force_import_local_module("search_utils", "search_utils.py")
requirements_checker = force_import_local_module("requirements_checker", "requirements_checker.py")
agent_contracts_module = force_import_local_module("agent_contracts_module", "agent_contracts.py")

ask_studio_ai_with_history = ai_utils.ask_studio_ai_with_history
search_freelance_jobs = search_utils.search_freelance_jobs
AVAILABLE_PLATFORMS = search_utils.AVAILABLE_PLATFORMS

ROLE_CONTRACTS = agent_contracts_module.ROLE_CONTRACTS
recommended_defaults_for = agent_contracts_module.recommended_defaults_for
role_contract_for = agent_contracts_module.role_contract_for


DEVELOPMENT_CORS_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
]


def _split_csv_setting(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _is_production_settings(settings: dict | None = None) -> bool:
    source = settings if settings is not None else os.environ
    env_name = str(source.get("FREELANCERSTUDIO_ENV") or source.get("APP_ENV") or source.get("ENV") or "development").strip().lower()
    return env_name in {"prod", "production"}


def get_cors_origins(settings: dict | None = None) -> list[str]:
    source = settings if settings is not None else os.environ
    if _is_production_settings(source):
        return _split_csv_setting(source.get("FREELANCERSTUDIO_CORS_ORIGINS") or source.get("BACKEND_CORS_ORIGINS") or "")
    extra_origins = _split_csv_setting(source.get("FREELANCERSTUDIO_DEV_CORS_ORIGINS") or "")
    return list(dict.fromkeys([*DEVELOPMENT_CORS_ORIGINS, *extra_origins]))


def get_backend_bind_host(settings: dict | None = None) -> str:
    source = settings if settings is not None else os.environ
    requested = str(source.get("BACKEND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if requested.lower().rstrip(".") == "localhost":
        requested = "127.0.0.1"
    allow_network = str(source.get("BACKEND_ALLOW_NETWORK") or source.get("FREELANCERSTUDIO_ALLOW_NETWORK_BIND") or "").strip().lower() in {"1", "true", "yes", "on"}
    if requested in {"0.0.0.0", "::"} and not allow_network:
        return "127.0.0.1"
    return requested


app = FastAPI(title="FreelancerStudio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_origin_regex=r"^http://(?:127\.0\.0\.1|localhost):(?:3000|5173|808[0-9]|809[0-9])$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-FreelancerStudio-Token"],
)
app.add_middleware(LocalSecurityMiddleware)
_sandbox_runtime = create_production_sandbox_runtime(
    Path(RUNTIME_DIR),
    ffmpeg_path=Path(FFMPEG_PATH) if FFMPEG_PATH else None,
    generated_projects_root=Path(GENERATED_PROJECTS_ROOT),
)
install_sandbox_test_lab_api(
    app,
    service=_sandbox_runtime.service,
    availability_provider=_sandbox_runtime.availability_provider,
    shutdown_timeout=PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS,
)

app.include_router(accounts_router)
app.include_router(android_router)
app.include_router(sandbox_test_lab_router)
app.include_router(system_router)
app.include_router(build_github_router())
app.include_router(build_discovery_router(search_freelance_jobs, AVAILABLE_PLATFORMS))
app.include_router(build_opencode_status_router(
    _HAS_OPENCODE,
    get_opencode_status if _HAS_OPENCODE else None,
    start_opencode_web if _HAS_OPENCODE else None,
    start_opencode_auth_terminal if _HAS_OPENCODE else None,
    BASE_DIR,
))


def _backend_health_available() -> tuple[bool, str]:
    try:
        from backend_security import get_app_security_context

        context = get_app_security_context(app)
        challenge = secrets.token_urlsafe(32)
        request = urllib.request.Request(
            f"http://127.0.0.1:{context.port}/health/owner",
            headers={
                "X-FreelancerStudio-Challenge": challenge,
            },
        )
        with urllib.request.urlopen(request, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            expected = context.owner_challenge_response(challenge)
            if (
                resp.status == 200
                and payload.get("instance_id") == context.instance_id
                and isinstance(payload.get("proof"), str)
                and hmac.compare_digest(payload["proof"], expected["proof"])
            ):
                return True, "ok"
            return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


from provider_config import (
    AI_PROVIDER_MODELS,
    MODEL_CAPABILITY_OVERRIDES,
    PRODUCT_JUDGE_STATUS_REASONS,
    _CLI_SUBSCRIPTION_MODEL_CATALOG,
    _PROVIDER_CONNECTION_CREDENTIAL_FIELDS,
    _canonical_connection_type,
    _cleanup_connection_assignments,
    _connection_cost_mode,
    _connection_for_id,
    _connection_used_by_agents,
    _connection_uses_opencode_transport,
    _default_global_ai_config,
    _ensure_provider_connection_records,
    _filter_supported_generation,
    _frontend_provider_connections,
    _general_provider_connection,
    _is_provider_connection_credential_field,
    _legacy_secret_warnings,
    _load_global_ai_config,
    _load_provider_connections,
    _mask_secret,
    _migrate_legacy_opencode_model_references,
    _model_capabilities,
    _models_with_capabilities,
    _normalize_model_for_connection,
    _opencode_connection_ids,
    _opencode_model_registry,
    _parameter_support,
    _provider_api_key,
    _provider_connection_id,
    _provider_connection_state,
    _provider_credential_exists,
    _provider_key_name,
    _remove_credential_fields,
    _sanitize_provider_connection,
    _sanitize_provider_connection_for_storage,
    _save_global_ai_config,
    _save_universal_provider_connections,
    _store_provider_connections,
    _test_provider_key,
    _universal_connection_index,
    _universal_provider_connections,
    _upsert_provider_connection,
    _vision_models_for_connection,
)


CONFIG_FILE = config_storage.CONFIG_FILE  # Compatibility alias; production storage reads config_storage.CONFIG_FILE.
PROJECTS_DATA_DIR = os.path.join(DATA_DIR, "projects_data")
PROJECTS_STATE_FILE = os.path.join(DATA_DIR, "projects_state.json")


def load_studio_keys():
    return config_storage.load_studio_keys()


def save_studio_keys(data):
    config_storage.save_studio_keys(data)


configure_system_settings_storage(load_studio_keys, save_studio_keys)
reload_system_settings()


@app.get("/api/config/keys")
def get_stored_keys():
    keys = load_studio_keys()
    masked_keys = {}
    saved_keys = []
    for k, v in keys.items():
        if v and not k.startswith("_"):
            masked_keys[k.replace("_key", "")] = True
            saved_keys.append(k)
    for provider in AI_PROVIDER_MODELS:
        key_name = _provider_key_name(provider)
        if provider != "ollama" and secret_store.get_secret(key_name, keys):
            masked_keys[provider] = True
            if key_name not in saved_keys:
                saved_keys.append(key_name)
    masked_keys["has_nvidia"] = bool(_provider_api_key("nvidia", keys))
    masked_keys["has_openai"] = bool(_provider_api_key("openai", keys))
    masked_keys["has_anthropic"] = bool(_provider_api_key("anthropic", keys))
    masked_keys["has_freelancer"] = bool(keys.get("freelancer_client_id") and secret_store.get_secret("freelancer_client_secret", keys))
    masked_keys["has_upwork"] = bool(keys.get("upwork_client_id") and secret_store.get_secret("upwork_client_secret", keys))
    masked_keys["saved_keys"] = sorted(saved_keys)
    masked_keys["legacy_secret_warnings"] = _legacy_secret_warnings(keys)
    return masked_keys


@app.post("/api/config/keys")
def update_stored_keys(payload: KeysUpdatePayload):
    """Saves active user keys locally on disk."""
    data = load_studio_keys()
    skipped_secrets = []
    for key, value in payload.keys.items():
        if not value:
            continue
        if secret_store.is_secret_key(key):
            skipped_secrets.append(key)
        else:
            data[key] = value
    save_studio_keys(data)
    response = {"status": "saved"}
    if skipped_secrets:
        response["secret_store_warning"] = "Secrets are no longer written to studio_config.json. Set the matching environment variables instead."
        response["skipped_secrets"] = sorted(skipped_secrets)
    return response


@app.delete("/api/config/keys")
def reset_all_credentials():
    """Clears all stored API credentials from local disk configuration."""
    save_studio_keys({})
    return {"status": "reset"}


@app.delete("/api/config/keys/{key_name}")
def delete_stored_key(key_name: str):
    """Deletes one stored credential while preserving other settings."""
    if key_name.startswith("_") or "/" in key_name or "\\" in key_name:
        raise HTTPException(status_code=400, detail="Invalid key name")
    data = load_studio_keys()
    existed = key_name in data
    data.pop(key_name, None)
    save_studio_keys(data)
    return {"status": "deleted", "key": key_name, "existed": existed}


@app.post("/api/config/legacy-secrets/cleanup")
def cleanup_legacy_secrets():
    data = load_studio_keys()
    removed = 0
    categories = set()
    for key in list(data):
        if secret_store.is_secret_key(key):
            data.pop(key)
            removed += 1
            categories.add("provider_keys")
    github = data.get("_github")
    if isinstance(github, dict) and "token" in github:
        github.pop("token")
        removed += 1
        categories.add("github_token")
    accounts = data.get("_accounts")
    if isinstance(accounts, list):
        cleaned_accounts, account_removed = _remove_credential_fields(accounts)
        if account_removed:
            data["_accounts"] = cleaned_accounts
            removed += account_removed
            categories.add("account_credentials")
    connections = data.get("_provider_connections")
    if isinstance(connections, list):
        cleaned_connections, connection_removed = _remove_credential_fields(connections)
        if connection_removed:
            data["_provider_connections"] = cleaned_connections
            removed += connection_removed
            categories.add("provider_connection_credentials")
    if removed:
        config_storage.backup_studio_config()
        save_studio_keys(data)
    return {"removed_fields": removed, "categories": sorted(categories)}


def _ai_settings_response(data: dict | None = None) -> dict:
    cfg = data if data is not None else load_studio_keys()
    system = _get_saved_system_settings(cfg)
    provider = system.get("global_provider", "nvidia")
    model = system.get("global_model") or AI_PROVIDER_MODELS.get(provider, [""])[0]
    saved = {}
    for name in AI_PROVIDER_MODELS:
        saved[name] = secret_store.secret_status(_provider_key_name(name), cfg)
    return {
        "provider": provider,
        "model": model,
        "providers": AI_PROVIDER_MODELS,
        "saved_keys": saved,
        "opencode_available": _HAS_OPENCODE,
        "connection_providers": PROVIDER_REGISTRY,
        "legacy_secret_warnings": _legacy_secret_warnings(cfg),
    }


@app.get("/api/config/ai")
def get_ai_settings():
    data = load_studio_keys()
    response = _ai_settings_response(data)
    response["global_ai"] = _load_global_ai_config(data)
    response["provider_connections"] = _frontend_provider_connections(data)
    response["effective_execution_config"] = build_effective_execution_config(data).to_dict()
    return response


@app.get("/api/config/ai/global")
def get_global_ai_config():
    data = load_studio_keys()
    return {"global_ai": _load_global_ai_config(data), "connections": _frontend_provider_connections(data), "effective_execution_config": build_effective_execution_config(data).to_dict()}


@app.post("/api/config/ai/global")
def save_global_ai_config(payload: GlobalAIConfigPayload):
    provider = (payload.provider or "").strip().lower()
    connection = _connection_for_id(payload.connection_id) if payload.connection_id else {}
    if connection:
        provider = str(connection.get("provider") or provider)
    if provider not in AI_PROVIDER_MODELS and provider not in {"opencode", "opencode_bridge"}:
        raise HTTPException(400, "Unsupported provider")
    model = (payload.model or "").strip()
    if not model:
        raise HTTPException(400, "Model is required")
    normalized = _normalize_model_for_connection(model, payload.connection_id, load_studio_keys())
    model = str(normalized.get("model_id") or model)
    if normalized.get("migrated") and not payload.connection_id:
        payload.connection_id = str(normalized.get("connection_id") or "")
    config = _save_global_ai_config({
        "connection_id": payload.connection_id or _provider_connection_id(provider),
        "connection_type": payload.connection_type or connection.get("connection_type") or _canonical_connection_type("api_provider", provider),
        "provider": provider,
        "model": model,
        "temperature": payload.temperature,
        "top_p": payload.top_p,
        "top_k": payload.top_k,
        "max_tokens": payload.max_tokens,
        "enabled": payload.enabled,
    })
    return {"status": "saved", "global_ai": config, "connections": _frontend_provider_connections()}


@app.get("/api/agents/effective-ai")
def get_effective_agent_ai_configs():
    return {agent_id: resolve_effective_agent_ai_config(agent_id) for agent_id in _all_agent_ids()}


@app.get("/api/agents/{agent_id}/effective-ai")
def get_effective_agent_ai_config(agent_id: str):
    if agent_id not in _all_agent_ids():
        raise HTTPException(404, "Agent not found")
    return resolve_effective_agent_ai_config(agent_id)


@app.post("/api/agents/apply-global-inheritance")
def apply_global_inheritance_to_agents():
    skipped = []
    updated = []
    global_cfg = _load_global_ai_config()
    connection = _connection_for_id(str(global_cfg.get("connection_id") or "")) or _general_provider_connection(load_studio_keys(), str(global_cfg.get("provider") or ""), str(global_cfg.get("model") or ""))
    for agent_id in _all_agent_ids():
        validation = _validate_agent_capabilities(agent_id, connection, str(global_cfg.get("model") or ""))
        if not validation.get("valid"):
            skipped.append({"agent_id": agent_id, "reason": validation.get("reason")})
            continue
        current = agent_configs.get(agent_id, {})
        current["use_global_connection"] = True
        current["use_global_model"] = True
        current["use_global_generation_parameters"] = True
        current["use_global"] = True
        agent_configs[agent_id] = current
        updated.append(agent_id)
    save_agent_configs(agent_configs)
    return {"status": "success", "updated": updated, "skipped": skipped, "effective": {agent_id: resolve_effective_agent_ai_config(agent_id) for agent_id in _all_agent_ids()}}


from provider_agent_config import (
    AGENT_MODELS,
    DEFAULT_AGENTS,
    OPENCODE_MODEL_ALIASES,
    _CODING_CAPABLE_CONNECTION_TYPES,
    _agent_config_error,
    _agent_required_capabilities,
    _compatible_opencode_models_for_agent,
    _identity_for_product_judge,
    _parse_optional_float,
    _parse_optional_positive_int,
    _reset_agent_ai_config,
    _run_provider_adapter_coding_task,
    _select_opencode_fallback_model,
    _validate_agent_capabilities,
    _validated_agent_ai_patch,
    build_agent_ai_resolution,
    load_agent_configs,
    save_agent_configs,
)














@app.get("/api/provider-connections")
def list_provider_connections():
    """Connection settings intentionally exclude raw credentials and browser state."""
    data = load_studio_keys()
    return {"providers": PROVIDER_REGISTRY, "connections": _frontend_provider_connections(data), "universal_connections": _universal_provider_connections(data), "credential_backend": ProviderCredentialStore(default_backend()).diagnostics()}


@app.get("/api/provider-layer")
def get_provider_layer_status():
    data = load_studio_keys()
    credential_store = ProviderCredentialStore(default_backend())
    return {
        "adapter_contract": "ExecutionBrief -> ProviderAdapter -> AgentEvent",
        "registered_connection_types": provider_registry.registered_types(),
        "connections": _universal_provider_connections(data),
        "agent_assignments": data.get("_agent_configs", {}) if isinstance(data.get("_agent_configs"), dict) else {},
        "credential_backend": credential_store.diagnostics(),
        "api_fallback_default": "disabled",
        "subscription_auth_owner": "official_cli",
    }


@app.post("/api/provider-layer/{connection_id}/test")
async def test_universal_provider_connection(connection_id: str):
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    index = _universal_connection_index(connections, connection_id)
    connection_data = connections[index] if index is not None else None
    if not connection_data:
        raise HTTPException(404, "Provider connection not found")
    try:
        connection = ProviderConnection(**{key: value for key, value in connection_data.items() if key in ProviderConnection.__dataclass_fields__})
        adapter = provider_registry.create(connection)
        result = await adapter.test_connection()
        capabilities = await adapter.get_capabilities()
        cost_mode = "local_compute" if connection.auth_method == "local" or "local" in connection.connection_type else "separately_billed_api" if "api_key" in connection.connection_type else "subscription_limits"
        locality = "local" if cost_mode == "local_compute" else "cloud"
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if index is not None:
            meta = connections[index].get("metadata") if isinstance(connections[index].get("metadata"), dict) else {}
            connections[index]["metadata"] = {**meta, "last_validation": now, "last_validation_status": result.status, "last_validation_message": result.message}
            _save_universal_provider_connections(data, connections)
            save_studio_keys(data)
        credential_state = "reference_configured" if connection.credential_reference else "none"
        if "api_key" in connection.connection_type and result.error_code == "auth_required":
            credential_state = "credential_missing"
        return {"connection_id": connection_id, "result": result.to_dict(), "capabilities": capabilities.to_dict(), "cost_mode": cost_mode, "privacy_locality": locality, "credential_state": credential_state, "last_validation": now, "diagnostics": result.diagnostics}
    except KeyError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/provider-connections")
def create_universal_provider_connection(payload: UniversalProviderConnectionPayload):
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    connection_id = (payload.connection_id or f"provider-{uuid.uuid4().hex[:8]}").strip()
    if _universal_connection_index(connections, connection_id) is not None:
        raise HTTPException(409, {"error_code": "duplicate_connection_id", "message": "Provider connection already exists."})
    if payload.connection_type not in provider_registry.registered_types():
        raise HTTPException(400, {"error_code": "unsupported_connection_type", "message": "Connection type is not registered."})
    ref = payload.credential_reference or (credential_reference(payload.provider_id or "provider", connection_id) if "api_key" in payload.connection_type else "")
    connection = ProviderConnection(
        connection_id=connection_id,
        provider_id=payload.provider_id,
        connection_type=payload.connection_type,
        auth_method=payload.auth_method or (AuthMethod.API_KEY.value if "api_key" in payload.connection_type else AuthMethod.LOCAL.value if "local" in payload.connection_type else AuthMethod.DELEGATED_CLI_LOGIN.value),
        display_name=payload.display_name or connection_id,
        model_id=payload.model_id,
        credential_reference=ref,
        endpoint=payload.endpoint,
        executable_path=payload.executable_path,
        enabled=payload.enabled,
        priority=payload.priority,
        metadata=payload.metadata,
    )
    connections.append(connection.to_dict())
    connections.sort(key=lambda item: int(item.get("priority", 100)))
    _save_universal_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "created", "connection": connection.to_dict()}

@app.post("/api/provider-connections/opencode")
def save_opencode_connection(payload: OpenCodeConnectionPayload):
    if payload.transport_type not in {"auto", "cli", "local_service"}:
        raise HTTPException(400, "Unsupported OpenCode transport")
    selected_model = str(_normalize_model_for_connection(payload.configured_model.strip(), payload.connection_id).get("model_id") or payload.configured_model.strip())
    valid_model, invalid_reason = validate_native_model_id(selected_model, "opencode")
    if not valid_model:
        raise HTTPException(400, {"error_code": invalid_reason, "message": "OpenCode model must use native provider/model format."})
    result = test_opencode_readiness(payload.executable_path.strip(), selected_model, payload.local_endpoint.strip())
    if result.get("ready") is not True:
        raise HTTPException(409, {"error_code": result.get("error_code", "readiness_failed"), "message": result.get("message", "OpenCode readiness check failed"), "checks": result.get("checks", {})})

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    selection = result["checks"]["selection"]
    server_url = result.get("server_url", "")
    connection = {
        "connection_id": payload.connection_id.strip() or f"opencode-{uuid.uuid4().hex[:12]}",
        "connection_type": "opencode_oauth_bridge",
        "name": payload.name.strip() or "My OpenCode",
        "enabled": bool(payload.enabled),
        "executable_path": result["executable_path"],
        "local_endpoint": server_url,
        "server_port": int(server_url.rsplit(":", 1)[1]) if server_url else None,
        "configured_provider": selection["provider"],
        "configured_model": selection["model"],
        "auth_type": (result.get("selected_auth_types") or ["configured"])[0],
        "auth_status": "authenticated",
        "readiness_status": "ready",
        "last_checked_at": now,
        "created_at": now,
        "updated_at": now,
    }
    data = load_studio_keys()
    connections = _load_provider_connections(data)
    existing = next((i for i, item in enumerate(connections) if item.get("connection_id") == connection["connection_id"]), None)
    if existing is None:
        connections.append(connection)
    else:
        connections[existing] = {**connections[existing], **connection}
    _store_provider_connections(data, connections)
    data["_global_ai"] = {
        "connection_id": connection["connection_id"],
        "connection_type": "opencode_oauth_bridge",
        "provider": "opencode_bridge",
        "model": connection["configured_model"],
        "enabled": bool(payload.enabled),
        "updated_at": now,
    }
    system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
    system["global_provider"] = "opencode_bridge"
    system["global_model"] = connection["configured_model"]
    data["_system"] = system
    SYSTEM_SETTINGS["global_provider"] = "opencode_bridge"
    SYSTEM_SETTINGS["global_model"] = connection["configured_model"]
    save_studio_keys(data)
    return {"status": "saved", "connection": _sanitize_provider_connection(connection), "effective_execution_config": build_effective_execution_config(data).to_dict(), "message": "OpenCode authentication remains owned by OpenCode; only verified connection metadata was stored."}


@app.post("/api/provider-connections/opencode/detect")
def detect_opencode_connection(payload: OpenCodeConnectionPayload):
    """Discover a local bridge without persisting or inspecting OpenCode credentials."""
    dependencies = get_opencode_onboarding_dependencies()
    opencode_dependency = dependencies["components"]["opencode"]
    connection = OpenCodeBridgeConnection(
        connection_id=payload.connection_id.strip() or "transient-opencode-detect", name=payload.name.strip() or "My OpenCode",
        configured_model=str(_normalize_model_for_connection(payload.configured_model.strip()).get("model_id") or payload.configured_model.strip()), enabled=payload.enabled,
        transport_type="cli" if payload.transport_type == "auto" else payload.transport_type,
        local_endpoint=payload.local_endpoint.strip(), executable_path=opencode_dependency["path"],
    )
    binary = connection.executable_path
    report = connection.capability_report()
    return {
        "status": "detected" if binary else "executable_not_found",
        "executable_path": binary,
        "version": opencode_dependency["version"],
        "available_models": connection.available_models() if binary else [],
        "capabilities": report,
        "dependencies": dependencies,
        "authentication": "Authentication is owned by OpenCode and is verified only by Test Connection.",
    }


@app.post("/api/provider-connections/opencode/test")
def test_transient_opencode_connection(payload: OpenCodeConnectionPayload):
    """Verify local OpenCode readiness without invoking a paid model request."""
    connection = OpenCodeBridgeConnection(
        connection_id=payload.connection_id.strip() or "transient-opencode-test", name=payload.name.strip() or "My OpenCode",
        configured_model=str(_normalize_model_for_connection(payload.configured_model.strip()).get("model_id") or payload.configured_model.strip()), enabled=payload.enabled,
        transport_type="cli" if payload.transport_type == "auto" else payload.transport_type,
        local_endpoint=payload.local_endpoint.strip(), executable_path=payload.executable_path.strip(),
    )
    result = test_opencode_readiness(connection.executable_path, connection.configured_model, connection.local_endpoint)
    connection.executable_path = result.get("executable_path", connection.executable_path)
    connection.local_endpoint = result.get("server_url", connection.local_endpoint)
    connection.configured_provider = connection.configured_model.split("/", 1)[0] if "/" in connection.configured_model else ""
    connection.last_checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {**result, "connection": connection.to_dict()}


@app.post("/api/provider-connections/claude/detect")
def detect_claude_code_connection(payload: ClaudeCodeConnectionPayload):
    """Discover the Claude Code CLI and Node/npm without touching its OAuth session."""
    dependencies = get_claude_code_onboarding_dependencies()
    claude_dependency = dependencies["components"]["claude"]
    binary = claude_dependency["path"]
    catalog = _CLI_SUBSCRIPTION_MODEL_CATALOG.get("claude_subscription", {})
    return {
        "status": "detected" if binary else "executable_not_found",
        "executable_path": binary,
        "version": claude_dependency["version"],
        "available_models": [{"id": model_id, "display_name": label} for model_id, label in catalog.items()],
        "dependencies": dependencies,
        "authentication": "Authentication is owned by the official Claude Code CLI and is verified only by Test Connection.",
    }


@app.post("/api/provider-connections/claude/test")
def test_transient_claude_code_connection(payload: ClaudeCodeConnectionPayload):
    """Verify the Claude Code CLI is installed and logged in, without invoking a paid request."""
    result = test_claude_code_readiness(payload.executable_path.strip())
    return {
        **result,
        "connection": {
            "connection_id": payload.connection_id.strip() or "claude-subscription",
            "name": payload.name.strip() or "My Claude Code",
            "configured_model": payload.configured_model.strip() or "claude/default",
            "executable_path": result.get("executable_path", payload.executable_path),
            "last_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
    }


@app.post("/api/provider-connections/claude")
def save_claude_code_connection(payload: ClaudeCodeConnectionPayload):
    selected_model = str(_normalize_model_for_connection(payload.configured_model.strip() or "claude/default", payload.connection_id).get("model_id") or payload.configured_model.strip() or "claude/default")
    result = test_claude_code_readiness(payload.executable_path.strip())
    if result.get("ready") is not True:
        raise HTTPException(409, {"error_code": result.get("error_code", "readiness_failed"), "message": result.get("message", "Claude Code readiness check failed")})

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connection = {
        "connection_id": payload.connection_id.strip() or "claude-subscription",
        "connection_type": "claude_subscription",
        "name": payload.name.strip() or "My Claude Code",
        "provider": "anthropic",
        "enabled": bool(payload.enabled),
        "executable_path": result["executable_path"],
        "configured_provider": "anthropic",
        "configured_model": selected_model,
        "auth_type": "delegated_cli_login",
        "auth_status": "authenticated",
        "readiness_status": "ready",
        "priority": 30,
        "last_checked_at": now,
        "created_at": now,
        "updated_at": now,
    }
    data = load_studio_keys()
    connections = _load_provider_connections(data)
    existing = next((i for i, item in enumerate(connections) if item.get("connection_id") == connection["connection_id"]), None)
    if existing is None:
        connections.append(connection)
    else:
        connections[existing] = {**connections[existing], **connection}
    _store_provider_connections(data, connections)
    data["_global_ai"] = {
        "connection_id": connection["connection_id"],
        "connection_type": "claude_subscription",
        "provider": "anthropic",
        "model": selected_model,
        "enabled": bool(payload.enabled),
        "updated_at": now,
    }
    system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
    system["global_provider"] = "anthropic"
    system["global_model"] = selected_model
    data["_system"] = system
    SYSTEM_SETTINGS["global_provider"] = "anthropic"
    SYSTEM_SETTINGS["global_model"] = selected_model
    save_studio_keys(data)
    return {"status": "saved", "connection": _sanitize_provider_connection(connection), "effective_execution_config": build_effective_execution_config(data).to_dict(), "message": "Claude Code authentication remains owned by the official CLI; only verified connection metadata was stored."}




@app.get("/api/provider-connections/{connection_id}")
async def get_universal_provider_connection(connection_id: str):
    if connection_id == "vision":
        return list_vision_provider_connections()
    connection_data = next((item for item in _universal_provider_connections() if item.get("connection_id") == connection_id), None)
    if not connection_data:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    connection = ProviderConnection(**{key: value for key, value in connection_data.items() if key in ProviderConnection.__dataclass_fields__})
    return await _provider_connection_state(connection)


@app.patch("/api/provider-connections/{connection_id}")
def patch_universal_provider_connection(connection_id: str, payload: UniversalProviderConnectionPatch):
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    index = _universal_connection_index(connections, connection_id)
    if index is None:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    current = dict(connections[index])
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "model_id" and "/" in str(value) and str(value).startswith(f"{connection_id}/"):
            value = str(value).split("/", 1)[1]
        current[key] = value
    if current.get("connection_type") not in provider_registry.registered_types():
        raise HTTPException(400, {"error_code": "unsupported_connection_type", "message": "Connection type is not registered."})
    connections[index] = _sanitize_provider_connection_for_storage(current)
    connections.sort(key=lambda item: int(item.get("priority", 100)))
    _save_universal_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "updated", "connection": connections[_universal_connection_index(connections, connection_id)]}


@app.delete("/api/provider-connections/{connection_id}")
def delete_universal_provider_connection(connection_id: str, payload: ProviderDeletePayload | None = None):
    payload = payload or ProviderDeletePayload()
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    index = _universal_connection_index(connections, connection_id)
    if index is None:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    connection = ProviderConnection(**{key: value for key, value in connections[index].items() if key in ProviderConnection.__dataclass_fields__})
    used_by = _connection_used_by_agents(data, connection_id)
    if used_by and not payload.confirmed:
        raise HTTPException(409, {"error_code": "connection_in_use", "message": "Connection is assigned to agents.", "agents": used_by})
    removed = connections.pop(index)
    _cleanup_connection_assignments(data, connection_id)
    if payload.delete_credential and connection.credential_reference:
        ProviderCredentialStore(default_backend()).delete_api_key(connection.credential_reference)
    _save_universal_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "deleted", "connection_id": connection_id, "credential_deleted": bool(payload.delete_credential and connection.credential_reference), "used_by_agents": used_by, "removed": _sanitize_provider_connection_for_storage(removed)}


@app.post("/api/provider-connections/{connection_id}/credential")
def save_universal_provider_credential(connection_id: str, payload: ProviderCredentialPayload):
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    index = _universal_connection_index(connections, connection_id)
    if index is None:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    current = dict(connections[index])
    if "api_key" not in str(current.get("connection_type") or ""):
        raise HTTPException(409, {"error_code": "unsupported_action", "message": "This connection does not use API key credentials."})
    ref = str(current.get("credential_reference") or credential_reference(str(current.get("provider_id") or "provider"), connection_id))
    store = ProviderCredentialStore(default_backend())
    try:
        saved = store.save_api_key(ref, payload.api_key)
    except Exception as exc:
        raise HTTPException(409, {"error_code": "credential_backend_unavailable", "message": str(exc), "credential_backend": store.diagnostics()})
    current["credential_reference"] = ref
    current["metadata"] = {**(current.get("metadata") if isinstance(current.get("metadata"), dict) else {}), "credential_updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "masked_key": saved.get("masked")}
    connections[index] = current
    _save_universal_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "saved", "credential_reference": ref, "masked": saved.get("masked"), "credential_backend": store.diagnostics()}


@app.delete("/api/provider-connections/{connection_id}/credential")
def delete_universal_provider_credential(connection_id: str):
    data = load_studio_keys()
    connections = _universal_provider_connections(data)
    index = _universal_connection_index(connections, connection_id)
    if index is None:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    ref = str(connections[index].get("credential_reference") or "")
    if ref:
        ProviderCredentialStore(default_backend()).delete_api_key(ref)
    connections[index]["metadata"] = {**(connections[index].get("metadata") if isinstance(connections[index].get("metadata"), dict) else {}), "credential_deleted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
    _save_universal_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "deleted", "credential_reference": ref}


@app.post("/api/provider-connections/{connection_id}/login")
async def login_universal_provider_connection(connection_id: str):
    connection_data = next((item for item in _universal_provider_connections() if item.get("connection_id") == connection_id), None)
    if not connection_data:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    connection = ProviderConnection(**{key: value for key, value in connection_data.items() if key in ProviderConnection.__dataclass_fields__})
    if connection.auth_method != AuthMethod.DELEGATED_CLI_LOGIN.value:
        raise HTTPException(409, {"error_code": "unsupported_action", "message": "Login is only available for delegated CLI connections."})
    result = await provider_registry.create(connection).authenticate()
    return {"connection_id": connection_id, "result": result.to_dict()}


@app.post("/api/provider-connections/{connection_id}/logout")
async def logout_universal_provider_connection(connection_id: str):
    connection_data = next((item for item in _universal_provider_connections() if item.get("connection_id") == connection_id), None)
    if not connection_data:
        raise HTTPException(404, {"error_code": "connection_not_found", "message": "Provider connection not found."})
    connection = ProviderConnection(**{key: value for key, value in connection_data.items() if key in ProviderConnection.__dataclass_fields__})
    if connection.auth_method == AuthMethod.API_KEY.value:
        raise HTTPException(409, {"error_code": "unsupported_action", "message": "API key connections do not support logout; remove the credential instead."})
    await provider_registry.create(connection).logout()
    return {"status": "logged_out", "connection_id": connection_id}


@app.post("/api/provider-connections/{connection_id}/test")
async def test_universal_provider_connection_action(connection_id: str):
    if any(item.get("connection_id") == connection_id for item in _universal_provider_connections()):
        return await test_universal_provider_connection(connection_id)
    return test_provider_connection(connection_id)


@app.get("/api/provider-connections/vision")
def list_vision_provider_connections():
    connections = [item for item in _frontend_provider_connections() if item.get("tested_status") == "passed" and _vision_models_for_connection(item)]
    return {"connections": connections}


@app.get("/api/provider-connections/{connection_id}/models")
async def list_provider_connection_models(connection_id: str, image_input: bool = False):
    universal = next((item for item in _universal_provider_connections() if item.get("connection_id") == connection_id), None)
    if universal:
        connection = ProviderConnection(**{key: value for key, value in universal.items() if key in ProviderConnection.__dataclass_fields__})
        adapter = provider_registry.create(connection)
        capabilities = await adapter.get_capabilities()
        if not capabilities.model_listing:
            return {"connection_id": connection_id, "models": [], "supported": False, "message": "Model listing is not supported; enter a model ID manually."}
        models = [item.to_dict() for item in await adapter.list_models()]
        selected_available = not connection.model_id or any(item.get("id") == connection.model_id for item in models)
        return {"connection_id": connection_id, "models": models, "supported": True, "selected_model_id": connection.model_id, "selected_available": selected_available}
    connection = _connection_for_id(connection_id)
    if not connection:
        raise HTTPException(404, "Provider connection not found")
    models = _vision_models_for_connection(connection) if image_input else connection.get("available_models", [])
    return {"connection_id": connection_id, "models": models}


@app.post("/api/provider-connections/{connection_id}/models/refresh")
def refresh_provider_connection_models(connection_id: str):
    data = load_studio_keys()
    connections = _load_provider_connections(data)
    index = next((i for i, item in enumerate(connections) if item.get("connection_id") == connection_id), None)
    if index is None:
        raise HTTPException(404, "Provider connection not found")
    connection = connections[index]
    ctype = _canonical_connection_type(str(connection.get("connection_type") or ""), str(connection.get("provider") or ""))
    if _connection_uses_opencode_transport(connection):
        ctype = "opencode_oauth_bridge"
        oc = OpenCodeBridgeConnection.from_dict(connection)
        models = oc.available_models()
        connection["available_models"] = models
        connection["capability_metadata"] = {str(item.get("id") if isinstance(item, dict) else item): {} for item in models if item}
    else:
        provider = str(connection.get("provider") or "").lower()
        connection.update(_general_provider_connection(data, provider, connection.get("configured_model", "")))
    connection["connection_type"] = ctype
    connection["models_refreshed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connections[index] = connection
    _store_provider_connections(data, connections)
    save_studio_keys(data)
    return {"status": "refreshed", "connection": _sanitize_provider_connection(connection)}


@app.post("/api/provider-connections/{connection_id}/legacy-test")
def test_provider_connection(connection_id: str):
    data = load_studio_keys()
    connections = _load_provider_connections(data)
    index = next((i for i, item in enumerate(connections) if item.get("connection_id") == connection_id), None)
    if index is None:
        raise HTTPException(404, "Provider connection not found")
    if not _connection_uses_opencode_transport(connections[index]):
        provider = str(connections[index].get("provider") or "").lower()
        key = _provider_api_key(provider, data)
        ok, message = _test_provider_key(provider, key)
        connection = _general_provider_connection(data, provider, connections[index].get("configured_model", ""), ok, {"status": "ok" if ok else "error", "message": message})
        connections[index] = connection
        _store_provider_connections(data, connections)
        save_studio_keys(data)
        return {"status": "ok" if ok else "error", "connection": _sanitize_provider_connection(connection), "message": message}
    connection = OpenCodeBridgeConnection.from_dict(connections[index])
    connection.configured_model = str(_normalize_model_for_connection(connection.configured_model, connection.connection_id, data).get("model_id") or connection.configured_model)
    result = test_opencode_readiness(connection.executable_path, connection.configured_model, connection.local_endpoint)
    connection.executable_path = result.get("executable_path", connection.executable_path)
    connection.local_endpoint = result.get("server_url", connection.local_endpoint)
    connection.configured_provider = connection.configured_model.split("/", 1)[0] if "/" in connection.configured_model else ""
    connection.last_checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connections[index] = connection.to_dict()
    _store_provider_connections(data, connections)
    save_studio_keys(data)
    return {**result, "connection": _sanitize_provider_connection(connections[index])}


@app.post("/api/config/ai")
def save_ai_settings(payload: AISettingsPayload):
    provider = (payload.provider or "").strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    model = (payload.model or "").strip() or AI_PROVIDER_MODELS[provider][0]
    data = load_studio_keys()
    system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
    system["global_provider"] = provider
    system["global_model"] = model
    data["_system"] = system
    SYSTEM_SETTINGS["global_provider"] = provider
    SYSTEM_SETTINGS["global_model"] = model
    secret_warning = "" if not payload.api_key.strip() else "API key was not written to studio_config.json. Set the matching environment variable to persist it."
    _upsert_provider_connection(data, _general_provider_connection(data, provider, model))
    global_ai = _load_global_ai_config(data)
    global_ai.update({"connection_id": _provider_connection_id(provider), "provider": provider, "model": model, "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")})
    data["_global_ai"] = global_ai
    save_studio_keys(data)
    response = {"status": "saved", **_ai_settings_response(data)}
    if secret_warning:
        response["secret_store_warning"] = secret_warning
    return response


@app.delete("/api/config/ai/key/{provider}")
def delete_ai_provider_key(provider: str):
    provider = provider.strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    data = load_studio_keys()
    existed = False
    for key_name in (_provider_key_name(provider), f"{provider}_api_key"):
        existed = key_name in data or existed
        data.pop(key_name, None)
    connection = _connection_for_id(_provider_connection_id(provider), data)
    if connection:
        _upsert_provider_connection(data, _general_provider_connection(data, provider, connection.get("configured_model", ""), None, {"status": "credential_missing", "message": "Credential deleted"}))
    save_studio_keys(data)
    return {"status": "deleted", "provider": provider, "existed": existed, **_ai_settings_response(data)}


@app.post("/api/config/ai/test")
def test_ai_provider(payload: ProviderTestPayload):
    data = load_studio_keys()
    system = _get_saved_system_settings(data)
    provider = (payload.provider or system.get("global_provider", "nvidia")).strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    key = payload.api_key.strip() if payload.api_key else _provider_api_key(provider, data)
    ok, message = _test_provider_key(provider, key)
    current_model = system.get("global_model") if system.get("global_provider") == provider else ""
    _upsert_provider_connection(data, _general_provider_connection(data, provider, current_model, ok, {"status": "ok" if ok else "error", "message": message}))
    save_studio_keys(data)
    response = {"status": "ok" if ok else "error", "provider": provider, "key_saved": bool(key), "message": message, "connections": _frontend_provider_connections(data)}
    if payload.api_key and payload.api_key.strip():
        response["secret_store_warning"] = "The tested API key was not written to studio_config.json. Set the matching environment variable to persist it."
    return response


@app.post("/api/config/ai/apply-opencode")
def apply_ai_settings_to_opencode():
    if not _HAS_OPENCODE:
        raise HTTPException(500, "OpenCode bridge is not available")
    ok = sync_opencode_config()
    if not ok:
        raise HTTPException(500, "Failed to write OpenCode config")
    return {"status": "applied", "message": "OpenCode config regenerated. Restart OpenCode to use the new settings."}


@app.post("/api/config/ai/verify")
def verify_ai_settings():
    data = load_studio_keys()
    system = _get_saved_system_settings(data)
    provider = system.get("global_provider", "nvidia")
    model = system.get("global_model") or AI_PROVIDER_MODELS.get(provider, [""])[0]
    key_exists = provider == "ollama" or bool(_provider_api_key(provider, data))
    opencode_generated = False
    mcp_exists = False
    if _HAS_OPENCODE:
        opencode_generated = sync_opencode_config()
        config_path = os.path.join(os.path.expanduser("~/.config/opencode"), "opencode.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                oc = json.load(f)
            mcp_exists = bool(oc.get("mcp", {}).get("freelancer-studio"))
        except Exception:
            mcp_exists = False
    return {
        "provider": provider,
        "model": model,
        "key_exists": key_exists,
        "opencode_config_generated": opencode_generated,
        "mcp_config_exists": mcp_exists,
    }



# ── Resume / Phase tracking ──────────────────────────────────────────────────
_PHASE_ORDER = ["planning", "designing", "testing", "coding", "review", "qa", "product_judge"]

_TERMINAL_PROJECT_STATES = {
    "completed",
    "failed",
    "failed_qa",
    "failed_final_audit",
    "blocked",
    "needs_credentials",
    "cancelled",
}


_ALLOWED_STATE_TRANSITIONS = {
    "created": {"meeting", "planning", "cancelled", "needs_human_input", "needs_credentials"},
    "meeting": {"planning", "cancelled", "blocked", "failed", "needs_credentials", "needs_human_input"},
    "planning": {"awaiting_input", "designing", "cancelled", "failed", "blocked", "needs_human_input", "needs_credentials"},
    "awaiting_input": {"planning", "designing", "cancelled", "needs_human_input"},
    "designing": {"testing", "cancelled", "failed", "blocked"},
    "testing": {"coding", "verifying", "cancelled", "failed", "blocked"},
    "coding": {"review", "verifying", "cancelled", "failed", "blocked", "needs_credentials"},
    "review": {"review", "verifying", "repairing", "cancelled", "failed", "blocked"},
    "verifying": {"repairing", "final_audit", "needs_user_input", "needs_human_input", "needs_credentials", "failed_qa", "cancelled", "failed", "blocked"},
    "repairing": {"review", "verifying", "final_audit", "failed_qa", "cancelled", "failed", "blocked"},
    "final_audit": {"product_judge", "completed", "repairing", "failed_final_audit", "cancelled", "failed", "blocked"},
    "product_judge": {"completed", "repairing", "failed_qa", "cancelled", "failed", "blocked"},
    "needs_user_input": {"verifying", "cancelled", "failed_qa", "failed"},
    "needs_human_input": {"planning", "verifying", "cancelled", "failed"},
    "failed_qa": {"planning", "verifying", "cancelled"},
    "failed_final_audit": {"verifying", "repairing", "cancelled"},
    "failed": {"planning", "verifying", "cancelled"},
    "blocked": {"planning", "verifying", "cancelled"},
    "needs_credentials": {"planning", "verifying", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


agent_configs = load_agent_configs()


def _set_agent_configs(value):
    global agent_configs
    agent_configs = value


_agent_ai_resolution = build_agent_ai_resolution(
    lambda: agent_configs,
    _set_agent_configs,
    _HAS_OPENCODE,
    (lambda: test_opencode_readiness) if _HAS_OPENCODE else None,
)
_agent_default_config = _agent_ai_resolution._agent_default_config
_all_agent_ids = _agent_ai_resolution._all_agent_ids
_agent_meta = _agent_ai_resolution._agent_meta
resolve_effective_agent_ai_config = _agent_ai_resolution.resolve_effective_agent_ai_config
get_agent_provider_model = _agent_ai_resolution.get_agent_provider_model
agent_opencode_model_override = _agent_ai_resolution.agent_opencode_model_override
resolve_agent_provider_connection = _agent_ai_resolution.resolve_agent_provider_connection
_product_judge_independence = _agent_ai_resolution._product_judge_independence
_product_judge_status = _agent_ai_resolution._product_judge_status
_configured_product_judge_runtime = _agent_ai_resolution._configured_product_judge_runtime
_save_repaired_opencode_model = _agent_ai_resolution._save_repaired_opencode_model
_opencode_preflight_for_agent = _agent_ai_resolution._opencode_preflight_for_agent


agent_statuses: Dict[str, Dict[str, str]] = {}


@app.get("/api/agents/status")
def get_agent_statuses():
    statuses = {}
    for agent_id, meta in DEFAULT_AGENTS.items():
        st = agent_statuses.get(agent_id, {})
        statuses[agent_id] = {
            "status": st.get("status", "idle"),
            "task": st.get("task", ""),
            "project_id": st.get("project_id", ""),
        }
    return statuses


def set_agent_status(agent_id: str, status: str, task: str = "", project_id: str = ""):
    agent_statuses[agent_id] = {"status": status, "task": task, "project_id": project_id}


def clear_agent_status(agent_id: str):
    agent_statuses.pop(agent_id, None)


@app.get("/api/agents")
def get_agents():
    result = {}
    for agent_id, agent in DEFAULT_AGENTS.items():
        cfg = agent_configs.get(agent_id, {})
        effective = resolve_effective_agent_ai_config(agent_id)
        entry = {**agent, "id": agent_id}
        entry.update(AGENT_STAGE_METADATA.get(agent_id, {}))
        entry["enabled"] = cfg.get("enabled", agent.get("enabled", True))
        entry["top_p"] = cfg.get("top_p", agent.get("top_p"))
        entry["top_k"] = cfg.get("top_k", agent.get("top_k"))
        entry["max_tokens"] = cfg.get("max_tokens", agent.get("max_tokens"))
        entry["use_global_connection"] = effective["use_global_connection"]
        entry["use_global_model"] = effective["use_global_model"]
        entry["use_global_generation_parameters"] = effective["use_global_generation_parameters"]
        entry["effective_ai"] = effective
        entry["role_contract"] = role_contract_for(agent_id)
        entry["role_contract_summary"] = entry["role_contract"].get("summary", "")
        entry["recommended_defaults"] = recommended_defaults_for(agent_id)
        if agent_id == "product_judge":
            entry["connection_id"] = cfg.get("connection_id", "")
            readiness = _product_judge_status({**agent, **cfg})
            entry["product_judge_status"] = readiness["status"]
            entry["product_judge_reason"] = readiness["reason"]
            entry["independence_level"] = readiness.get("independence_level", "unavailable")
        if cfg.get("custom_prompt"):
            entry["custom_prompt"] = cfg["custom_prompt"]
        if cfg.get("save_path"):
            entry["save_path"] = cfg["save_path"]
        if cfg.get("use_global") is False:
            entry["provider"] = cfg.get("provider", agent["provider"])
            entry["model"] = cfg.get("model", agent["model"])
            entry["temperature"] = cfg.get("temperature", agent["temperature"])
            entry["use_global"] = False
            entry["active_provider"] = effective["provider"]
            entry["active_model"] = effective["model"]
        else:
            entry["use_global"] = True
            entry["active_provider"] = effective["provider"]
            entry["active_model"] = effective["model"]
        result[agent_id] = entry
    for cid, cagent in agent_configs.get("_custom_agents", {}).items():
        cfg = agent_configs.get(cid, {})
        effective = resolve_effective_agent_ai_config(cid)
        entry = {**cagent, "id": cid, "builtin": False}
        entry.setdefault("stage", cagent.get("stage", "custom"))
        entry.setdefault("display_role", cagent.get("role", "Custom Agent"))
        entry["enabled"] = cfg.get("enabled", cagent.get("enabled", True))
        entry["top_p"] = cfg.get("top_p", cagent.get("top_p"))
        entry["top_k"] = cfg.get("top_k", cagent.get("top_k"))
        entry["max_tokens"] = cfg.get("max_tokens", cagent.get("max_tokens"))
        entry["use_global_connection"] = effective["use_global_connection"]
        entry["use_global_model"] = effective["use_global_model"]
        entry["use_global_generation_parameters"] = effective["use_global_generation_parameters"]
        entry["effective_ai"] = effective
        entry["role_contract"] = role_contract_for(cid)
        entry["role_contract_summary"] = entry["role_contract"].get("summary", "")
        entry["recommended_defaults"] = recommended_defaults_for(cid)
        if cfg.get("custom_prompt"):
            entry["custom_prompt"] = cfg["custom_prompt"]
        if cfg.get("save_path"):
            entry["save_path"] = cfg["save_path"]
        if cfg.get("use_global") is False:
            entry["provider"] = cfg.get("provider", cagent.get("provider", SYSTEM_SETTINGS["global_provider"]))
            entry["model"] = cfg.get("model", cagent.get("model", SYSTEM_SETTINGS["global_model"]))
            entry["temperature"] = cfg.get("temperature", cagent.get("temperature", 0.2))
            entry["use_global"] = False
            entry["active_provider"] = effective["provider"]
            entry["active_model"] = effective["model"]
        else:
            entry["use_global"] = True
            entry["active_provider"] = effective["provider"]
            entry["active_model"] = effective["model"]
        result[cid] = entry
    return result






@app.post("/api/agents/{agent_id}/config")
def update_agent_config(agent_id: str, payload: AgentAIConfigPayload):
    if agent_id not in _all_agent_ids():
        raise HTTPException(status_code=404, detail="Agent not found")
    current = agent_configs.get(agent_id, {})
    current = _validated_agent_ai_patch(agent_id, current, payload.model_dump(exclude_unset=True))
    agent_configs[agent_id] = current
    save_agent_configs(agent_configs)
    return {"status": "success", "agent_id": agent_id, "config": current, "effective_ai": resolve_effective_agent_ai_config(agent_id)}


@app.get("/api/agents/{agent_id}/config")
def get_agent_config(agent_id: str):
    if agent_id not in _all_agent_ids():
        raise HTTPException(status_code=404, detail="Agent not found")
    current = {
        "use_global_connection": True,
        "use_global_model": True,
        "use_global_generation_parameters": True,
        **agent_configs.get(agent_id, {}),
    }
    return {"agent_id": agent_id, "config": current, "effective_ai": resolve_effective_agent_ai_config(agent_id)}


@app.get("/api/product-judge/config")
def get_product_judge_config():
    cfg = {**DEFAULT_AGENTS["product_judge"], **agent_configs.get("product_judge", {})}
    status = _product_judge_status(cfg)
    connections = [item for item in _frontend_provider_connections() if item.get("enabled", True) and _vision_models_for_connection(item)]
    return {
        "config": {
            "enabled": bool(cfg.get("enabled", True)),
            "connection_id": cfg.get("connection_id", ""),
            "model": cfg.get("model", ""),
            "use_global": bool(cfg.get("use_global", False)),
            "temperature": cfg.get("temperature", 0.3),
            "top_p": cfg.get("top_p", 0.9),
            "top_k": cfg.get("top_k"),
            "last_successful_readiness": cfg.get("last_successful_readiness", {}),
            "independence_level": cfg.get("independence_level") or status.get("independence_level", "unavailable"),
        },
        "status": status,
        "connections": connections,
    }


@app.post("/api/product-judge/config")
def save_product_judge_config(payload: ProductJudgeConfigPayload):
    data = load_studio_keys()
    global_cfg = _load_global_ai_config(data)
    connection = _connection_for_id(payload.connection_id, data) if not payload.use_global else _connection_for_id(str(global_cfg.get("connection_id") or _provider_connection_id(_get_saved_system_settings(data).get("global_provider", ""))), data)
    provider, identity_model = _identity_for_product_judge(connection, payload.model, payload.use_global) if connection else ("", "")
    independence = _product_judge_independence(provider, identity_model)
    current = agent_configs.get("product_judge", {})
    current.update({
        "enabled": payload.enabled,
        "use_global": payload.use_global,
        "provider": connection.get("provider", "") if connection else "",
        "connection_id": connection.get("connection_id", payload.connection_id) if connection else payload.connection_id,
        "model": payload.model,
        "temperature": payload.temperature,
        "top_p": payload.top_p,
        "top_k": payload.top_k,
        "independence_level": independence,
    })
    status = _product_judge_status(current, data)
    if status.get("available"):
        current["last_successful_readiness"] = {"status": status["status"], "reason": status["reason"], "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
    agent_configs["product_judge"] = current
    save_agent_configs(agent_configs)
    return {"status": "success", "config": current, "readiness": status}


@app.post("/api/product-judge/test")
def test_product_judge_readiness(payload: ProductJudgeConfigPayload | None = None):
    cfg = {**DEFAULT_AGENTS["product_judge"], **agent_configs.get("product_judge", {})}
    if payload is not None:
        cfg.update(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    status = _product_judge_status(cfg)
    if status.get("available"):
        status["test_result"] = "ready"
        status["message"] = "Credential reference exists, connection is tested, selected model exists, and image input is supported."
    else:
        status["test_result"] = "not_ready"
        status["message"] = status.get("reason") or "Product Judge is not ready."
    return status


class CreateAgentPayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    agent_id: str = ""
    name: str = "Custom Agent"
    role: str = "custom"
    provider: str = "nvidia"
    model: str = "nvidia/nemotron-4-340b-instruct"
    temperature: float = 0.2
    custom_prompt: str = ""
    use_global: bool = True
    save_path: str = ""


@app.post("/api/agents")
def create_agent(payload: CreateAgentPayload):
    cid = payload.agent_id or f"custom_{uuid.uuid4().hex[:8]}"
    if cid in _all_agent_ids():
        raise HTTPException(status_code=400, detail=f"Agent ID '{cid}' already exists")
    custom_agents = agent_configs.get("_custom_agents", {})
    custom_agents[cid] = {
        "name": payload.name,
        "role": payload.role,
        "enabled": True,
        "builtin": False,
        "status": "idle",
        "provider": payload.provider,
        "model": payload.model,
        "temperature": payload.temperature,
        "save_path": payload.save_path,
        "use_global": payload.use_global,
    }
    agent_configs["_custom_agents"] = custom_agents
    if payload.custom_prompt:
        agent_configs[cid] = {"custom_prompt": payload.custom_prompt}
    save_agent_configs(agent_configs)
    return {"status": "created", "agent_id": cid, "name": payload.name}


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    if agent_id in DEFAULT_AGENTS:
        agent_configs[agent_id] = {"enabled": False}
        save_agent_configs(agent_configs)
        return {"status": "disabled", "agent_id": agent_id, "note": "Built-in agents cannot be deleted; disabled instead."}
    custom_agents = agent_configs.get("_custom_agents", {})
    if agent_id not in custom_agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    del custom_agents[agent_id]
    agent_configs["_custom_agents"] = custom_agents
    agent_configs.pop(agent_id, None)
    save_agent_configs(agent_configs)
    return {"status": "deleted", "agent_id": agent_id}


def iter_app_route_paths(routes) -> list[str]:
    """Flatten FastAPI route paths, including lazily-wrapped included routers.

    Newer FastAPI versions represent app.include_router(...) as an opaque
    _IncludedRouter wrapper (no .path) instead of eagerly flattening the
    sub-router's routes, so a plain `r.path for r in app.routes` crashes
    with AttributeError once a router has been included this way.
    """
    paths: list[str] = []
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.append(path)
            continue
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            paths.extend(iter_app_route_paths(getattr(original_router, "routes", [])))
    return paths


def find_app_routes(app_or_routes, *, path: str, method: str | None = None) -> list[Any]:
    """Resolve routes matching a path (and optional method), flattening included routers."""
    routes = getattr(app_or_routes, "routes", app_or_routes)
    matches: list[Any] = []
    for route in routes:
        route_path = getattr(route, "path", None)
        if route_path is not None:
            if route_path == path and (method is None or method in getattr(route, "methods", set())):
                matches.append(route)
            continue
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            matches.extend(find_app_routes(original_router, path=path, method=method))
    return matches


@app.get("/api/debug/frontend", include_in_schema=False)
async def debug_frontend():
    return {
        "BASE_DIR": BASE_DIR,
        "FRONTEND_DIST": FRONTEND_DIST,
        "isdir": os.path.isdir(FRONTEND_DIST),
        "index_exists": os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")),
        "assets_exists": os.path.isdir(os.path.join(FRONTEND_DIST, "assets")),
        "routes": iter_app_route_paths(app.routes),
    }

if os.path.isdir(FRONTEND_DIST):
    print(f"[Backend]: Frontend dist found at {FRONTEND_DIST}")

    FRONTEND_SHELL_CACHE_HEADERS = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    def _is_hashed_frontend_asset(path: str) -> bool:
        normalized = path.replace("\\", "/")
        name = os.path.basename(normalized)
        stem, ext = os.path.splitext(name)
        return normalized.startswith("assets/") and ext.lower() in {".js", ".css", ".png", ".svg", ".ico", ".woff2"} and bool(re.search(r"-[A-Za-z0-9_-]{6,}$", stem))

    def _frontend_cache_headers(path: str, *, spa_shell: bool = False) -> dict[str, str]:
        if spa_shell or path == "index.html" or path.endswith(".html"):
            return FRONTEND_SHELL_CACHE_HEADERS
        if _is_hashed_frontend_asset(path):
            return {"Cache-Control": "public, max-age=31536000, immutable"}
        return {"Cache-Control": "no-cache, max-age=0"}

    @app.get("/", include_in_schema=False)
    async def serve_index():
        idx = os.path.join(FRONTEND_DIST, "index.html")
        if not os.path.isfile(idx):
            raise HTTPException(404)
        with open(idx, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html, media_type="text/html", headers=_frontend_cache_headers("index.html", spa_shell=True))

    MEDIA_TYPES = {".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2", ".html": "text/html", ".map": "application/json"}

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        if path.startswith("api/") or path in ("openapi.json", "docs", "redoc"):
            raise HTTPException(404)
        file_path = os.path.join(FRONTEND_DIST, path)
        if os.path.isfile(file_path):
            ext = os.path.splitext(path)[1].lower()
            return FileResponse(file_path, media_type=MEDIA_TYPES.get(ext), headers=_frontend_cache_headers(path))
        idx = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.isfile(idx):
            return FileResponse(idx, media_type="text/html", headers=_frontend_cache_headers("index.html", spa_shell=True))
        raise HTTPException(404)
else:
    print(f"[Backend Warning]: Frontend dist not found at {FRONTEND_DIST}")


def run_backend():
    import uvicorn
    import socket

    def find_free_port(start_port: int = 8080, max_attempts: int = 20, host: str = "127.0.0.1") -> int:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        for port in range(start_port, start_port + max_attempts):
            with socket.socket(family, socket.SOCK_STREAM) as s:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((host, port))
                    return port
                except OSError:
                    continue
        raise IOError("Could not find any available network ports in the system registry.")

    start_port = int(os.environ.get("BACKEND_START_PORT", "8080"))
    bind_host = get_backend_bind_host()
    free_port = find_free_port(start_port=start_port, host=bind_host)
    startup_token = None
    launch_id = None
    if os.environ.pop("FREELANCERSTUDIO_AUTH_STDIN", "") == "1":
        try:
            startup_payload = json.loads(sys.stdin.readline(4096))
            startup_token = str(startup_payload.get("token") or "")
            launch_id = str(startup_payload.get("launch_id") or "")
        except (AttributeError, json.JSONDecodeError, OSError, ValueError):
            raise RuntimeError("Trusted backend authorization bootstrap failed") from None
    security_context = LocalSecurityContext.create(
        token=startup_token,
        bind_host=bind_host,
        port=free_port,
        launch_id=launch_id,
        allowed_origins=get_cors_origins(),
    )
    set_app_security_context(app, security_context)
    control_fd = os.environ.pop("FREELANCERSTUDIO_CONTROL_FD", "")
    if control_fd:
        connect_host = "::1" if bind_host == "::" else "127.0.0.1" if bind_host == "0.0.0.0" else bind_host
        descriptor = {
            "port": free_port,
            "bind_host": bind_host,
            "connect_host": connect_host,
            "launch_id": security_context.launch_id,
            "instance_id": security_context.instance_id,
        }
        try:
            os.write(int(control_fd), (json.dumps(descriptor, separators=(",", ":")) + "\n").encode("utf-8"))
            os.close(int(control_fd))
        except (OSError, ValueError):
            raise RuntimeError("Trusted backend control channel failed") from None
    print(f"[Backend Boot]: Network cluster allocated securely on {bind_host}:{free_port}")

    os.makedirs(RUNTIME_DIR, exist_ok=True)
    port_file = os.path.join(RUNTIME_DIR, "studio_port.txt")
    try:
        with open(port_file, "w", encoding="utf-8") as f:
            f.write(str(free_port))
    except Exception as e:
        print(f"[Backend Warning]: Failed to write port file: {e}")

    uvicorn.run(app, host=bind_host, port=free_port)


if __name__ == "__main__":
    run_backend()
