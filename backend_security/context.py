from __future__ import annotations

import secrets
import threading
import uuid
import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Iterable

from .network_policy import is_loopback_host


TOKEN_BYTES = 32
TOKEN_HEADER = "x-freelancerstudio-token"
TRANSPORT_HEADER = "x-freelancerstudio-transport"
TRUSTED_MAIN_TRANSPORT = "electron-main"


@dataclass(frozen=True)
class LocalSecurityContext:
    _token: str = field(repr=False)
    bind_host: str
    port: int
    launch_id: str
    instance_id: str
    allowed_origins: frozenset[str]
    allow_test_client: bool = False

    @classmethod
    def create(
        cls,
        *,
        token: str | None = None,
        bind_host: str = "127.0.0.1",
        port: int = 8080,
        launch_id: str | None = None,
        allowed_origins: Iterable[str] = (),
        allow_test_client: bool = False,
    ) -> "LocalSecurityContext":
        process_token = token or secrets.token_urlsafe(TOKEN_BYTES)
        if len(process_token.encode("utf-8")) < 32:
            raise ValueError("Local authorization token is too short")
        active_origins = set(allowed_origins)
        if is_loopback_host(bind_host):
            active_origins.update(
                {
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                    f"http://[::1]:{port}",
                }
            )
        return cls(
            _token=process_token,
            bind_host=bind_host,
            port=port,
            launch_id=launch_id or str(uuid.uuid4()),
            instance_id=str(uuid.uuid4()),
            allowed_origins=frozenset(active_origins),
            allow_test_client=allow_test_client,
        )

    @property
    def local_capabilities_available(self) -> bool:
        return is_loopback_host(self.bind_host)

    @property
    def local_capability_reason(self) -> str:
        return "available" if self.local_capabilities_available else "network_bind_disallowed"

    def token_for_trusted_transport(self) -> str:
        return self._token

    def public_owner_state(self) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "FreelancerStudio",
            "port": self.port,
            "instance_id": self.instance_id,
            "launch_id": self.launch_id,
            "loopback_only": self.local_capabilities_available,
        }

    def owner_challenge_response(self, challenge: str) -> dict[str, object]:
        message = f"{challenge}:{self.launch_id}:{self.instance_id}:{self.port}".encode("utf-8")
        proof = base64.urlsafe_b64encode(
            hmac.new(self._token.encode("utf-8"), message, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        return {**self.public_owner_state(), "proof": proof}


_context_lock = threading.Lock()


def set_app_security_context(app, context: LocalSecurityContext) -> None:
    app.state.local_security_context = context


def get_app_security_context(app) -> LocalSecurityContext:
    context = getattr(app.state, "local_security_context", None)
    if context is not None:
        return context
    with _context_lock:
        context = getattr(app.state, "local_security_context", None)
        if context is None:
            context = LocalSecurityContext.create()
            set_app_security_context(app, context)
    return context
