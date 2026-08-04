import json
import asyncio
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend_security.dependencies as security_dependencies
from backend_security import (
    LocalSecurityContext,
    LocalSecurityMiddleware,
    StrictRequestModel,
    authorize_websocket,
    require_local_only_request,
    set_app_security_context,
)
from backend_security.network_policy import exact_origin_allowed, host_matches_context, is_loopback_host


pytestmark = pytest.mark.unit
TOKEN = "focused-security-token-32-bytes-ok"
ORIGIN = "http://127.0.0.1:8080"


class MutationPayload(StrictRequestModel):
    value: str


def _security_app(*, bind_host="127.0.0.1", port=8080, allow_test_client=True):
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    context = LocalSecurityContext.create(
        token=TOKEN,
        bind_host=bind_host,
        port=port,
        launch_id="launch-for-focused-test",
        allow_test_client=allow_test_client,
    )
    set_app_security_context(app, context)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/owner")
    def owner(request: Request):
        challenge = request.headers.get("X-FreelancerStudio-Challenge", "")
        if len(challenge) < 32:
            return {"error": "invalid_ownership_challenge"}
        return context.owner_challenge_response(challenge)

    @app.get("/api/read")
    def read():
        return {"status": "ok"}

    @app.post("/api/mutate")
    def mutate(payload: MutationPayload):
        return {"value": payload.value}

    @app.post("/api/projects/example/files/upload")
    async def upload(request: Request):
        return {"size": len(await request.body())}

    @app.get("/api/local-only")
    def local_only(_context=Depends(require_local_only_request)):
        return {"status": "available"}

    @app.websocket("/ws/private")
    async def private_socket(websocket: WebSocket):
        if await authorize_websocket(websocket) is None:
            return
        await websocket.accept()
        await websocket.send_json({"status": "accepted"})
        await websocket.close()

    return app, context


def _client(app, *, token=TOKEN, origin=ORIGIN, base_url=ORIGIN, transport=None):
    headers = {"X-FreelancerStudio-Token": token, "Origin": origin}
    if transport:
        headers["X-FreelancerStudio-Transport"] = transport
    return TestClient(app, base_url=base_url, headers=headers)


def test_token_lifecycle_is_fresh_injected_and_not_publicly_serialized():
    first = LocalSecurityContext.create()
    second = LocalSecurityContext.create()
    injected = LocalSecurityContext.create(token=TOKEN)

    assert first.token_for_trusted_transport() != second.token_for_trusted_transport()
    assert len(first.token_for_trusted_transport()) >= 43
    assert injected.token_for_trusted_transport() == TOKEN
    assert TOKEN not in repr(injected)
    assert TOKEN not in json.dumps(injected.public_owner_state())


def test_public_health_stays_public_and_owner_requires_a_challenge():
    app, _ = _security_app()
    client = TestClient(app, base_url=ORIGIN)

    assert client.get("/health").status_code == 200
    response = client.get("/health/owner")
    assert response.status_code == 200
    assert response.json() == {"error": "invalid_ownership_challenge"}
    assert TOKEN not in response.text


@pytest.mark.parametrize("supplied", [None, "incorrect-token-with-enough-length", f"x{TOKEN}", f"{TOKEN}x"])
def test_missing_incorrect_and_lookalike_tokens_are_rejected(supplied):
    app, _ = _security_app()
    headers = {"Origin": ORIGIN}
    if supplied is not None:
        headers["X-FreelancerStudio-Token"] = supplied
    response = TestClient(app, base_url=ORIGIN).get("/api/read", headers=headers)

    assert response.status_code == 401
    assert TOKEN not in response.text


def test_correct_token_uses_common_constant_time_comparison(monkeypatch):
    app, _ = _security_app()
    calls = []
    real_compare = security_dependencies.hmac.compare_digest

    def tracked(left, right):
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(security_dependencies.hmac, "compare_digest", tracked)
    response = _client(app).get("/api/read")

    assert response.status_code == 200
    assert len(calls) == 1


def test_duplicate_token_headers_fail_safely():
    app, _ = _security_app()
    response = TestClient(app, base_url=ORIGIN).get(
        "/api/read",
        headers=[
            ("X-FreelancerStudio-Token", TOKEN),
            ("X-FreelancerStudio-Token", TOKEN),
            ("Origin", ORIGIN),
        ],
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (ORIGIN, 200),
        ("https://evil.example", 403),
        ("null", 403),
        (None, 403),
        ("http://127.0.0.1.evil.example:8080", 403),
        ("http://127.0.0.1:8081", 403),
    ],
)
def test_mutation_origin_policy_is_exact(origin, expected):
    app, _ = _security_app()
    headers = {"X-FreelancerStudio-Token": TOKEN, "Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    response = TestClient(app, base_url=ORIGIN).post("/api/mutate", headers=headers, content='{"value":"ok"}')

    assert response.status_code == expected


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    [
        ("127.0.0.1:8080", 8080, True),
        ("localhost:8080", 8080, False),
        ("[::1]:8080", 8080, False),
        ("127.0.0.2:8080", 8080, False),
        ("127.0.0.1:8081", 8080, False),
        ("studio.example:8080", 8080, False),
        ("127.0.0.1", 8080, False),
        ("[::1", 8080, False),
    ],
)
def test_host_policy_handles_loopback_and_rejects_malformed_values(host, port, expected):
    assert host_matches_context(host, "127.0.0.1", port) is expected


def test_ipv6_host_is_accepted_only_for_ipv6_bind():
    assert host_matches_context("[::1]:8080", "::1", 8080) is True
    assert host_matches_context("127.0.0.1:8080", "::1", 8080) is False


