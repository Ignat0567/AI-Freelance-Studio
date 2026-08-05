from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.video as video_api
from api.video import install_video_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from video_generation import VideoGenerationFailed, VideoGenerationResult

pytestmark = pytest.mark.unit
TOKEN = "video-api-focused-token-32-bytes-min"
ORIGIN = "http://127.0.0.1:8080"


class _FakeProvider:
    def __init__(self, *, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.prompts: list[str] = []

    def generate_video(self, prompt: str):
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return self._result


def _app(*, provider=None):
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(token=TOKEN, bind_host="127.0.0.1", port=8080, launch_id="video-api-test-launch", allow_test_client=True),
    )
    install_video_api(app, provider=provider)
    app.include_router(router)
    return app


def _client(**kwargs) -> TestClient:
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    return TestClient(_app(**kwargs), base_url=ORIGIN, headers=headers)


def test_generate_returns_real_result_shape_with_fake_provider():
    result = VideoGenerationResult(video_url="https://replicate.delivery/out.mp4", prediction_id="pred-1", model_slug="wan-video/wan-2.7-t2v")
    provider = _FakeProvider(result=result)

    response = _client(provider=provider).post("/api/video/generate", json={"prompt": "a cat riding a bike"})

    assert response.status_code == 200
    body = response.json()
    assert body["video_url"] == "https://replicate.delivery/out.mp4"
    assert body["prediction_id"] == "pred-1"
    assert body["model_slug"] == "wan-video/wan-2.7-t2v"
    assert provider.prompts == ["a cat riding a bike"]


def test_generate_returns_502_on_video_generation_failed():
    provider = _FakeProvider(error=VideoGenerationFailed("pred-2", "failed", "NSFW content detected"))

    response = _client(provider=provider).post("/api/video/generate", json={"prompt": "prompt"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "video_generation_failed"


def test_generate_returns_400_when_no_token_is_configured(monkeypatch):
    monkeypatch.setattr(video_api, "get_secret", lambda _name: "")

    response = _client().post("/api/video/generate", json={"prompt": "prompt"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "replicate_token_missing"


def test_generate_requires_the_security_token():
    unauthenticated = TestClient(_app(), base_url=ORIGIN)

    response = unauthenticated.post("/api/video/generate", json={"prompt": "prompt"})

    assert response.status_code in (401, 403)
