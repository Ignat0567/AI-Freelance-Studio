"""Pydantic request/response payload models used by main.py's API routes.

Extracted from main.py as a pure, zero-logic decomposition step: these
classes only declare field shapes and defaults, so moving them here does
not change any behavior or shared state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict

from backend_security import StrictRequestModel as BaseModel


class KeysUpdatePayload(BaseModel):
    keys: Dict[str, str] = {}


class AISettingsPayload(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""


class GlobalAIConfigPayload(BaseModel):
    connection_id: str = ""
    connection_type: str = ""
    provider: str = ""
    model: str = ""
    temperature: float = 0.2
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    enabled: bool = True


class AgentAIConfigPayload(BaseModel):
    reset_to_defaults: bool | None = None
    use_global_connection: bool | None = None
    use_global_model: bool | None = None
    use_global_generation_parameters: bool | None = None
    connection_id: str | None = None
    connection_type: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: Any | None = None
    top_p: Any | None = None
    top_k: Any | None = None
    max_tokens: Any | None = None
    use_global: bool | None = None
    enabled: bool | None = None
    custom_prompt: str | None = None
    primary_connection: str | None = None
    preferred_model: str | None = None
    fallbacks: list[str] | None = None
    fallback_mode: str | None = None
    allow_paid_api: bool | None = None
    required_capabilities: list[str] | None = None
    locality_policy: str | None = None


class UniversalProviderConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    connection_id: str = ""
    provider_id: str = ""
    connection_type: str = ""
    auth_method: str = ""
    display_name: str = ""
    model_id: str = ""
    credential_reference: str = ""
    endpoint: str = ""
    executable_path: str = ""
    enabled: bool = True
    priority: int = 100
    metadata: dict[str, Any] = {}


class UniversalProviderConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_id: str | None = None
    connection_type: str | None = None
    auth_method: str | None = None
    display_name: str | None = None
    model_id: str | None = None
    endpoint: str | None = None
    executable_path: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    metadata: dict[str, Any] | None = None


class ProviderCredentialPayload(BaseModel):
    api_key: str


class ProviderDeletePayload(BaseModel):
    delete_credential: bool = False
    confirmed: bool = False
    save_path: str | None = None
    reset_to_defaults: bool | None = None


class ProviderTestPayload(BaseModel):
    provider: str = ""
    api_key: str = ""


class ProductJudgeConfigPayload(BaseModel):
    enabled: bool = True
    connection_id: str = ""
    model: str = ""
    use_global: bool = False
    temperature: float = 0.3
    top_p: float | None = 0.9
    top_k: int | None = None


class OpenCodeConnectionPayload(BaseModel):
    connection_id: str = ""
    name: str = "My OpenCode"
    configured_model: str = ""
    transport_type: str = "cli"
    local_endpoint: str = ""
    executable_path: str = ""
    enabled: bool = True
    capabilities: Dict[str, Any] = {}
    last_checked_at: str = ""


class ProposalGeneratePayload(BaseModel):
    job_description: str


class ProposalRefinePayload(BaseModel):
    original_job: str
    client_answers: str


class ChatPayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    message: str
    chat_history: List[Dict[str, str]] = []


class AgentChatPayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    message: str
    chat_history: List[Dict[str, str]] = []
    provider: str = ""
    model_name: str = ""
    project_id: str = ""
    fast_mode: bool = True
    use_project_context: bool = False
    history_limit: int = 6


class ManualProjectPayload(BaseModel):
    platform: str = "manual"
    jobTitle: str = ""
    title: str = ""
    description: str = ""
    initial_description: str = ""
    budget: str = "?"
    project_mode: str = "strict_mvp"
    quality_profile: str = ""
    required_targets: List[str] = []
    optional_targets: List[str] = []
    strict_completion_toggles: Dict[str, Any] = {}


class ProjectApprovePayload(BaseModel):
    approved: bool = True
    autonomous_mode: bool = True


class ProjectClaimPayload(BaseModel):
    platform: str = "unknown"
    job_id: str = ""
    title: str = ""
    description: str = ""
    budget: str = "?"
    url: str = ""
    project_mode: str = "strict_mvp"
    quality_profile: str = ""
    required_targets: List[str] = []
    optional_targets: List[str] = []


class TaskModel(BaseModel):
    title: str
    description: str = ""
    status: str = "todo"  # todo, in_progress, review, done
    assignee: str = ""  # agent_id
    priority: str = "medium"  # low, medium, high, critical
    due_date: str = ""


class TaskUpdateModel(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None


class TaskCommentPayload(BaseModel):
    text: str = ""
    author: str = ""


class GitHubConfigPayload(BaseModel):
    token: str = ""
    username: str = ""
    repo: str = ""


class GitHubPushPayload(BaseModel):
    project_id: str


class GitHubImportPayload(BaseModel):
    raw_url: str


class GoldieChatPayload(BaseModel):
    message: str
    chat_history: List[Dict[str, str]] = []


class GoldieAnalyzePayload(BaseModel):
    project_id: str = ""
    project_title: str = ""
    project_description: str = ""
    project_budget: str = "?"


class GoldieSearchPayload(BaseModel):
    query: str
