from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


OPENCODE_CONNECTION_TYPES = {"opencode", "opencode_bridge", "opencode_oauth_bridge"}
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+/.+")


@dataclass(frozen=True)
class EffectiveExecutionConfig:
    connection_id: str
    provider_id: str
    model_id: str
    execution_mode: str
    live_execution_enabled: bool
    project_root: str
    backend_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_opencode_connection_type(value: str) -> bool:
    return str(value or "").strip().lower() in OPENCODE_CONNECTION_TYPES


def provider_connection_id(provider_id: str) -> str:
    return f"provider-{str(provider_id or '').strip().lower()}"


def known_connection_ids(config: dict[str, Any] | None) -> set[str]:
    source = config if isinstance(config, dict) else {}
    items = source.get("_provider_connections", [])
    if not isinstance(items, list):
        return set()
    return {str(item.get("connection_id")) for item in items if isinstance(item, dict) and item.get("connection_id")}


def normalize_model_id(model_id: str, connection_id: str = "", known_ids: set[str] | None = None) -> dict[str, Any]:
    raw = str(model_id or "").strip()
    known = {str(item) for item in (known_ids or set()) if item}
    if connection_id:
        known.add(str(connection_id))
    if raw and "/" in raw:
        first, rest = raw.split("/", 1)
        if first in known and rest and "/" in rest:
            return {
                "model_id": rest,
                "connection_id": first,
                "provider_id": rest.split("/", 1)[0],
                "migrated": True,
                "legacy_model": raw,
            }
    return {
        "model_id": raw,
        "connection_id": connection_id or "",
        "provider_id": raw.split("/", 1)[0] if "/" in raw else "",
        "migrated": False,
        "legacy_model": raw,
    }


def validate_native_model_id(model_id: str, backend_type: str = "opencode") -> tuple[bool, str]:
    value = str(model_id or "").strip()
    if not value:
        return False, "model_not_selected"
    if backend_type == "opencode" and not MODEL_ID_RE.match(value):
        return False, "model_id_must_use_provider_slash_model"
    return True, ""


def _first_opencode_connection(config: dict[str, Any]) -> dict[str, Any]:
    for item in config.get("_provider_connections", []) if isinstance(config.get("_provider_connections"), list) else []:
        if isinstance(item, dict) and is_opencode_connection_type(item.get("connection_type")) and item.get("enabled", True):
            return item
    return {}


def build_effective_execution_config(config: dict[str, Any] | None, project_root: str = "") -> EffectiveExecutionConfig:
    source = config if isinstance(config, dict) else {}
    system = source.get("_system", {}) if isinstance(source.get("_system"), dict) else {}
    global_ai = source.get("_global_ai", {}) if isinstance(source.get("_global_ai"), dict) else {}

    connection_id = str(global_ai.get("connection_id") or "").strip()
    connection = next((item for item in source.get("_provider_connections", []) if isinstance(item, dict) and item.get("connection_id") == connection_id), {}) if connection_id else {}
    if not connection and is_opencode_connection_type(global_ai.get("connection_type") or system.get("global_provider")):
        connection = _first_opencode_connection(source)
        connection_id = str(connection.get("connection_id") or connection_id).strip()

    provider_id = str(global_ai.get("provider") or system.get("global_provider") or connection.get("provider") or "nvidia").strip().lower()
    backend_type = "opencode" if is_opencode_connection_type(global_ai.get("connection_type")) or is_opencode_connection_type(connection.get("connection_type")) or provider_id in OPENCODE_CONNECTION_TYPES else "api_provider"
    if backend_type == "opencode":
        provider_id = "opencode_bridge"
        if not connection_id:
            connection_id = str(connection.get("connection_id") or "opencode_bridge").strip()

    model = str(global_ai.get("model") or system.get("global_model") or connection.get("configured_model") or "").strip()
    normalized = normalize_model_id(model, connection_id, known_connection_ids(source) | {"opencode_bridge", "opencode_oauth_bridge"})
    model_id = normalized["model_id"]
    native_provider = normalized.get("provider_id") or (model_id.split("/", 1)[0] if "/" in model_id else "")

    live = bool(global_ai.get("enabled", True))
    if system.get("live_execution_enabled") is not None:
        live = bool(system.get("live_execution_enabled"))

    return EffectiveExecutionConfig(
        connection_id=connection_id or provider_connection_id(native_provider or provider_id),
        provider_id=native_provider if backend_type == "opencode" and native_provider else provider_id,
        model_id=model_id,
        execution_mode=str(system.get("execution_mode") or global_ai.get("execution_mode") or "production"),
        live_execution_enabled=live,
        project_root=str(Path(project_root).resolve()) if project_root else "",
        backend_type=backend_type,
    )


def migrate_legacy_execution_config(config: dict[str, Any]) -> bool:
    if not isinstance(config, dict):
        return False
    changed = False
    known = known_connection_ids(config) | {"opencode_bridge", "opencode_oauth_bridge"}
    for section_name in ("_global_ai", "_system"):
        section = config.get(section_name, {}) if isinstance(config.get(section_name), dict) else {}
        key = "model" if section_name == "_global_ai" else "global_model"
        normalized = normalize_model_id(str(section.get(key) or ""), str(section.get("connection_id") or ""), known)
        if normalized.get("migrated"):
            section[key] = normalized["model_id"]
            if section_name == "_global_ai":
                section["connection_id"] = normalized.get("connection_id") or section.get("connection_id")
                section["provider"] = "opencode_bridge"
                section["connection_type"] = "opencode_oauth_bridge"
            config[section_name] = section
            changed = True
    return changed
