from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from workflow_artifacts import mask_secrets
from secret_store import mask_secret


class CredentialBackend(Protocol):
    mode: str
    def set(self, reference: str, secret: str) -> None: ...
    def get(self, reference: str) -> str: ...
    def delete(self, reference: str) -> None: ...
    def diagnostics(self) -> dict: ...


class CredentialBackendMode(StrEnum):
    AVAILABLE_SECURE = "available_secure"
    READ_ONLY = "read_only"
    UNAVAILABLE = "unavailable"
    TEST_MEMORY_ONLY = "test_memory_only"


class UnavailableCredentialBackend:
    mode = CredentialBackendMode.UNAVAILABLE.value

    def set(self, reference: str, secret: str) -> None:
        raise RuntimeError("secure_credential_backend_unavailable")

    def get(self, reference: str) -> str:
        return ""

    def delete(self, reference: str) -> None:
        return None

    def diagnostics(self) -> dict:
        return {"mode": self.mode, "can_save": False, "remediation": _platform_remediation()}


class KeyringCredentialBackend:
    service_name = "AI Freelancer Studio"
    mode = CredentialBackendMode.AVAILABLE_SECURE.value

    def __init__(self) -> None:
        import keyring  # type: ignore
        self.keyring = keyring

    def set(self, reference: str, secret: str) -> None:
        self.keyring.set_password(self.service_name, reference, secret)

    def get(self, reference: str) -> str:
        return self.keyring.get_password(self.service_name, reference) or ""

    def delete(self, reference: str) -> None:
        try:
            self.keyring.delete_password(self.service_name, reference)
        except Exception:
            pass

    def diagnostics(self) -> dict:
        return {"mode": self.mode, "can_save": True, "backend": str(getattr(self.keyring, "get_keyring", lambda: "keyring")())}


class EnvReadOnlyCredentialBackend:
    mode = CredentialBackendMode.READ_ONLY.value

    def set(self, reference: str, secret: str) -> None:
        raise RuntimeError("env_credential_backend_is_read_only")

    def get(self, reference: str) -> str:
        return os.environ.get(reference, "").strip()

    def delete(self, reference: str) -> None:
        return None

    def diagnostics(self) -> dict:
        return {"mode": self.mode, "can_save": False, "source": "process_environment", "remediation": "Save API keys in the OS credential manager; environment references are read-only."}


class TestMemoryCredentialBackend:
    mode = CredentialBackendMode.TEST_MEMORY_ONLY.value

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, reference: str, secret: str) -> None:
        self.data[reference] = secret

    def get(self, reference: str) -> str:
        return self.data.get(reference, "")

    def delete(self, reference: str) -> None:
        self.data.pop(reference, None)

    def diagnostics(self) -> dict:
        return {"mode": self.mode, "can_save": True, "test_only": True}


def default_backend() -> CredentialBackend:
    if os.environ.get("FREELANCERSTUDIO_TEST_MEMORY_CREDENTIALS") == "1":
        return TestMemoryCredentialBackend()
    try:
        return KeyringCredentialBackend()
    except Exception:
        if os.environ.get("FREELANCERSTUDIO_ALLOW_ENV_CREDENTIAL_READ") == "1":
            return EnvReadOnlyCredentialBackend()
        return UnavailableCredentialBackend()


@dataclass
class ProviderCredentialStore:
    backend: CredentialBackend

    def save_api_key(self, reference: str, api_key: str) -> dict:
        if not reference or not api_key:
            raise ValueError("credential_reference_and_secret_required")
        self.backend.set(reference, api_key)
        return {"credential_reference": reference, "saved": True, "masked": mask_secret(api_key)}

    def read_api_key(self, reference: str) -> str:
        return self.backend.get(reference) if reference else ""

    def delete_api_key(self, reference: str) -> None:
        if reference:
            self.backend.delete(reference)

    def diagnostics(self) -> dict:
        return mask_secrets(self.backend.diagnostics())


def _platform_remediation() -> str:
    if os.name == "nt":
        return "Enable Windows Credential Manager/keyring support, then retry saving the API key."
    if hasattr(os, "uname") and os.uname().sysname == "Darwin":
        return "Enable macOS Keychain/keyring access, then retry saving the API key."
    return "Install and unlock a Secret Service compatible keyring, then retry saving the API key."


def credential_reference(provider_id: str, connection_id: str) -> str:
    safe_provider = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in provider_id.lower())
    safe_connection = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in connection_id.lower())
    return f"FREELANCERSTUDIO_{safe_provider}_{safe_connection}_API_KEY".upper()
