from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import StringConstraints

from backend_security import StrictRequestModel
from secret_store import get_secret
from video_generation import ReplicateVideoProvider, VideoGenerationFailed

router = APIRouter(prefix="/api/video", tags=["video"])

_PromptText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class VideoGenerateRequest(StrictRequestModel):
    prompt: _PromptText


def get_video_provider(request: Request) -> ReplicateVideoProvider | None:
    provider = getattr(request.app.state, "video_provider", None)
    if provider is not None:
        return provider
    token = get_secret("replicate_api_token")
    if not token:
        return None
    return ReplicateVideoProvider(api_token=token)


def install_video_api(app, *, provider: ReplicateVideoProvider | None = None) -> None:
    if provider is not None:
        app.state.video_provider = provider


@router.post("/generate")
def generate(payload: VideoGenerateRequest, request: Request) -> dict:
    provider = get_video_provider(request)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "replicate_token_missing", "message": "Set REPLICATE_API_TOKEN to enable video generation. Get a token at replicate.com."},
        )
    try:
        result = provider.generate_video(payload.prompt)
    except VideoGenerationFailed as exc:
        raise HTTPException(status_code=502, detail={"code": "video_generation_failed", "message": str(exc)}) from None

    return {"video_url": result.video_url, "prediction_id": result.prediction_id, "model_slug": result.model_slug}
