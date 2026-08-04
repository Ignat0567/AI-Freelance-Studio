from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .replicate_client import create_prediction as _real_create_prediction
from .replicate_client import get_prediction as _real_get_prediction

MODEL_SLUG = "wan-video/wan-2.7-t2v"

_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


@dataclass(frozen=True, slots=True)
class VideoGenerationResult:
    video_url: str
    prediction_id: str
    model_slug: str


class VideoGenerationFailed(Exception):
    def __init__(self, prediction_id: str, status: str, detail: str = "") -> None:
        self.prediction_id = prediction_id
        self.status = status
        self.detail = detail
        super().__init__(f"Video generation {prediction_id or '<unknown>'} ended with status {status!r}: {detail}")


class ReplicateVideoProvider:
    def __init__(
        self,
        api_token: str,
        create_prediction: Callable[[str, dict[str, Any], str], dict[str, Any]] = _real_create_prediction,
        get_prediction: Callable[[str, str], dict[str, Any]] = _real_get_prediction,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 5.0,
        max_poll_attempts: int = 120,
    ) -> None:
        self._api_token = api_token
        self._create_prediction = create_prediction
        self._get_prediction = get_prediction
        self._sleep = sleep
        self._poll_interval_seconds = poll_interval_seconds
        self._max_poll_attempts = max_poll_attempts

    def generate_video(self, prompt: str, **extra_input: Any) -> VideoGenerationResult:
        prediction = self._create_prediction(MODEL_SLUG, {"prompt": prompt, **extra_input}, self._api_token)
        prediction_id = str(prediction.get("id") or "")
        status_url = str((prediction.get("urls") or {}).get("get") or "")

        status = str(prediction.get("status") or "")
        attempts = 0
        while status not in _TERMINAL_STATUSES:
            attempts += 1
            if attempts > self._max_poll_attempts:
                raise VideoGenerationFailed(prediction_id, "timeout", "Exceeded max_poll_attempts waiting for a terminal status")
            self._sleep(self._poll_interval_seconds)
            prediction = self._get_prediction(status_url, self._api_token)
            status = str(prediction.get("status") or "")

        if status != "succeeded":
            error_detail = str(prediction.get("error") or "")
            raise VideoGenerationFailed(prediction_id, status, error_detail)

        output = prediction.get("output")
        video_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) and output else "")
        if not video_url:
            raise VideoGenerationFailed(prediction_id, status, "Prediction succeeded but no output URL was returned")

        return VideoGenerationResult(video_url=str(video_url), prediction_id=prediction_id, model_slug=MODEL_SLUG)
