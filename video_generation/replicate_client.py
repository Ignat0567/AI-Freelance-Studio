from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

_API_BASE = "https://api.replicate.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 30


class ReplicateAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Replicate API error {status_code}: {detail}")


def _headers(api_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}


def _request_json(method: str, url: str, payload: dict[str, Any] | None, api_token: str, timeout: int) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=_headers(api_token))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ReplicateAPIError(exc.code, detail or exc.reason) from exc
    except urllib.error.URLError as exc:
        raise ReplicateAPIError(0, str(exc.reason)) from exc


def create_prediction(model_slug: str, input_payload: dict[str, Any], api_token: str, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    url = f"{_API_BASE}/models/{model_slug}/predictions"
    return _request_json("POST", url, {"input": input_payload}, api_token, timeout)


def get_prediction(status_url: str, api_token: str, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    return _request_json("GET", status_url, None, api_token, timeout)
