from __future__ import annotations

import pytest

from video_generation import MODEL_SLUG, ReplicateAPIError, ReplicateVideoProvider, VideoGenerationFailed, VideoGenerationResult
from video_generation.replicate_client import create_prediction, get_prediction

pytestmark = pytest.mark.unit


class _FakeReplicate:
    """Scripts create_prediction/get_prediction responses without any real HTTP call."""

    def __init__(self, poll_sequence: list[dict]) -> None:
        self._poll_sequence = list(poll_sequence)
        self.create_calls: list[tuple[str, dict, str]] = []
        self.poll_calls: list[tuple[str, str]] = []

    def create_prediction(self, model_slug: str, input_payload: dict, api_token: str) -> dict:
        self.create_calls.append((model_slug, input_payload, api_token))
        return self._poll_sequence[0]

    def get_prediction(self, status_url: str, api_token: str) -> dict:
        self.poll_calls.append((status_url, api_token))
        # index 0 was already consumed by create_prediction's initial status
        return self._poll_sequence[len(self.poll_calls)]


def _sleep_recorder():
    calls: list[float] = []
    return calls, calls.append


def test_generate_video_polls_until_succeeded_and_returns_result():
    fake = _FakeReplicate(
        [
            {"id": "pred-1", "status": "starting", "urls": {"get": "https://api.replicate.com/v1/predictions/pred-1"}},
            {"id": "pred-1", "status": "processing"},
            {"id": "pred-1", "status": "succeeded", "output": "https://replicate.delivery/pred-1/out.mp4"},
        ]
    )
    sleep_calls, sleep_fn = _sleep_recorder()
    provider = ReplicateVideoProvider(
        api_token="tok-123",
        create_prediction=fake.create_prediction,
        get_prediction=fake.get_prediction,
        sleep=sleep_fn,
        poll_interval_seconds=0,
    )

    result = provider.generate_video("a cat riding a bike")

    assert result == VideoGenerationResult(video_url="https://replicate.delivery/pred-1/out.mp4", prediction_id="pred-1", model_slug=MODEL_SLUG)
    assert fake.create_calls == [(MODEL_SLUG, {"prompt": "a cat riding a bike"}, "tok-123")]
    assert len(fake.poll_calls) == 2
    assert sleep_calls == [0, 0]


def test_generate_video_accepts_list_output():
    fake = _FakeReplicate(
        [
            {"id": "pred-2", "status": "succeeded", "urls": {"get": "https://x/pred-2"}, "output": ["https://replicate.delivery/pred-2/out.mp4"]},
        ]
    )
    provider = ReplicateVideoProvider(api_token="tok", create_prediction=fake.create_prediction, get_prediction=fake.get_prediction, sleep=lambda _: None)

    result = provider.generate_video("prompt")

    assert result.video_url == "https://replicate.delivery/pred-2/out.mp4"


def test_generate_video_raises_on_failed_status():
    fake = _FakeReplicate(
        [
            {"id": "pred-3", "status": "starting", "urls": {"get": "https://x/pred-3"}},
            {"id": "pred-3", "status": "failed", "error": "NSFW content detected"},
        ]
    )
    provider = ReplicateVideoProvider(api_token="tok", create_prediction=fake.create_prediction, get_prediction=fake.get_prediction, sleep=lambda _: None)

    with pytest.raises(VideoGenerationFailed) as excinfo:
        provider.generate_video("prompt")

    assert excinfo.value.prediction_id == "pred-3"
    assert excinfo.value.status == "failed"
    assert excinfo.value.detail == "NSFW content detected"


def test_generate_video_raises_on_canceled_status():
    fake = _FakeReplicate(
        [
            {"id": "pred-4", "status": "canceled", "urls": {"get": "https://x/pred-4"}},
        ]
    )
    provider = ReplicateVideoProvider(api_token="tok", create_prediction=fake.create_prediction, get_prediction=fake.get_prediction, sleep=lambda _: None)

    with pytest.raises(VideoGenerationFailed) as excinfo:
        provider.generate_video("prompt")

    assert excinfo.value.status == "canceled"


def test_generate_video_times_out_after_max_poll_attempts():
    poll_sequence = [{"id": "pred-5", "status": "starting", "urls": {"get": "https://x/pred-5"}}]
    poll_sequence += [{"id": "pred-5", "status": "processing"} for _ in range(5)]
    fake = _FakeReplicate(poll_sequence)
    provider = ReplicateVideoProvider(
        api_token="tok",
        create_prediction=fake.create_prediction,
        get_prediction=fake.get_prediction,
        sleep=lambda _: None,
        max_poll_attempts=3,
    )

    with pytest.raises(VideoGenerationFailed) as excinfo:
        provider.generate_video("prompt")

    assert excinfo.value.status == "timeout"
    assert len(fake.poll_calls) == 3


def test_generate_video_raises_when_succeeded_has_no_output():
    fake = _FakeReplicate([{"id": "pred-6", "status": "succeeded", "urls": {"get": "https://x/pred-6"}, "output": None}])
    provider = ReplicateVideoProvider(api_token="tok", create_prediction=fake.create_prediction, get_prediction=fake.get_prediction, sleep=lambda _: None)

    with pytest.raises(VideoGenerationFailed):
        provider.generate_video("prompt")


class _FakeHTTPResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def read(self) -> bytes:
        import json

        return json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_create_prediction_posts_to_model_shorthand_url_with_auth_header(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        return _FakeHTTPResponse({"id": "pred-x", "status": "starting"})

    monkeypatch.setattr("video_generation.replicate_client.urllib.request.urlopen", fake_urlopen)

    result = create_prediction("wan-video/wan-2.7-t2v", {"prompt": "hi"}, "tok-abc")

    assert result == {"id": "pred-x", "status": "starting"}
    assert captured["url"] == "https://api.replicate.com/v1/models/wan-video/wan-2.7-t2v/predictions"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer tok-abc"
    assert b'"prompt": "hi"' in captured["body"]


def test_get_prediction_gets_status_url(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return _FakeHTTPResponse({"id": "pred-x", "status": "succeeded", "output": "https://x/out.mp4"})

    monkeypatch.setattr("video_generation.replicate_client.urllib.request.urlopen", fake_urlopen)

    result = get_prediction("https://api.replicate.com/v1/predictions/pred-x", "tok-abc")

    assert result["status"] == "succeeded"
    assert captured["url"] == "https://api.replicate.com/v1/predictions/pred-x"
    assert captured["method"] == "GET"


def test_create_prediction_raises_replicate_api_error_on_http_error(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=__import__("io").BytesIO(b'{"detail": "Invalid token"}'))

    monkeypatch.setattr("video_generation.replicate_client.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ReplicateAPIError) as excinfo:
        create_prediction("wan-video/wan-2.7-t2v", {"prompt": "hi"}, "bad-token")

    assert excinfo.value.status_code == 401
    assert "Invalid token" in excinfo.value.detail
