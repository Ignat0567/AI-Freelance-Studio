from __future__ import annotations

from pydantic import BaseModel, ConfigDict


MAX_JSON_BODY_BYTES = 1024 * 1024
MAX_MULTIPART_BODY_BYTES = 64 * 1024 * 1024
JSON_MEDIA_TYPE = "application/json"
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MULTIPART_MUTATION_SUFFIXES = frozenset({"/files/upload", "/files/upload/archive"})


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def is_json_content_type(value: str) -> bool:
    media_type, separator, parameters = str(value or "").partition(";")
    if media_type.strip().lower() != JSON_MEDIA_TYPE:
        return False
    if not separator:
        return True
    parts = [item.strip() for item in parameters.split(";") if item.strip()]
    return len(parts) == 1 and parts[0].lower().replace(" ", "") == "charset=utf-8"


def is_multipart_mutation_path(path: str) -> bool:
    return path.startswith("/api/projects/") and any(path.endswith(suffix) for suffix in MULTIPART_MUTATION_SUFFIXES)
