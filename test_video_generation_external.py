from __future__ import annotations

import os

import pytest

from secret_store import get_secret
from video_generation import ReplicateVideoProvider

pytestmark = pytest.mark.external


@pytest.mark.skipif(os.environ.get("RUN_EXTERNAL_VIDEO_TESTS") != "1", reason="Set RUN_EXTERNAL_VIDEO_TESTS=1 and REPLICATE_API_TOKEN to run a real, billed Replicate video generation")
def test_replicate_video_provider_generates_a_real_short_video():
    api_token = get_secret("replicate_api_token")
    assert api_token, "REPLICATE_API_TOKEN must be set to run this test"

    provider = ReplicateVideoProvider(api_token=api_token)

    result = provider.generate_video("a single wooden sailboat gently rocking on calm water at sunset, cinematic")

    assert result.video_url.startswith("https://")
    assert result.prediction_id
