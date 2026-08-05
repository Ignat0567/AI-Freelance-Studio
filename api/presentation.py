from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import StringConstraints
from starlette.background import BackgroundTask

from ai_utils import ask_studio_ai_with_history
from backend_security import StrictRequestModel
from design_system import DEFAULT_TOKENS
from order_workflow.execution_config import ExecutionConfigurationProvider
from presentation_generator import build_presentation, select_presentation_slides

router = APIRouter(prefix="/api/presentation", tags=["presentation"])

_PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_TopicText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class PresentationGenerateRequest(StrictRequestModel):
    topic: _TopicText


def _default_ai_ask(prompt: str) -> str:
    snapshot = ExecutionConfigurationProvider().snapshot()
    return ask_studio_ai_with_history(snapshot.provider.provider, snapshot.model.model, prompt, [], temperature=0.3)


def get_presentation_ai_ask(request: Request) -> Callable[[str], str]:
    return getattr(request.app.state, "presentation_ai_ask", None) or _default_ai_ask


def install_presentation_api(app, *, ai_ask: Callable[[str], str] | None = None) -> None:
    if ai_ask is not None:
        app.state.presentation_ai_ask = ai_ask


@router.post("/generate")
def generate(payload: PresentationGenerateRequest, request: Request):
    ai_ask = get_presentation_ai_ask(request)
    try:
        selected = select_presentation_slides(payload.topic, ai_ask)
        destination = Path(tempfile.gettempdir()) / f"presentation_{uuid4().hex}.pptx"
        build_presentation(
            [(slide.slug, slide.content) for slide in selected],
            DEFAULT_TOKENS.light,
            DEFAULT_TOKENS.font_pairing,
            destination,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail={"code": "presentation_generation_failed", "message": str(exc)}) from None

    return FileResponse(
        destination,
        media_type=_PPTX_MEDIA_TYPE,
        filename="presentation.pptx",
        background=BackgroundTask(os.unlink, str(destination)),
    )
