from __future__ import annotations

from typing import Any

from provider_contracts import AuthMethod, ConnectionType, ProviderConnection
from provider_credentials import ProviderCredentialStore, credential_reference


LEGACY_PROVIDER_TO_TYPE = {
    "openai": ConnectionType.OPENAI_API_KEY.value,
    "anthropic": ConnectionType.ANTHROPIC_API_KEY.value,
    "google": ConnectionType.GEMINI_API_KEY.value,
    "ollama": ConnectionType.OLLAMA_LOCAL.value,
}


def migrate_provider_connections(config: dict[str, Any], credential_store: ProviderCredentialStore | None = None) -> bool:
    if not isinstance(config, dict):
        return False
    changed = False
    existing = config.get("_universal_provider_connections", []) if isinstance(config.get("_universal_provider_connections"), list) else []
    existing_ids = {item.get("connection_id") for item in existing if isinstance(item, dict)}
    config.setdefault("_provider_connection_migration_version", 1)
    legacy_connections = config.get("_provider_connections", []) if isinstance(config.get("_provider_connections"), list) else []
    for item in legacy_connections:
        if not isinstance(item, dict):
            continue
        connection_id = str(item.get("connection_id") or "")
        if not connection_id or connection_id in existing_ids:
            continue
        ctype = str(item.get("connection_type") or "")
        if ctype in {"opencode_bridge", "opencode_oauth_bridge"}:
            connection = ProviderConnection(
                connection_id=connection_id,
                provider_id="opencode",
                connection_type=ConnectionType.OPENCODE_PROVIDER.value,
                auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value,
                display_name=str(item.get("name") or item.get("display_name") or "OpenCode"),
                model_id=str(item.get("configured_model") or ""),
                executable_path=str(item.get("executable_path") or ""),
                metadata={"source": "legacy_opencode_connection"},
            )
            existing.append(connection.to_dict())
            existing_ids.add(connection_id)
            changed = True
    system = config.get("_system", {}) if isinstance(config.get("_system"), dict) else {}
    provider = str(system.get("global_provider") or "").lower()
    model = str(system.get("global_model") or "")
    if provider in LEGACY_PROVIDER_TO_TYPE:
        connection_id = f"provider-{provider}"
        if connection_id not in existing_ids:
            ref = credential_reference(provider, connection_id)
            legacy_key = str(config.get(f"{provider}_key") or config.get(f"{provider}_api_key") or "")
            secure_write_failed = False
            if legacy_key and credential_store:
                try:
                    credential_store.save_api_key(ref, legacy_key)
                    config.pop(f"{provider}_key", None)
                    config.pop(f"{provider}_api_key", None)
                    changed = True
                except Exception:
                    config.setdefault("_provider_connection_migration_errors", []).append({"provider": provider, "reason": "secure_credential_write_failed"})
                    secure_write_failed = True
            if not secure_write_failed:
                existing.append(ProviderConnection(
                connection_id=connection_id,
                provider_id=provider,
                connection_type=LEGACY_PROVIDER_TO_TYPE[provider],
                auth_method=AuthMethod.LOCAL.value if provider == "ollama" else AuthMethod.API_KEY.value,
                display_name=f"{provider.upper()} {'local' if provider == 'ollama' else 'API'}",
                model_id=model,
                credential_reference="" if provider == "ollama" else ref,
                endpoint="http://127.0.0.1:11434" if provider == "ollama" else "",
                metadata={"source": "legacy_system_provider"},
                ).to_dict())
                changed = True
    if changed:
        config["_universal_provider_connections"] = existing
    return changed
