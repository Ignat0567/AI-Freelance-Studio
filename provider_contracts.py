from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator

from workflow_contracts import ExecutionBrief


class ProviderId(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENCODE = "opencode"
    OLLAMA = "ollama"
    LOCAL = "local"
    CUSTOM = "custom"


class ConnectionType(StrEnum):
    CODEX_CHATGPT_SUBSCRIPTION = "codex_chatgpt_subscription"
    OPENAI_API_KEY = "openai_api_key"
    CLAUDE_SUBSCRIPTION = "claude_subscription"
    ANTHROPIC_API_KEY = "anthropic_api_key"
    GEMINI_GOOGLE_ACCOUNT = "gemini_google_account"
    GEMINI_API_KEY = "gemini_api_key"
    OPENCODE_PROVIDER = "opencode_provider"
    OLLAMA_LOCAL = "ollama_local"
    LM_STUDIO_LOCAL = "lm_studio_local"
    LLAMA_CPP_SERVER = "llama_cpp_server"
    LOCALAI_LOCAL = "localai_local"
    VLLM_LOCAL = "vllm_local"
    OPENAI_COMPATIBLE_LOCAL = "openai_compatible_local"
    CUSTOM_CLI = "custom_cli"


class AuthMethod(StrEnum):
    DELEGATED_CLI_LOGIN = "delegated_cli_login"
    API_KEY = "api_key"
    LOCAL = "local"
    NONE = "none"


class ConnectionStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    NOT_INSTALLED = "not_installed"
    SIGNED_OUT = "signed_out"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class ProviderErrorCode(StrEnum):
    CLI_NOT_INSTALLED = "cli_not_installed"
    CLI_VERSION_UNSUPPORTED = "cli_version_unsupported"
    AUTH_REQUIRED = "auth_required"
    AUTH_CANCELLED = "auth_cancelled"
    AUTH_EXPIRED = "auth_expired"
    PLAN_UNSUPPORTED = "plan_unsupported"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MODEL_UNAVAILABLE = "model_unavailable"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    PROCESS_CRASHED = "process_crashed"
    CAPABILITY_MISMATCH = "capability_mismatch"
    PRIVACY_POLICY_BLOCK = "privacy_policy_block"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    MALFORMED_RESPONSE = "malformed_response"


class DataLocalityPolicy(StrEnum):
    ANY = "any"
    LOCAL_ONLY = "local_only"
    SUBSCRIPTION_ALLOWED = "subscription_allowed"
    API_ALLOWED = "api_allowed"


class ToolCallingMode(StrEnum):
    NATIVE = "native"
    PROMPT_EMULATED = "prompt_emulated"
    DISABLED = "disabled"


class FallbackMode(StrEnum):
    STOP = "stop"
    ASK_USER = "ask_user"
    SUBSCRIPTION_ONLY = "subscription_only"
    LOCAL_ONLY = "local_only"
    ALLOW_API = "allow_api"


@dataclass(frozen=True)
class ProviderCapabilities:
    coding: bool = False
    chat: bool = True
    tools: bool = False
    code_generation: bool = False
    native_tool_calling: bool = False
    file_editing: bool = False
    command_execution: bool = False
    repository_access: bool = False
    streaming: bool = False
    vision: bool = False
    structured_output: bool = False
    model_listing: bool = False
    cancellation: bool = True
    local_execution: bool = False
    tool_calling: str = ToolCallingMode.DISABLED.value
    context_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    display_name: str
    capabilities: ProviderCapabilities
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = self.capabilities.to_dict()
        return data


@dataclass(frozen=True)
class LocalModelDescriptor:
    id: str
    display_name: str
    runtime: str
    family: str | None
    parameter_size: str | None
    quantization: str | None
    file_size_bytes: int | None
    context_length: int | None
    capabilities: ProviderCapabilities
    loaded: bool | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = self.capabilities.to_dict()
        return data


@dataclass(frozen=True)
class ProviderConnection:
    connection_id: str
    provider_id: str
    connection_type: str
    auth_method: str
    display_name: str
    model_id: str = ""
    credential_reference: str = ""
    endpoint: str = ""
    executable_path: str = ""
    enabled: bool = True
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionResult:
    status: str
    installed: bool
    version: str = ""
    executable_path: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectionHealth:
    status: str
    error_code: str = ""
    message: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuthenticationResult:
    status: str
    message: str = ""
    command_started: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectionTestResult:
    status: str
    ready: bool
    message: str = ""
    error_code: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentEvent:
    type: str
    message: str = ""
    execution_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None
    provider_reported_cost: float | None = None
    currency: str | None = None
    estimated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentEventType(StrEnum):
    STARTED = "started"
    STATUS = "status"
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FILE_CHANGED = "file_changed"
    COMMAND_STARTED = "command_started"
    COMMAND_OUTPUT = "command_output"
    COMMAND_FINISHED = "command_finished"
    USAGE = "usage"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class AgentProviderPolicy:
    primary_connection_id: str | None
    fallback_connection_ids: list[str]
    fallback_mode: str = FallbackMode.ASK_USER.value
    allow_paid_api_fallback: bool = False
    preferred_models: list[str] = field(default_factory=list)
    required_capabilities: set[str] = field(default_factory=set)
    data_locality_policy: str = DataLocalityPolicy.ANY.value


class ProviderAdapter(ABC):
    def __init__(self, connection: ProviderConnection):
        self.connection = connection

    @abstractmethod
    async def detect(self) -> DetectionResult: ...

    @abstractmethod
    async def get_status(self) -> ConnectionHealth: ...

    @abstractmethod
    async def authenticate(self) -> AuthenticationResult: ...

    @abstractmethod
    async def logout(self) -> None: ...

    @abstractmethod
    async def list_models(self) -> list[ModelDescriptor]: ...

    @abstractmethod
    async def get_capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult: ...

    @abstractmethod
    async def execute(self, brief: ExecutionBrief) -> AsyncIterator[AgentEvent]: ...

    @abstractmethod
    async def cancel(self, execution_id: str) -> None: ...
