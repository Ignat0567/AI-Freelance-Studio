import os
from abc import ABC, abstractmethod


PROVIDER_ENV_NAMES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "together": "TOGETHER_API_KEY",
}

NAMED_SECRET_ENV_NAMES = {
    "github_token": "GITHUB_TOKEN",
    "freelancer_client_secret": "FREELANCER_CLIENT_SECRET",
    "upwork_client_secret": "UPWORK_CLIENT_SECRET",
}

SECRET_KEY_SUFFIXES = ("_key", "_api_key", "_token", "_secret", "_password")


class SecretStore(ABC):
    @abstractmethod
    def get(self, name: str) -> str:
        """Return a secret value, or an empty string when it is not configured."""


class EnvSecretStore(SecretStore):
    def get(self, name: str) -> str:
        env_name = env_name_for_secret(name)
        return os.environ.get(env_name, "").strip() if env_name else ""


def env_name_for_secret(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if normalized in NAMED_SECRET_ENV_NAMES:
        return NAMED_SECRET_ENV_NAMES[normalized]
    if normalized.endswith("_key"):
        provider = normalized[:-4]
        if provider in PROVIDER_ENV_NAMES:
            return PROVIDER_ENV_NAMES[provider]
    if normalized.endswith("_api_key"):
        provider = normalized[:-8]
        if provider in PROVIDER_ENV_NAMES:
            return PROVIDER_ENV_NAMES[provider]
    return ""


def provider_secret_name(provider: str) -> str:
    return f"{str(provider or '').strip().lower()}_key"


def is_secret_key(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if normalized in NAMED_SECRET_ENV_NAMES:
        return True
    if env_name_for_secret(normalized):
        return True
    return normalized.endswith(SECRET_KEY_SUFFIXES)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]


def legacy_secret_warning(secret_name: str) -> dict:
    env_name = env_name_for_secret(secret_name) or "the matching environment variable"
    return {
        "secret": secret_name,
        "env": env_name,
        "message": f"Legacy secret '{secret_name}' was found in studio_config.json. Move it to {env_name}; the value was not returned.",
    }


def get_secret(name: str, config: dict | None = None, store: SecretStore | None = None) -> str:
    active_store = store or EnvSecretStore()
    value = active_store.get(name)
    if value:
        return value
    if not isinstance(config, dict):
        return ""
    normalized = str(name or "").strip().lower()
    if normalized.endswith("_key"):
        provider = normalized[:-4]
        return str(config.get(normalized) or config.get(f"{provider}_api_key") or "").strip()
    if normalized.endswith("_api_key"):
        provider = normalized[:-8]
        return str(config.get(normalized) or config.get(f"{provider}_key") or "").strip()
    return str(config.get(normalized) or "").strip()


def has_env_secret(name: str, store: SecretStore | None = None) -> bool:
    return bool((store or EnvSecretStore()).get(name))


def has_legacy_secret(name: str, config: dict | None) -> bool:
    if not isinstance(config, dict):
        return False
    normalized = str(name or "").strip().lower()
    if normalized.endswith("_key"):
        provider = normalized[:-4]
        return bool(config.get(normalized) or config.get(f"{provider}_api_key"))
    if normalized.endswith("_api_key"):
        provider = normalized[:-8]
        return bool(config.get(normalized) or config.get(f"{provider}_key"))
    return bool(config.get(normalized))


def secret_status(name: str, config: dict | None = None, store: SecretStore | None = None) -> dict:
    value = get_secret(name, config, store)
    env_configured = has_env_secret(name, store)
    legacy_configured = has_legacy_secret(name, config)
    status = {
        "saved": bool(value),
        "source": "env" if env_configured else "legacy_config" if legacy_configured else "missing",
        "masked": mask_secret(value),
    }
    if legacy_configured and not env_configured:
        status["legacy_warning"] = legacy_secret_warning(name)
    return status


def collect_legacy_secret_warnings(config: dict | None) -> list[dict]:
    if not isinstance(config, dict):
        return []
    warnings = []
    for key, value in config.items():
        if key.startswith("_") or not value or not is_secret_key(key):
            continue
        if not has_env_secret(key):
            warnings.append(legacy_secret_warning(key))
    github = config.get("_github", {}) if isinstance(config.get("_github"), dict) else {}
    if github.get("token") and not has_env_secret("github_token"):
        warnings.append(legacy_secret_warning("github_token"))
    return warnings
