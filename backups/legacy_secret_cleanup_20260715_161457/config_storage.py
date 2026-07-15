import json
import os
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "studio_config.json")


def load_json_config(config_file: str) -> dict[str, Any]:
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json_config(config_file: str, data: dict[str, Any]) -> None:
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_studio_keys() -> dict[str, Any]:
    return load_json_config(CONFIG_FILE)


def save_studio_keys(data: dict[str, Any]) -> None:
    save_json_config(CONFIG_FILE, data)
