from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

import pytest

from order_workflow import (
    ExecutionMode,
    FakeProjectExecutionAdapter,
    OpenCodeExecutionResult,
    ProductionProjectExecutionAdapter,
    ProjectExecutionService,
    ReadinessResult,
)
from order_workflow.api_models import CreateOrderRequest
from order_workflow.service import OrderWorkflowService
from order_workflow.service import OrderWorkflowError
from order_workflow.execution_config import ExecutionConfigurationProvider


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
PDF_DESCRIPTION = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions by voice or text, receive grounded answers with page citations, and hear answers spoken aloud."
)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"ready-{self.index:04d}"


class ReadyOpenCodeClient:
    def __init__(self) -> None:
        self.invoked = False

    def check_readiness(self):
        return ReadinessResult.ready_result()

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation):
        self.invoked = True
        return OpenCodeExecutionResult(success=True, summary="not used in readiness tests")


def _bridge_config(model="nvidia/deepseek-ai/deepseek-v4-pro"):
    return {
        "_provider_connections": [
            {
                "connection_type": "opencode_oauth_bridge",
                "configured_provider": "nvidia",
                "configured_model": model,
                "readiness_status": "ready",
                "auth_status": "authenticated",
                "enabled": True,
            }
        ]
    }


def _config(tmp_path=None, provider="", model="", secret=False, opencode=True, opt_in=False, config_data=None):
    config = config_data if config_data is not None else {"_system": {"global_provider": provider, "global_model": model}} if provider or model else {}
    return ExecutionConfigurationProvider(
        config_loader=lambda: config,
        secret_lookup=lambda name, _config=None: "configured-secret" if secret else "",
        opencode_version_probe=lambda: (opencode, "1.17.11" if opencode else "", "opencode.cmd" if opencode else ""),
        active_backend_probe=lambda: "",
        workspace_root=tmp_path or __import__("pathlib").Path(__file__).resolve().parent,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"} if opt_in else {},
    )


def _service(*, production_adapter=None, configuration_provider=None):
    ids = SequenceIds()
    execution = ProjectExecutionService(
        id_factory=ids,
        clock=lambda: NOW,
        fake_adapter=FakeProjectExecutionAdapter(),
        production_adapter=production_adapter,
    )
    return OrderWorkflowService(id_factory=ids, clock=lambda: NOW, execution_service=execution, configuration_provider=configuration_provider or _config())


def _configured_service(configuration_provider, *, opencode_client=None):
    ids = SequenceIds()
    return OrderWorkflowService(id_factory=ids, clock=lambda: NOW, configuration_provider=configuration_provider, opencode_client=opencode_client)


def _create(service, description=PDF_DESCRIPTION):
    state = service.create_order(CreateOrderRequest(title="Readiness App", description=description, product_type="web_app"))
    return state["order"]["id"]


def _brief_ready(service, order_id):
    service.defaults(order_id)
    return service.generate_brief(order_id)


def _approve_all(service, order_id):
    state = _brief_ready(service, order_id)
    brief = state["brief"]
    state = service.approve_brief(order_id, revision=brief["revision"], fingerprint=None)
    preview = state["design_preview"]
    return service.approve_design_preview(order_id, preview_id=preview["preview_id"], brief_version=preview["brief_version"])


def test_readiness_before_brief_approval_does_not_start_execution():
    service = _service()
    order_id = _create(service)

    readiness = service.execution_readiness(order_id)

    assert readiness["ready"] is False
    assert readiness["can_run_simulation"] is False
    assert readiness["can_run_live"] is False
    assert readiness["blockers"][0]["code"] == "brief_not_approved"
    with pytest.raises(OrderWorkflowError):
        service.execution(order_id)


def test_readiness_before_design_preview_approval_blocks_execution():
    service = _service()
    order_id = _create(service)
    state = _brief_ready(service, order_id)
    service.approve_brief(order_id, revision=state["brief"]["revision"], fingerprint=None)

    readiness = service.execution_readiness(order_id)

    assert readiness["can_run_simulation"] is False
    assert "design_preview_not_approved" in {item["code"] for item in readiness["blockers"]}


