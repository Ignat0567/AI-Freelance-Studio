import time
import uuid

import config_storage
from ai_utils import ask_studio_ai_with_history

PLATFORMS = {
    "google": {"name": "Google", "icon": "🔴", "auth_type": "oauth", "url": "https://console.cloud.google.com/apis/credentials"},
    "github": {"name": "GitHub", "icon": "🐙", "auth_type": "token", "url": "https://github.com/settings/tokens"},
    "upwork": {"name": "Upwork", "icon": "🇺", "auth_type": "api_key", "url": "https://www.upwork.com/settings/api"},
    "freelancer": {"name": "Freelancer", "icon": "🇫", "auth_type": "api_key", "url": "https://www.freelancer.com/settings/api"},
    "openai": {"name": "OpenAI", "icon": "🤖", "auth_type": "api_key", "url": "https://platform.openai.com/api-keys"},
    "nvidia": {"name": "NVIDIA AI", "icon": "🟢", "auth_type": "api_key", "url": "https://build.nvidia.com"},
    "ollama": {"name": "Ollama", "icon": "🦙", "auth_type": "local", "url": "https://ollama.com"},
    "xai": {"name": "Grok (xAI)", "icon": "✦", "auth_type": "api_key", "url": "https://console.x.ai"},
    "grok": {"name": "Grok Subscription", "icon": "✦", "auth_type": "local", "url": "https://grok.com"},
}

ACCOUNT_METADATA_FIELDS = (
    "id",
    "platform",
    "label",
    "status",
    "last_sync",
    "created_at",
)


def _load():
    return config_storage.load_studio_keys()


def _save(data):
    config_storage.save_studio_keys(data)


def get_accounts():
    data = _load()
    accounts = data.get("_accounts", [])
    platforms = PLATFORMS
    result = []
    for acc in accounts:
        plat = platforms.get(acc.get("platform", ""), {})
        account = {
            "id": acc.get("id", ""),
            "platform": acc.get("platform", ""),
            "platform_name": plat.get("name", acc["platform"]),
            "icon": plat.get("icon", "🔗"),
            "label": acc.get("label", ""),
            "status": acc.get("status", "disconnected"),
            "last_sync": acc.get("last_sync", 0),
            "created_at": acc.get("created_at", 0),
            "auth_type": plat.get("auth_type", "unknown"),
        }
        if "credentials" in acc:
            account["warning"] = "legacy_credentials_present"
        result.append(account)
    return result


def add_account(platform: str, label: str = ""):
    data = _load()
    accounts = data.get("_accounts", [])
    new_id = str(uuid.uuid4())
    account = {
        "id": new_id,
        "platform": platform,
        "label": label or f"{PLATFORMS.get(platform, {}).get('name', platform)} account",
        "status": "connected",
        "last_sync": int(time.time()),
        "created_at": int(time.time()),
    }
    accounts.append({field: account[field] for field in ACCOUNT_METADATA_FIELDS})
    data["_accounts"] = accounts
    _save(data)
    return new_id


def remove_account(account_id: str):
    data = _load()
    accounts = data.get("_accounts", [])
    data["_accounts"] = [a for a in accounts if a.get("id") != account_id]
    _save(data)
    return True


def sync_account(account_id: str):
    data = _load()
    accounts = data.get("_accounts", [])
    for acc in accounts:
        if acc.get("id") == account_id:
            acc["last_sync"] = int(time.time())
            acc["status"] = "connected"
            data["_accounts"] = accounts
            _save(data)
            return {"status": "connected", "last_sync": acc["last_sync"]}
    return {"status": "not_found"}
