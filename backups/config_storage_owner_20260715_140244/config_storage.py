import json
import os
from typing import Any


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
