from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .context import (
    TOKEN_HEADER,
    TRANSPORT_HEADER,
    TRUSTED_MAIN_TRANSPORT,
    LocalSecurityContext,
    get_app_security_context,
)
from .network_policy import exact_origin_allowed, host_matches_context, is_loopback_host
from .request_policy import (
    MAX_JSON_BODY_BYTES,
    MAX_MULTIPART_BODY_BYTES,
    MUTATION_METHODS,
    is_json_content_type,
    is_multipart_mutation_path,
)


def _header_values(scope: Scope, name: str) -> list[str]:
    target = name.encode("ascii")
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == target]


def _authorized(scope: Scope, context: LocalSecurityContext) -> bool:
    supplied = _header_values(scope, TOKEN_HEADER)
    return len(supplied) == 1 and hmac.compare_digest(supplied[0], context.token_for_trusted_transport())


def _trusted_main_request(scope: Scope) -> bool:
    values = _header_values(scope, TRANSPORT_HEADER)
    return len(values) == 1 and values[0] == TRUSTED_MAIN_TRANSPORT


def _request_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code}})


async def _buffer_limited_body(receive: Receive, limit: int) -> tuple[Receive | None, bool]:
    messages = []
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            messages.append(message)
            break
        body = message.get("body", b"")
        total += len(body)
        if total > limit:
            return None, False
        messages.append(message)
        if not message.get("more_body", False):
            break

    async def replay() -> dict:
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return replay, True


def _common_request_error(scope: Scope, context: LocalSecurityContext) -> tuple[int, str] | None:
    if not _authorized(scope, context):
        return 401, "local_authorization_required"
    client = scope.get("client")
    if not client or not (is_loopback_host(client[0]) or (context.allow_test_client and client[0] == "testclient")):
        return 403, "loopback_client_required"
    host_values = _header_values(scope, "host")
    if len(host_values) != 1 or not host_matches_context(host_values[0], context.bind_host, context.port):
        return 403, "invalid_host"
    return None


class LocalSecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        protected = path.startswith("/api/")
        if not protected:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        context = get_app_security_context(scope["app"])
        common_error = _common_request_error(scope, context)
        if common_error:
            await _request_error(*common_error)(scope, receive, send)
            return
        if method in MUTATION_METHODS:
            if not _trusted_main_request(scope):
                origins = _header_values(scope, "origin")
                if len(origins) != 1 or not exact_origin_allowed(origins[0], context.allowed_origins):
                    await _request_error(403, "invalid_origin")(scope, receive, send)
                    return
            content_types = _header_values(scope, "content-type")
            multipart = is_multipart_mutation_path(path)
            if multipart:
                if len(content_types) != 1 or not content_types[0].lower().startswith("multipart/form-data; boundary="):
                    await _request_error(415, "multipart_content_type_required")(scope, receive, send)
                    return
            elif len(content_types) != 1 or not is_json_content_type(content_types[0]):
                await _request_error(415, "json_content_type_required")(scope, receive, send)
                return
            lengths = _header_values(scope, "content-length")
            limit = MAX_MULTIPART_BODY_BYTES if multipart else MAX_JSON_BODY_BYTES
            if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
                await _request_error(411, "content_length_required")(scope, receive, send)
                return
            if lengths and int(lengths[0]) > limit:
                await _request_error(413, "request_body_too_large")(scope, receive, send)
                return
            buffered_receive, within_limit = await _buffer_limited_body(receive, limit)
            if not within_limit:
                await _request_error(413, "request_body_too_large")(scope, receive, send)
                return
            receive = buffered_receive
        await self.app(scope, receive, send)


def require_authenticated_request(request: Request) -> LocalSecurityContext:
    context = get_app_security_context(request.app)
    error = _common_request_error(request.scope, context)
    if error:
        raise HTTPException(status_code=error[0], detail=error[1])
    return context


def require_local_only_request(request: Request) -> LocalSecurityContext:
    context = require_authenticated_request(request)
    if not context.local_capabilities_available:
        raise HTTPException(status_code=403, detail="network_bind_disallowed")
    return context


async def authorize_websocket(websocket: WebSocket) -> LocalSecurityContext | None:
    context = get_app_security_context(websocket.app)
    error = _common_request_error(websocket.scope, context)
    origins = _header_values(websocket.scope, "origin")
    if error or len(origins) != 1 or not exact_origin_allowed(origins[0], context.allowed_origins):
        await websocket.close(code=4401)
        return None
    return context
