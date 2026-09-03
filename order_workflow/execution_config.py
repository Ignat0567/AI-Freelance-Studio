from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil

import config_storage
import secret_store

from .models import StrictDomainModel
from .production_adapter import live_opencode_execution_enabled


LOCAL_NO_KEY_PROVIDERS = frozenset({"ollama", "opencode", "opencode_bridge", "claude_code", "grok", "grok_cli"})
SUPPORTED_EXECUTION_PROVIDERS = frozenset({*secret_store.PROVIDER_ENV_NAMES, *LOCAL_NO_KEY_PROVIDERS})


class ProviderStatus(StrictDomainModel):
    code: str
    provider: str
    configured: bool
    secret_required: bool
    secret_present: bool
    message: str
    action: str = "Open Settings"


class ModelStatus(StrictDomainModel):
    code: str
    model: str
    selected: bool
    supported: bool
    message: str
    action: str = "Open Settings"


class OpenCodeStatus(StrictDomainModel):
    code: str
    available: bool
    version: str = ""
    path: str = ""
    message: str
    action: str = "Open Settings"


class WorkspaceStatus(StrictDomainModel):
    code: str
    root: str
    available: bool
    writable: bool
    message: str
    action: str = "Open Settings"


class LiveOptInStatus(StrictDomainModel):
    code: str
    enabled: bool
    message: str
    action: str = "Restart Studio"


class ExecutionConfigurationSnapshot(StrictDomainModel):
    provider: ProviderStatus
    model: ModelStatus
    opencode: OpenCodeStatus
    workspace: WorkspaceStatus
    live_opt_in: LiveOptInStatus
    qa_status: str = "not_run"