def test_approved_order_reports_simulation_ready_and_missing_provider():
    service = _service(configuration_provider=_config())
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id)
    serialized = str(readiness).casefold()

    assert readiness["simulation_ready"] is True
    assert readiness["can_run_simulation"] is True
    assert readiness["production_dry_run_ready"] is False
    assert readiness["production_live_ready"] is False
    assert "provider_not_configured" in {item["code"] for item in readiness["blockers"]}
    assert any(item["code"] == "provider" and item["status"] == "missing" for item in readiness["checks"])
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_production_readiness_reports_model_workspace_and_qa_blockers(tmp_path):
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="", workspace_root=tmp_path / "missing", qa_commands=())
    service = _service(production_adapter=adapter)
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id)

    assert {item["code"] for item in readiness["blockers"]} >= {
        "model_not_selected",
        "workspace_root_unavailable",
        "qa_tools_unavailable",
    }
    assert readiness["can_prepare_dry_run"] is False


def test_production_dry_run_ready_but_live_unavailable(tmp_path):
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    service = _service(production_adapter=adapter, configuration_provider=_config(tmp_path, provider="ollama", model="codellama", opencode=True))
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)

    assert readiness["ready"] is True
    assert readiness["can_prepare_dry_run"] is True
    assert readiness["can_run_live"] is False
    assert any(item["code"] == "live_execution" and item["status"] == "unavailable" for item in readiness["checks"])


def test_readiness_reflects_configured_provider_model_opencode_workspace_and_opt_in(tmp_path):
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    service = _service(
        production_adapter=adapter,
        configuration_provider=_config(tmp_path, provider="openai", model="gpt-5.5", secret=True, opencode=True, opt_in=True),
    )
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id)
    checks = {item["code"]: item for item in readiness["checks"]}

    assert checks["opencode"]["status"] == "ready"
    assert "1.17.11" in checks["opencode"]["message"]
    assert checks["provider"]["status"] == "ready"
    assert checks["model"]["status"] == "ready"
    assert checks["workspace"]["status"] == "ready"
    assert checks["live_opt_in"]["status"] == "ready"
    assert "configured-secret" not in str(readiness)


def test_default_live_adapter_uses_same_opencode_bridge_config_as_readiness_route(tmp_path):
    client = ReadyOpenCodeClient()
    service = _configured_service(
        _config(tmp_path, config_data=_bridge_config(), opt_in=True),
        opencode_client=client,
    )
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)
    blockers = {item["code"] for item in readiness["blockers"]}
    checks = {item["code"]: item for item in readiness["checks"]}

    assert checks["provider"]["status"] == "ready"
    assert checks["model"]["status"] == "ready"
    assert checks["live_opt_in"]["status"] == "ready"
    assert "provider_not_configured" not in blockers
    assert "model_not_selected" not in blockers
    assert "live_execution_opt_in_required" not in blockers
    assert readiness["can_prepare_dry_run"] is True
    assert readiness["can_run_live"] is True
    assert client.invoked is False


def test_default_live_adapter_still_requires_exact_live_opt_in(tmp_path):
    service = _configured_service(
        _config(tmp_path, config_data=_bridge_config(), opt_in=False),
        opencode_client=ReadyOpenCodeClient(),
    )
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)

    assert "live_execution_opt_in_required" in {item["code"] for item in readiness["blockers"]}
    assert readiness["can_prepare_dry_run"] is True
    assert readiness["can_run_live"] is False


def test_default_adapters_still_block_when_provider_and_model_are_missing(tmp_path):
    service = _configured_service(_config(tmp_path), opencode_client=ReadyOpenCodeClient())
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)
    blockers = {item["code"] for item in readiness["blockers"]}

    assert "provider_not_configured" in blockers
    assert "model_not_selected" in blockers
    assert readiness["can_prepare_dry_run"] is False