def test_wrong_request_host_is_rejected():
    app, _ = _security_app()
    response = _client(app, base_url="http://127.0.0.1:8081").get("/api/read")
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "invalid_host"}}


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/json", 200),
        ("application/json; charset=utf-8", 200),
        (None, 415),
        ("text/plain", 415),
        ("application/x-www-form-urlencoded", 415),
        ("application/json-patch+json", 415),
    ],
)
def test_strict_mutation_content_type(content_type, expected):
    app, _ = _security_app()
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    if content_type:
        headers["Content-Type"] = content_type
    response = TestClient(app, base_url=ORIGIN).post("/api/mutate", headers=headers, content='{"value":"ok"}')
    assert response.status_code == expected


def test_strict_schema_rejects_malformed_extra_and_empty_bodies():
    app, _ = _security_app()
    client = _client(app)

    assert client.post("/api/mutate", headers={"Content-Type": "application/json"}, content="{").status_code == 422
    assert client.post("/api/mutate", json={"value": "ok", "extra": True}).status_code == 422
    assert client.post("/api/mutate", headers={"Content-Type": "application/json"}, content="").status_code == 422


def test_content_length_is_required_validated_and_bounded():
    app, _ = _security_app()
    client = _client(app)

    invalid = client.post(
        "/api/mutate",
        headers={"Content-Type": "application/json", "Content-Length": "invalid"},
        content='{"value":"ok"}',
    )
    oversized = client.post(
        "/api/mutate",
        headers={"Content-Type": "application/json", "Content-Length": str(1024 * 1024 + 1)},
        content='{"value":"ok"}',
    )

    assert invalid.status_code == 411
    assert oversized.status_code == 413


def test_multipart_limit_is_separate_from_json_limit():
    app, _ = _security_app()
    body = b"x" * (1024 * 1024 + 1)
    response = _client(app).post(
        "/api/projects/example/files/upload",
        headers={"Content-Type": "multipart/form-data; boundary=focused-test"},
        content=body,
    )
    assert response.status_code == 200
    assert response.json()["size"] == len(body)


def test_actual_streamed_body_limit_does_not_trust_declared_length():
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    replay, within_limit = asyncio.run(security_dependencies._buffer_limited_body(receive, 5))
    assert replay is None
    assert within_limit is False


def test_loopback_policy_is_explicit_and_network_bind_disables_local_capabilities():
    loopback = LocalSecurityContext.create(token=TOKEN, bind_host="::1")
    network = LocalSecurityContext.create(token=TOKEN, bind_host="192.168.1.10")

    assert loopback.local_capabilities_available is True
    assert network.local_capabilities_available is False
    assert network.local_capability_reason == "network_bind_disallowed"
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("127.0.0.2") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("loopback.example") is False


def test_non_loopback_client_is_rejected():
    app, _ = _security_app(allow_test_client=False)
    response = _client(app).get("/api/read")
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "loopback_client_required"}}


def test_network_bind_disables_concrete_local_only_route():
    app, _ = _security_app(bind_host="192.168.1.10")
    response = _client(app, base_url="http://192.168.1.10:8080").get("/api/local-only")
    assert response.status_code == 403
    assert response.json() == {"detail": "network_bind_disallowed"}


def test_authenticated_owner_handshake_matches_launch_without_disclosing_token():
    app, context = _security_app()
    challenge = "ownership-challenge-with-32-bytes-minimum"
    response = TestClient(app, base_url=ORIGIN).get(
        "/health/owner",
        headers={"X-FreelancerStudio-Challenge": challenge},
    )

    assert response.status_code == 200
    assert response.json()["launch_id"] == "launch-for-focused-test"
    assert response.json()["instance_id"] == context.instance_id
    assert response.json()["proof"] == context.owner_challenge_response(challenge)["proof"]
    assert TOKEN not in response.text


def test_websocket_requires_header_authentication_and_exact_origin():
    app, _ = _security_app()
    client = TestClient(app, base_url=ORIGIN)

    with pytest.raises(WebSocketDisconnect) as missing:
        with client.websocket_connect("/ws/private?token=ignored", headers={"Origin": ORIGIN}):
            pass
    assert missing.value.code == 4401

    with pytest.raises(WebSocketDisconnect) as invalid:
        with client.websocket_connect(
            "/ws/private",
            headers={"Host": "127.0.0.1:8080", "Origin": "https://evil.example", "X-FreelancerStudio-Token": TOKEN},
        ):
            pass
    assert invalid.value.code == 4401

    with client.websocket_connect(
        "/ws/private",
        headers={"Host": "127.0.0.1:8080", "Origin": ORIGIN, "X-FreelancerStudio-Token": TOKEN},
    ) as socket:
        assert socket.receive_json() == {"status": "accepted"}


def test_token_is_absent_from_port_config_and_frontend_sources():
    frontend = Path("frontend")
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [frontend / "preload.js", frontend / "src" / "App.jsx"]
    )
    assert TOKEN not in source
    assert "localStorage" not in source or TOKEN not in source
    assert "sessionStorage" not in source or TOKEN not in source

    main_source = Path("main.py").read_text(encoding="utf-8")
    port_write = main_source.split('port_file = os.path.join(RUNTIME_DIR, "studio_port.txt")', 1)[1].split("uvicorn.run", 1)[0]
    assert "startup_token" not in port_write
    assert "token_for_trusted_transport" not in port_write


def test_exact_origin_helper_rejects_lookalikes():
    allowed = frozenset({ORIGIN})
    assert exact_origin_allowed(ORIGIN, allowed)
    assert not exact_origin_allowed(f"{ORIGIN}.evil.example", allowed)
