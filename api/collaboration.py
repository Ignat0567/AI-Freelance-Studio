from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StringConstraints

from backend_security import StrictRequestModel
from collaboration.models import ChannelId
from collaboration.service import CollaborationError, CollaborationService, get_or_create_collaboration_service

router = APIRouter(prefix="/api/collaboration", tags=["collaboration"])


_STATUS_BY_CODE = {
    "unknown_channel": 404,
}

_SenderName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
_MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
_AttachmentRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class PostMessageRequest(StrictRequestModel):
    sender: _SenderName
    message: _MessageText
    attachments: Annotated[tuple[_AttachmentRef, ...], Field(default=(), max_length=20)] = ()
    referenced_files: Annotated[tuple[_AttachmentRef, ...], Field(default=(), max_length=20)] = ()


def get_collaboration_service(request: Request) -> CollaborationService:
    return get_or_create_collaboration_service(request.app)


def install_collaboration_api(app, *, service: CollaborationService | None = None) -> CollaborationService:
    active = service or CollaborationService()
    app.state.collaboration_service = active
    return active


def _handle_error(exc: CollaborationError) -> None:
    raise HTTPException(status_code=_STATUS_BY_CODE.get(exc.code, 400), detail={"code": exc.code, "message": exc.message}) from None


@router.get("/channels")
def list_channels(request: Request) -> dict:
    service = get_collaboration_service(request)
    return {"channels": [channel.to_dict() for channel in service.channels()]}


@router.get("/channels/{channel_id}/messages")
def list_messages(channel_id: ChannelId, request: Request) -> dict:
    service = get_collaboration_service(request)
    try:
        messages = service.list_messages(channel_id)
    except CollaborationError as exc:
        _handle_error(exc)
    return {"messages": [message.to_dict() for message in messages]}


@router.post("/channels/{channel_id}/messages")
def post_message(channel_id: ChannelId, payload: PostMessageRequest, request: Request) -> dict:
    service = get_collaboration_service(request)
    try:
        message = service.post_message(
            channel_id=channel_id,
            sender=payload.sender,
            sender_kind="user",
            message=payload.message,
            attachments=payload.attachments,
            referenced_files=payload.referenced_files,
        )
    except CollaborationError as exc:
        _handle_error(exc)
    return {"message": message.to_dict()}


@router.get("/events")
def list_events(request: Request, channel_id: str | None = None) -> dict:
    service = get_collaboration_service(request)
    try:
        events = service.list_events(channel_id)
    except CollaborationError as exc:
        _handle_error(exc)
    return {"events": [event.to_dict() for event in events]}