class ExecutionConfigurationProvider:
    def __init__(
        self,
        *,
        config_loader: Callable[[], dict] = config_storage.load_studio_keys,
        secret_lookup: Callable[[str, dict | None], str] = secret_store.get_secret,
        opencode_version_probe: Callable[[], tuple[bool, str, str]] | None = None,
        active_backend_probe: Callable[[], str] | None = None,
        workspace_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._secret_lookup = secret_lookup
        self._opencode_version_probe = opencode_version_probe or _probe_opencode_version
        self._active_backend_probe = active_backend_probe or _active_cli_backend
        self._workspace_root = Path(workspace_root).resolve() if workspace_root is not None else Path(config_storage.DATA_DIR, "generated_projects").resolve()
        # Whether this is the application's own default location or somewhere the operator
        # named. The two deserve opposite treatment when the directory is missing: see
        # get_workspace_status().
        self._workspace_root_is_default = workspace_root is None
        self._environ = environ

    def snapshot(self) -> ExecutionConfigurationSnapshot:
        config = self._config_loader()
        return ExecutionConfigurationSnapshot(
            provider=self.get_provider_status(config),
            model=self.get_model_status(config),
            opencode=self.get_opencode_status(),
            workspace=self.get_workspace_status(),
            live_opt_in=self.get_live_opt_in_status(config),
        )

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def get_provider_status(self, config: dict | None = None) -> ProviderStatus:
        config = config if config is not None else self._config_loader()
        provider = _selected_provider(config, self._active_backend_probe)
        if not provider:
            return ProviderStatus(code="provider_not_configured", provider="", configured=False, secret_required=True, secret_present=False, message="No provider is configured.")
        if provider not in SUPPORTED_EXECUTION_PROVIDERS:
            return ProviderStatus(code="provider_not_configured", provider=provider, configured=False, secret_required=True, secret_present=False, message=f"Provider {provider} is not supported for execution.")
        secret_required = provider not in LOCAL_NO_KEY_PROVIDERS
        secret_present = True if not secret_required else bool(self._secret_lookup(secret_store.provider_secret_name(provider), config))
        if secret_required and not secret_present:
            return ProviderStatus(code="provider_secret_missing", provider=provider, configured=False, secret_required=True, secret_present=False, message=f"Provider {provider} is selected but its API key is missing.")
        return ProviderStatus(code="provider_configured", provider=provider, configured=True, secret_required=secret_required, secret_present=secret_present, message=f"Provider {provider} is configured." if secret_required else f"Provider {provider} uses local/no-key execution mode.")

    def get_model_status(self, config: dict | None = None) -> ModelStatus:
        config = config if config is not None else self._config_loader()
        model = _selected_model(config, self._active_backend_probe)
        if not model:
            return ModelStatus(code="model_not_selected", model="", selected=False, supported=False, message="No model is selected.")
        supported = len(model) <= 160 and not any(part in model for part in ("..", "\x00"))
        if not supported:
            return ModelStatus(code="model_unsupported_for_execution", model=model[:80], selected=True, supported=False, message="Selected model is not supported for execution.")
        return ModelStatus(code="model_selected", model=model, selected=True, supported=True, message=f"Model {model} is selected.")

    def get_opencode_status(self) -> OpenCodeStatus:
        available, version, path = self._opencode_version_probe()
        if not available:
            return OpenCodeStatus(code="opencode_unavailable", available=False, message="OpenCode is not available.")
        return OpenCodeStatus(code="opencode_available", available=True, version=version, path=Path(path).name if path else "opencode", message=f"OpenCode {version or 'CLI'} is available.")

    def get_workspace_status(self) -> WorkspaceStatus:
        root = self._workspace_root
        if not root.is_dir() and self._workspace_root_is_default:
            # The default root lives under the application's own data directory and holds
            # nothing but generated output, so its absence is not a decision anyone made --
            # it is simply a first run. Blocking on it made a fresh checkout unable to
            # execute a single order: generated_projects/ is gitignored, nothing created it,
            # and the acceptance run on 2026-08-23 failed 3/3 in 34 seconds because of it.
            # Invisible from a working tree, where the directory has existed since day one.
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if not root.is_dir():
            # Still missing: either creation failed, or an operator pointed the setting at a
            # path that does not exist -- which is a real choice to correct, not a first run.
            return WorkspaceStatus(code="workspace_root_unavailable", root=root.name or "generated_projects", available=False, writable=False, message="Generated projects workspace root is missing.")
        writable = _is_writable(root)
        if not writable:
            return WorkspaceStatus(code="workspace_not_writable", root=root.name or "generated_projects", available=True, writable=False, message="Generated projects workspace is not writable.")
        return WorkspaceStatus(code="workspace_ready", root=root.name or "generated_projects", available=True, writable=True, message=f"{root.name or 'generated_projects'} is writable.")

    def get_live_opt_in_status(self, config: dict | None = None) -> LiveOptInStatus:
        loaded = config if config is not None else self._config_loader()
        if live_opencode_execution_enabled(self._environ, loaded):
            return LiveOptInStatus(code="live_execution_opt_in_enabled", enabled=True, message="Live execution opt-in is enabled.")
        return LiveOptInStatus(
            code="live_execution_opt_in_required",
            enabled=False,
            message="Enable Live coding execution in Settings, or set FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION=1.",
            action="Open Settings",
        )


def _selected_provider(config: dict | None, active_backend_probe: Callable[[], str] = None) -> str:
    if not isinstance(config, dict):
        return ""
    system = config.get("_system", {}) if isinstance(config.get("_system"), dict) else {}
    global_ai = config.get("_global_ai", {}) if isinstance(config.get("_global_ai"), dict) else {}
    if global_ai.get("connection_type") == "grok_subscription":
        return "grok"
    if global_ai.get("connection_type") == "claude_subscription":
        # The claude-subscription connection template stores provider_id "anthropic" (the
        # vendor identity, shared with the plain API-key connection) even though it
        # authenticates via delegated `claude` CLI login and needs no API key. Normalize to
        # order_workflow's own "claude_code" identity so execution readiness reflects reality.
        return "claude_code"
    provider = str(system.get("global_provider") or global_ai.get("provider") or config.get("global_provider") or "").strip().lower()
    if provider:
        return provider
    bridge = _selected_opencode_bridge(config)
    if bridge:
        return "opencode_bridge"
    return (active_backend_probe or _active_cli_backend)()


def _selected_model(config: dict | None, active_backend_probe: Callable[[], str] = None) -> str:
    if not isinstance(config, dict):
        return ""
    system = config.get("_system", {}) if isinstance(config.get("_system"), dict) else {}
    global_ai = config.get("_global_ai", {}) if isinstance(config.get("_global_ai"), dict) else {}
    model = str(system.get("global_model") or global_ai.get("model") or config.get("global_model") or "").strip()
    if model:
        return model
    bridge = _selected_opencode_bridge(config)
    if bridge:
        return str(bridge.get("configured_model") or "").strip()
    backend = (active_backend_probe or _active_cli_backend)()
    if backend == "claude_code":
        return "claude/default"
    if backend == "ollama":
        return "qwen2.5-coder:14b"
    if backend == "grok":
        return "grok-4.6"
    if backend == "openrouter":
        return "anthropic/claude-sonnet-4"
    return ""


def _active_cli_backend() -> str:
    from .claude_code_client import active_coding_backend  # local import: avoids a claude_code_client<->execution_config import cycle

    return active_coding_backend()


def _selected_opencode_bridge(config: dict) -> dict:
    connections = config.get("_provider_connections")
    if not isinstance(connections, list):
        return {}
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        connection_type = str(connection.get("connection_type") or "").strip().lower()
        if connection_type not in {"opencode_bridge", "opencode_oauth_bridge"}:
            continue
        if connection.get("enabled", True) is False:
            continue
        if connection.get("readiness_status") != "ready":
            continue
        if str(connection.get("configured_model") or "").strip():
            return connection
    return {}


def _probe_opencode_version() -> tuple[bool, str, str]:
    path = shutil.which("opencode.cmd") or shutil.which("opencode.exe") or shutil.which("opencode") or ""
    if not path:
        return False, "", ""
    try:
        import subprocess

        completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
    except Exception:
        return False, "", Path(path).name
    if completed.returncode != 0:
        return False, "", Path(path).name
    return True, (completed.stdout or completed.stderr).strip().splitlines()[0][:40], Path(path).name


def _is_writable(root: Path) -> bool:
    probe = root / ".freelancerstudio-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
