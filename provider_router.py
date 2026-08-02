from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from provider_contracts import AgentProviderPolicy, ConnectionType, DataLocalityPolicy, FallbackMode, ProviderCapabilities, ProviderConnection, ProviderErrorCode
from provider_artifacts import ProviderArtifactRecorder
from provider_registry import provider_registry


SUBSCRIPTION_TYPES = {ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value, ConnectionType.CLAUDE_SUBSCRIPTION.value, ConnectionType.GEMINI_GOOGLE_ACCOUNT.value}
API_TYPES = {ConnectionType.OPENAI_API_KEY.value, ConnectionType.ANTHROPIC_API_KEY.value, ConnectionType.GEMINI_API_KEY.value, ConnectionType.OPENROUTER_API_KEY.value}
LOCAL_TYPES = {ConnectionType.OLLAMA_LOCAL.value, ConnectionType.LM_STUDIO_LOCAL.value, ConnectionType.LLAMA_CPP_SERVER.value, ConnectionType.LOCALAI_LOCAL.value, ConnectionType.VLLM_LOCAL.value, ConnectionType.OPENAI_COMPATIBLE_LOCAL.value}


@dataclass(frozen=True)
class RouteDecision:
    selected_connection_id: str | None
    status: str
    error_code: str = ""
    message: str = ""
    rejected_candidates: list[dict[str, Any]] | None = None
    approval_required: bool = False
    fallback_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_connection_id": self.selected_connection_id,
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "rejected_candidates": self.rejected_candidates or [],
            "approval_required": self.approval_required,
            "fallback_applied": self.fallback_applied,
        }


async def route_connection(policy: AgentProviderPolicy, connections: list[ProviderConnection], required: set[str] | None = None, artifact_run_id: str | None = None) -> RouteDecision:
    by_id = {item.connection_id: item for item in connections if item.enabled}
    candidates = []
    if policy.primary_connection_id:
        candidates.append(policy.primary_connection_id)
    candidates.extend(policy.fallback_connection_ids)
    required_caps = set(required or policy.required_capabilities)
    rejected: list[dict[str, Any]] = []
    for cid in candidates:
        connection = by_id.get(cid)
        if not connection:
            rejected.append({"connection_id": cid, "reason": "not_configured_or_disabled"})
            continue
        block = _policy_block(policy, connection)
        if block:
            rejected.append({"connection_id": cid, "reason": block.error_code, "message": block.message})
            if block.status == "requires_user_action":
                decision = RouteDecision(None, block.status, block.error_code, block.message, rejected, True, cid != policy.primary_connection_id)
                _write_route_artifact(artifact_run_id, policy, decision, None, required_caps)
                return decision
            continue
        adapter = provider_registry.create(connection)
        capabilities = await adapter.get_capabilities()
        missing = _missing_capabilities(capabilities, required_caps)
        if missing:
            rejected.append({"connection_id": cid, "reason": ProviderErrorCode.CAPABILITY_MISMATCH.value, "missing": sorted(missing)})
            continue
        test = await adapter.test_connection()
        if test.ready:
            decision = RouteDecision(cid, "ready", message="Selected ready provider connection", rejected_candidates=rejected, fallback_applied=cid != policy.primary_connection_id)
            _write_route_artifact(artifact_run_id, policy, decision, by_id.get(cid), required_caps)
            return decision
        rejected.append({"connection_id": cid, "reason": test.error_code or test.status, "message": test.message})
        if policy.fallback_mode == FallbackMode.STOP.value:
            decision = RouteDecision(None, "blocked", test.error_code, test.message, rejected)
            _write_route_artifact(artifact_run_id, policy, decision, None, required_caps)
            return decision
        if connection.connection_type in API_TYPES and not policy.allow_paid_api_fallback and cid != policy.primary_connection_id:
            decision = RouteDecision(None, "requires_user_action", ProviderErrorCode.UNSUPPORTED.value, "Paid API fallback requires explicit user approval", rejected, True, True)
            _write_route_artifact(artifact_run_id, policy, decision, None, required_caps)
            return decision
    if rejected:
        first = rejected[0]
        decision = RouteDecision(None, "blocked", str(first.get("reason") or ProviderErrorCode.UNKNOWN.value), str(first.get("message") or "No configured provider connection is ready"), rejected)
        _write_route_artifact(artifact_run_id, policy, decision, None, required_caps)
        return decision
    decision = RouteDecision(None, "blocked", ProviderErrorCode.UNKNOWN.value, "No configured provider connection is ready", rejected)
    _write_route_artifact(artifact_run_id, policy, decision, None, required_caps)
    return decision


def _write_route_artifact(run_id: str | None, policy: AgentProviderPolicy, decision: RouteDecision, selected: ProviderConnection | None, required_caps: set[str]) -> None:
    if not run_id:
        return
    ProviderArtifactRecorder(run_id).write_json("route_decision.json", {
        "schema_version": 1,
        "requested_connection_id": policy.primary_connection_id,
        "selected_connection_id": decision.selected_connection_id,
        "selected_model_id": selected.model_id if selected else "",
        "fallback_applied": decision.fallback_applied,
        "requires_user_confirmation": decision.approval_required,
        "policy": {
            "fallback_mode": policy.fallback_mode,
            "allow_paid_api_fallback": policy.allow_paid_api_fallback,
            "data_locality": policy.data_locality_policy,
            "required_capabilities": sorted(required_caps),
        },
        "rejected_candidates": [{"connection_id": item.get("connection_id"), "reason_code": str(item.get("reason") or "").upper(), "message": item.get("message", "")} for item in decision.rejected_candidates or []],
    })


def _policy_block(policy: AgentProviderPolicy, connection: ProviderConnection) -> RouteDecision | None:
    if policy.data_locality_policy == DataLocalityPolicy.LOCAL_ONLY.value and connection.connection_type not in LOCAL_TYPES:
        return RouteDecision(None, "blocked", ProviderErrorCode.PRIVACY_POLICY_BLOCK.value, "LOCAL_ONLY policy blocks cloud subscription/API connections")
    if policy.fallback_mode == FallbackMode.LOCAL_ONLY.value and connection.connection_type not in LOCAL_TYPES:
        return RouteDecision(None, "blocked", ProviderErrorCode.PRIVACY_POLICY_BLOCK.value, "LOCAL_ONLY fallback blocks this connection")
    if policy.fallback_mode == FallbackMode.SUBSCRIPTION_ONLY.value and connection.connection_type not in SUBSCRIPTION_TYPES:
        return RouteDecision(None, "blocked", ProviderErrorCode.CAPABILITY_MISMATCH.value, "Subscription-only fallback blocks this connection")
    if connection.connection_type in API_TYPES and not policy.allow_paid_api_fallback and policy.fallback_mode != FallbackMode.ALLOW_API.value and policy.primary_connection_id != connection.connection_id:
        return RouteDecision(None, "requires_user_action", ProviderErrorCode.UNSUPPORTED.value, "Paid API fallback is disabled by default")
    return None


def _missing_capabilities(capabilities: ProviderCapabilities, required: set[str]) -> set[str]:
    missing = set()
    data = capabilities.to_dict()
    for item in required:
        if not data.get(item):
            missing.add(item)
    return missing
