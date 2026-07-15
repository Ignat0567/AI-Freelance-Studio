from typing import Any, Callable


DEFAULT_SYSTEM_SETTINGS: dict[str, Any] = {
    "global_provider": "nvidia",
    "global_model": "meta/llama-3.3-70b-instruct",
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
}

ALLOWED_SYSTEM_KEYS = [
    "global_provider", "global_model",
    "theme", "accent_color", "animation_speed", "font_size",
    "language", "auto_save", "notifications_enabled",
    "default_budget", "polling_interval", "log_detail",
    "vscode_path", "pycharm_path",
]

_load_studio_keys: Callable[[], dict] | None = None


def configure_system_settings_loader(load_studio_keys: Callable[[], dict]):
    global _load_studio_keys
    _load_studio_keys = load_studio_keys


def _get_saved_system_settings(data: dict | None = None) -> dict:
    if data is not None:
        source = data
    elif _load_studio_keys is not None:
        source = _load_studio_keys()
    else:
        source = {}
    saved = source.get("_system", {}) if isinstance(source.get("_system"), dict) else {}
    return {**DEFAULT_SYSTEM_SETTINGS, **saved}


SYSTEM_SETTINGS: dict[str, Any] = _get_saved_system_settings()


def reload_system_settings(data: dict | None = None) -> dict[str, Any]:
    SYSTEM_SETTINGS.clear()
    SYSTEM_SETTINGS.update(_get_saved_system_settings(data))
    return SYSTEM_SETTINGS
