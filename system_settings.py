from typing import Any, Callable


DEFAULT_SYSTEM_SETTINGS: dict[str, Any] = {
    "global_provider": "grok",
    "global_model": "grok-4.6",
    "theme": "dark",
    "accent_color": "#0ea5e9",
    "animation_speed": "normal",
    "font_size": "medium",
    "language": "en",
    "auto_save": True,
    "notifications_enabled": True,
    "default_budget": "500",
    "polling_interval": 3000,
    "log_detail": "normal",
    "vscode_path": "code",
    "pycharm_path": "pycharm",
    "live_execution_enabled": False,
    "show_experimental": False,
    "coding_backend": "",
}

ALLOWED_SYSTEM_KEYS = [
    "global_provider", "global_model",
    "theme", "accent_color", "animation_speed", "font_size",
    "language", "auto_save", "notifications_enabled",
    "default_budget", "polling_interval", "log_detail",
    "vscode_path", "pycharm_path",
    "live_execution_enabled", "show_experimental",
    "coding_backend",
]

_load_studio_keys: Callable[[], dict] | None = None
_save_studio_keys: Callable[[dict], None] | None = None


def configure_system_settings_loader(load_studio_keys: Callable[[], dict]):
    global _load_studio_keys
    _load_studio_keys = load_studio_keys


def configure_system_settings_storage(load_studio_keys: Callable[[], dict], save_studio_keys: Callable[[dict], None]):
    global _load_studio_keys, _save_studio_keys
    _load_studio_keys = load_studio_keys
    _save_studio_keys = save_studio_keys


def _load_config() -> dict:
    return _load_studio_keys() if _load_studio_keys is not None else {}


def _save_config(data: dict):
    if _save_studio_keys is None:
        raise RuntimeError("System settings storage is not configured")
    _save_studio_keys(data)


def _get_saved_system_settings(data: dict | None = None) -> dict:
    if data is not None:
        source = data
    else:
        source = _load_config()
    saved = source.get("_system", {}) if isinstance(source.get("_system"), dict) else {}
    return {**DEFAULT_SYSTEM_SETTINGS, **saved}


SYSTEM_SETTINGS: dict[str, Any] = _get_saved_system_settings()


def reload_system_settings(data: dict | None = None) -> dict[str, Any]:
    SYSTEM_SETTINGS.clear()
    SYSTEM_SETTINGS.update(_get_saved_system_settings(data))
    return SYSTEM_SETTINGS


def get_current_system_config() -> dict:
    all_keys = _load_config()
    saved = all_keys.get("_system", {})
    merged = {**DEFAULT_SYSTEM_SETTINGS, **SYSTEM_SETTINGS, **saved}
    return {k: merged.get(k, DEFAULT_SYSTEM_SETTINGS.get(k)) for k in ALLOWED_SYSTEM_KEYS}


def update_system_config(payload: dict[str, Any]) -> dict[str, str]:
    data = _load_config()
    saved = data.get("_system", {})
    for key in ALLOWED_SYSTEM_KEYS:
        if key in payload:
            saved[key] = payload[key]
            SYSTEM_SETTINGS[key] = payload[key]
    data["_system"] = saved
    _save_config(data)
    return {"status": "saved"}
