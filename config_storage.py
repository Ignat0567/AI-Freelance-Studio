import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FREELANCERSTUDIO_USER_DATA") or BASE_DIR
CONFIG_FILE = os.path.join(DATA_DIR, "studio_config.json")


def load_json_config(config_file: str) -> dict[str, Any]:
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json_config(config_file: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(config_file)), exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_studio_keys() -> dict[str, Any]:
    return load_json_config(CONFIG_FILE)


def save_studio_keys(data: dict[str, Any]) -> None:
    save_json_config(CONFIG_FILE, data)


def backup_studio_config() -> str | None:
    source = Path(CONFIG_FILE)
    if not source.is_file():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = source.with_name(f"{source.stem}.legacy-secrets-{timestamp}{source.suffix}.bak")
    shutil.copy2(source, backup)
    return str(backup)
