from __future__ import annotations

import pytest

from order_workflow.execution_config import ExecutionConfigurationProvider
from order_workflow.phased_adapter import PhasedLiveOpenCodeExecutionAdapter
from order_workflow.production_adapter import LiveOpenCodeExecutionAdapter, ProductionProjectExecutionAdapter, UnavailableOpenCodeExecutionClient
from order_workflow.qa_runner import run_qa_commands
from order_workflow.docker_qa_runner import run_qa_commands_in_docker
from order_workflow.service import ConfigurationBackedExecutionAdapter

pytestmark = pytest.mark.unit


def _bridge_config():
    return {
        "_provider_connections": [
            {
                "connection_type": "opencode_oauth_bridge",
                "configured_provider": "nvidia",
                "configured_model": "nvidia/deepseek-ai/deepseek-v4-pro",
                "readiness_status": "ready",
                "auth_status": "authenticated",
                "enabled": True,
            }
        ],
        "opencode_provider_ready": True,
        "opencode_provider": "nvidia",
        "opencode_model": "nvidia/deepseek-ai/deepseek-v4-pro",
    }


def _configuration(tmp_path, *, live_environ=None):
    return ExecutionConfigurationProvider(
        config_loader=lambda: _bridge_config(),
        secret_lookup=lambda name, _config=None: "",
        opencode_version_probe=lambda: (True, "1.17.11", "opencode.cmd"),
        workspace_root=tmp_path,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1", **(live_environ or {})},
    )


def test_live_defaults_to_the_phased_adapter(tmp_path):
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=True, opencode_client=UnavailableOpenCodeExecutionClient())

    assert isinstance(adapter._adapter(), PhasedLiveOpenCodeExecutionAdapter)


def test_legacy_env_var_selects_the_legacy_adapter(tmp_path):
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=True, opencode_client=UnavailableOpenCodeExecutionClient(), environ={"FREELANCERSTUDIO_EXECUTION_PIPELINE": "legacy"})

    assert isinstance(adapter._adapter(), LiveOpenCodeExecutionAdapter)
    assert not isinstance(adapter._adapter(), PhasedLiveOpenCodeExecutionAdapter)


def test_unrecognized_pipeline_value_still_defaults_to_phased(tmp_path):
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=True, opencode_client=UnavailableOpenCodeExecutionClient(), environ={"FREELANCERSTUDIO_EXECUTION_PIPELINE": "typo"})

    assert isinstance(adapter._adapter(), PhasedLiveOpenCodeExecutionAdapter)


def test_dry_run_mode_is_unaffected_by_the_pipeline_flag(tmp_path):
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=False, environ={"FREELANCERSTUDIO_EXECUTION_PIPELINE": "legacy"})

    resolved = adapter._adapter()
    assert isinstance(resolved, ProductionProjectExecutionAdapter)
    assert not isinstance(resolved, LiveOpenCodeExecutionAdapter)


def test_real_qa_backend_env_var_is_respected_even_though_live_opt_in_synthesizes_its_own_environ(tmp_path):
    """Regression test: ConfigurationBackedExecutionAdapter._adapter() builds a synthetic
    environ dict just for the live opt-in check when constructing the adapter -- that dict
    must not shadow a real FREELANCERSTUDIO_PHASED_QA_BACKEND setting from the actual
    environment source (self._environ), or the operator's host-QA opt-out would be silently
    ignored whenever live execution is enabled."""
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=True, opencode_client=UnavailableOpenCodeExecutionClient(), environ={"FREELANCERSTUDIO_PHASED_QA_BACKEND": "host"})

    resolved = adapter._adapter()
    assert isinstance(resolved, PhasedLiveOpenCodeExecutionAdapter)
    assert resolved._qa_runner is run_qa_commands


def test_default_qa_backend_is_docker(tmp_path):
    adapter = ConfigurationBackedExecutionAdapter(_configuration(tmp_path), live=True, opencode_client=UnavailableOpenCodeExecutionClient())

    resolved = adapter._adapter()
    assert resolved._qa_runner is run_qa_commands_in_docker
