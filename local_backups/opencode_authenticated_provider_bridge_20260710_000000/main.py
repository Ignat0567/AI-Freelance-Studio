import json
import os
import sys
import time
import asyncio
import uuid
import shutil
import zipfile
import tarfile
import mimetypes
import importlib.util
import re
import glob
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, UploadFile, File as FastAPIFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import threading as _threading
import project_state

# OpenCode bridge (optional — for real AI-assisted code generation)
try:
    from opencode_bridge import ensure_opencode, stop_opencode, sync_opencode_config, get_opencode_status, start_opencode_web, start_opencode_auth_login, get_bridge as _get_oc_bridge
    _HAS_OPENCODE = True
except ImportError:
    _HAS_OPENCODE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Shared critical rules injected into ALL agent prompts ─────────────────
# Все тонкости, нюансы и грабли, выявленные в процессе разработки.
_CRITICAL_RULES = (
    "CRITICAL RULES — VIOLATING ANY WILL CAUSE PROJECT REJECTION:\n"
    "- Do not hardcode secrets or credentials; use configuration, environment variables, or explicit placeholders.\n"
    "- ALL file paths use forward slash (/) not backslash (\\) even on Windows.\n"
    "- Import names MUST exactly match the exports of the dependency file — verify before importing.\n"
    "- Test assertions MUST use the exact field names/types from actual models/schemas.\n"
    "- Tests MUST only test endpoints that actually exist in the API code — cross-reference before writing.\n"
    "- Do not leave placeholder/stub implementation, fake output, or skipped/falsified tests.\n"
    "- Container names in docker-compose.yml must NOT start with a digit. Use underscores or letters.\n"
    "- ALL dependencies used in code MUST appear in the project's dependency manifest.\n"
)


def force_import_local_module(module_name, filename):
    full_path = os.path.join(BASE_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ai_utils = force_import_local_module("ai_utils", "ai_utils.py")
search_utils = force_import_local_module("search_utils", "search_utils.py")
ai_developer = force_import_local_module("ai_developer", "ai_developer.py")
docker_tester = force_import_local_module("docker_tester", "docker_tester.py")
goldie_agent = force_import_local_module("goldie_agent", "goldie_agent.py")
qa_engine_module = force_import_local_module("qa_engine_module", "qa_engine.py")
requirements_checker = force_import_local_module("requirements_checker", "requirements_checker.py")
proposal_generator = force_import_local_module("proposal_generator", "proposal_generator.py")
connected_accounts = force_import_local_module("connected_accounts", "connected_accounts.py")
project_spec_module = force_import_local_module("project_spec_module", "project_spec.py")
delivery_audit_module = force_import_local_module("delivery_audit_module", "delivery_audit.py")

check_all_req = requirements_checker.check_all
get_components = requirements_checker.get_components
install_component = requirements_checker.install_component

generate_proposal = proposal_generator.generate_proposal
refine_spec = proposal_generator.refine_spec

get_accounts = connected_accounts.get_accounts
add_account_fn = connected_accounts.add_account
remove_account_fn = connected_accounts.remove_account
sync_account_fn = connected_accounts.sync_account
PLATFORMS_LIST = connected_accounts.PLATFORMS

ask_studio_ai_with_history = ai_utils.ask_studio_ai_with_history
QAEngine = qa_engine_module.QAEngine
search_freelance_jobs = search_utils.search_freelance_jobs
AVAILABLE_PLATFORMS = search_utils.AVAILABLE_PLATFORMS
ensure_project_spec_bundle = project_spec_module.ensure_project_spec_bundle
acceptance_summary = project_spec_module.acceptance_summary
detect_project_profiles = project_spec_module.detect_project_profiles
record_acceptance_evidence = project_spec_module.record_acceptance_evidence
ensure_acceptance_evidence_history = project_spec_module.ensure_acceptance_evidence_history
append_agent_review_issues = project_spec_module.append_agent_review_issues
build_product_judge_input = project_spec_module.build_product_judge_input
Issue = project_spec_module.Issue
run_final_delivery_audit = delivery_audit_module.run_final_delivery_audit
write_delivery_report = delivery_audit_module.write_delivery_report

app = FastAPI(title="FreelancerStudio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "FreelancerStudio", "port": 8080}


def _backend_health_available() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
            if resp.status == 200:
                return True, "ok"
            return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


AI_PROVIDER_MODELS = {
    "openai": ["gpt-5.5", "gpt-5", "gpt-4.1"],
    "nvidia": ["meta/llama-3.3-70b-instruct"],
    "anthropic": ["claude-sonnet-4-20250514"],
    "google": ["gemini-2.0-flash-001"],
    "groq": ["llama-3.1-70b-versatile"],
    "mistral": ["mistral-large-latest"],
    "deepseek": ["deepseek-chat"],
    "together": ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"],
    "ollama": ["codellama", "llama3.1", "mistral"],
}


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]


def _provider_key_name(provider: str) -> str:
    return f"{provider}_key"


def _get_saved_system_settings(data: dict | None = None) -> dict:
    source = data if data is not None else load_studio_keys()
    saved = source.get("_system", {}) if isinstance(source.get("_system"), dict) else {}
    return {**DEFAULT_SYSTEM_SETTINGS, **saved}

CONFIG_FILE = os.path.join(BASE_DIR, "studio_config.json")
PROJECTS_DATA_DIR = os.path.join(BASE_DIR, "projects_data")
PROJECTS_STATE_FILE = os.path.join(BASE_DIR, "projects_state.json")


def load_studio_keys():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_studio_keys(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_projects_state():
    """Load persisted projects and tasks from disk on server start."""
    state, error = project_state.read_json(PROJECTS_STATE_FILE)
    if not state:
        return {}, {}
    return state.get("projects", {}), state.get("tasks", {})


def _save_projects_state(preserve_missing: bool = True):
    """Persist active_projects and PROJECT_TASKS to disk."""
    try:
        projects = active_projects
        tasks = PROJECT_TASKS
        if preserve_missing and os.path.exists(PROJECTS_STATE_FILE):
            persisted_projects, persisted_tasks = _load_projects_state()
            authoritative_paths = {
                os.path.normcase(os.path.abspath(str(project.get("target_path") or "")))
                for project in active_projects.values() if project.get("target_path")
            }
            persisted_projects = {
                project_id: project for project_id, project in persisted_projects.items()
                if not project.get("target_path") or os.path.normcase(os.path.abspath(str(project.get("target_path")))) not in authoritative_paths
            }
            projects = {**persisted_projects, **active_projects}
            tasks = {**persisted_tasks, **PROJECT_TASKS}
        project_state.atomic_write_json(PROJECTS_STATE_FILE, {"projects": projects, "tasks": tasks})
        for project in active_projects.values():
            project_state.persist_project_state(project)
    except Exception as exc:
        for project in active_projects.values():
            project.setdefault("persistence_errors", []).append(f"projects_state:{exc}")


class KeysUpdatePayload(BaseModel):
    keys: Dict[str, str] = {}
    model_config = {"extra": "allow"}


class AISettingsPayload(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""


class ProviderTestPayload(BaseModel):
    provider: str = ""
    api_key: str = ""


class OpenCodeLoginPayload(BaseModel):
    provider: str = ""
    method: str = ""


class ChatPayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    message: str
    chat_history: List[Dict[str, str]] = []
    provider: str = "nvidia"
    model_name: str = "nvidia/nemotron-4-340b-instruct"


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
    model_config = {"extra": "allow"}
    platform: str = "manual"
    jobTitle: str = ""
    title: str = ""
    description: str = ""
    initial_description: str = ""
    budget: str = "?"


class ProjectApprovePayload(BaseModel):
    model_config = {"extra": "allow"}
    approved: bool = True
    autonomous_mode: bool = True


class ProjectClaimPayload(BaseModel):
    model_config = {"extra": "allow"}
    platform: str = "unknown"
    job_id: str = ""
    title: str = ""
    description: str = ""
    budget: str = "?"
    url: str = ""


active_projects: Dict[str, Dict[str, Any]] = {}
# Task tracking system - stores tasks per project
PROJECT_TASKS: Dict[str, list] = {}  # project_id -> list of tasks

# Load persisted state from disk (survives server restarts)
_persisted_projects, _persisted_tasks = _load_projects_state()
active_projects.update(_persisted_projects)
PROJECT_TASKS.update(_persisted_tasks)


def _project_from_durable_state(state: dict[str, Any]) -> dict[str, Any]:
    project = {
        "project_id": state["project_id"],
        "id": state["project_id"],
        "title": state.get("project_name", "Untitled"),
        "jobTitle": state.get("project_name", "Untitled"),
        "target_path": state["project_path"],
        "status": state.get("current_state", "created"),
        "_phase": state.get("pipeline_stage", ""),
        "project_spec": state.get("project_spec", {}),
        "project_profiles": state.get("effective_project_profile", []),
        "product_runtime_profile": state.get("product_runtime_profile", {}),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "acceptance_criteria_source": state.get("acceptance_criteria_source", "project_contract"),
        "issues": state.get("issues", []),
        "logs": ["[RECOVERY] Loaded authoritative per-project durable state."],
        "recovery_status": state.get("recovery_status", "fully_recovered"),
        **state.get("gates", {}),
    }
    ledger, _ = project_state.load_evidence_ledger(project["target_path"])
    if ledger:
        project["acceptance_evidence"] = {}
        for criterion in project["acceptance_criteria"]:
            history = ledger.get("history", {}).get(str(criterion.get("id") or ""), [])
            evidence = [item.get("evidence", {}) for item in history]
            criterion["evidence"] = evidence
            project["acceptance_evidence"][criterion.get("id", "")] = evidence
    return project


def _discover_durable_projects() -> None:
    generated_root = os.path.join(BASE_DIR, "generated_projects")
    for discovered in project_state.discover_projects(generated_root):
        if discovered.get("classification") != "fully_recovered":
            continue
        state = discovered.get("project") or {}
        project_id = state.get("project_id")
        durable_path = os.path.normcase(os.path.abspath(state.get("project_path", "")))
        conflicting_ids = [
            existing_id for existing_id, existing in active_projects.items()
            if durable_path and os.path.normcase(os.path.abspath(str(existing.get("target_path") or ""))) == durable_path
        ]
        for existing_id in conflicting_ids:
            active_projects.pop(existing_id, None)
        if project_id:
            active_projects[project_id] = _project_from_durable_state(state)


_discover_durable_projects()


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


def get_next_task_id(project_id: str) -> str:
    return f"task_{uuid.uuid4().hex[:8]}"


@app.get("/api/projects/{project_id}/tasks")
def get_project_tasks(project_id: str):
    """Get all tasks for a project."""
    tasks = PROJECT_TASKS.get(project_id, [])
    return {"tasks": tasks}


@app.post("/api/projects/{project_id}/tasks")
def create_task(project_id: str, task: TaskModel):
    """Create a new task in a project."""
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    task_id = get_next_task_id(project_id)
    new_task = {
        "id": task_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "assignee": task.assignee,
        "priority": task.priority,
        "due_date": task.due_date,
        "created_at": datetime.now().isoformat(),
        "comments": [],
    }
    if project_id not in PROJECT_TASKS:
        PROJECT_TASKS[project_id] = []
    PROJECT_TASKS[project_id].append(new_task)
    _save_projects_state()
    return new_task


@app.put("/api/projects/{project_id}/tasks/{task_id}")
def update_task(project_id: str, task_id: str, updates: TaskUpdateModel):
    """Update a task."""
    tasks = PROJECT_TASKS.get(project_id, [])
    for task in tasks:
        if task["id"] == task_id:
            if updates.title is not None:
                task["title"] = updates.title
            if updates.description is not None:
                task["description"] = updates.description
            if updates.status is not None:
                task["status"] = updates.status
            if updates.assignee is not None:
                task["assignee"] = updates.assignee
            if updates.priority is not None:
                task["priority"] = updates.priority
            if updates.due_date is not None:
                task["due_date"] = updates.due_date
            task["updated_at"] = datetime.now().isoformat()
            _save_projects_state()
            return task
    raise HTTPException(status_code=404, detail="Task not found")


@app.delete("/api/projects/{project_id}/tasks/{task_id}")
def delete_task(project_id: str, task_id: str):
    """Delete a task."""
    tasks = PROJECT_TASKS.get(project_id, [])
    for i, task in enumerate(tasks):
        if task["id"] == task_id:
            tasks.pop(i)
            _save_projects_state()
            return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/projects/{project_id}/tasks/{task_id}/comments")
def add_task_comment(project_id: str, task_id: str, payload: dict):
    """Add a comment to a task."""
    tasks = PROJECT_TASKS.get(project_id, [])
    for task in tasks:
        if task["id"] == task_id:
            comment = {
                "id": f"comment_{len(task['comments']) + 1}",
                "text": payload.get("text", ""),
                "author": payload.get("author", ""),
                "created_at": datetime.now().isoformat(),
            }
            task["comments"].append(comment)
            return comment
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/api/config/keys")
def get_stored_keys():
    keys = load_studio_keys()
    masked_keys = {}
    saved_keys = []
    for k, v in keys.items():
        if v:
            masked_keys[k.replace("_key", "")] = True
            if not k.startswith("_"):
                saved_keys.append(k)
    masked_keys["has_nvidia"] = bool(keys.get("nvidia_key"))
    masked_keys["has_openai"] = bool(keys.get("openai_key"))
    masked_keys["has_anthropic"] = bool(keys.get("anthropic_key"))
    masked_keys["has_freelancer"] = bool(keys.get("freelancer_client_id") and keys.get("freelancer_client_secret"))
    masked_keys["has_upwork"] = bool(keys.get("upwork_client_id") and keys.get("upwork_client_secret"))
    masked_keys["saved_keys"] = sorted(saved_keys)
    return masked_keys


@app.post("/api/config/keys")
def update_stored_keys(payload: KeysUpdatePayload):
    """Saves active user keys locally on disk."""
    data = load_studio_keys()
    for key, value in payload.keys.items():
        if value:
            data[key] = value
    save_studio_keys(data)
    return {"status": "saved"}


@app.delete("/api/config/keys")
def reset_all_credentials():
    """Clears all stored API credentials from local disk configuration."""
    save_studio_keys({})
    return {"status": "reset"}


@app.delete("/api/config/keys/{key_name}")
def delete_stored_key(key_name: str):
    """Deletes one stored credential while preserving other settings."""
    if key_name.startswith("_") or "/" in key_name or "\\" in key_name:
        raise HTTPException(status_code=400, detail="Invalid key name")
    data = load_studio_keys()
    existed = key_name in data
    data.pop(key_name, None)
    save_studio_keys(data)
    return {"status": "deleted", "key": key_name, "existed": existed}


def _ai_settings_response(data: dict | None = None) -> dict:
    cfg = data if data is not None else load_studio_keys()
    system = _get_saved_system_settings(cfg)
    provider = system.get("global_provider", "nvidia")
    model = system.get("global_model") or AI_PROVIDER_MODELS.get(provider, [""])[0]
    saved = {}
    for name in AI_PROVIDER_MODELS:
        key = cfg.get(_provider_key_name(name)) or cfg.get(f"{name}_api_key") or ""
        saved[name] = {"saved": bool(key), "masked": _mask_secret(key)}
    return {
        "provider": provider,
        "model": model,
        "providers": AI_PROVIDER_MODELS,
        "saved_keys": saved,
        "opencode_available": _HAS_OPENCODE,
    }


@app.get("/api/config/ai")
def get_ai_settings():
    return _ai_settings_response()


@app.post("/api/config/ai")
def save_ai_settings(payload: AISettingsPayload):
    provider = (payload.provider or "").strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    model = (payload.model or "").strip() or AI_PROVIDER_MODELS[provider][0]
    data = load_studio_keys()
    system = data.get("_system", {}) if isinstance(data.get("_system"), dict) else {}
    system["global_provider"] = provider
    system["global_model"] = model
    data["_system"] = system
    SYSTEM_SETTINGS["global_provider"] = provider
    SYSTEM_SETTINGS["global_model"] = model
    if payload.api_key and payload.api_key.strip():
        data[_provider_key_name(provider)] = payload.api_key.strip()
    save_studio_keys(data)
    return {"status": "saved", **_ai_settings_response(data)}


@app.delete("/api/config/ai/key/{provider}")
def delete_ai_provider_key(provider: str):
    provider = provider.strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    data = load_studio_keys()
    existed = False
    for key_name in (_provider_key_name(provider), f"{provider}_api_key"):
        existed = key_name in data or existed
        data.pop(key_name, None)
    save_studio_keys(data)
    return {"status": "deleted", "provider": provider, "existed": existed, **_ai_settings_response(data)}


def _test_provider_key(provider: str, key: str) -> tuple[bool, str]:
    if provider == "ollama":
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
                return resp.status == 200, "Ollama is reachable" if resp.status == 200 else f"HTTP {resp.status}"
        except Exception as e:
            return False, f"Ollama is not reachable: {e}"
    if not key:
        return False, f"No API key saved for {provider}"
    endpoints = {
        "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}),
        "nvidia": ("https://integrate.api.nvidia.com/v1/models", {"Authorization": f"Bearer {key}"}),
        "groq": ("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"}),
        "mistral": ("https://api.mistral.ai/v1/models", {"Authorization": f"Bearer {key}"}),
        "deepseek": ("https://api.deepseek.com/models", {"Authorization": f"Bearer {key}"}),
        "together": ("https://api.together.xyz/v1/models", {"Authorization": f"Bearer {key}"}),
        "google": (f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", {}),
        "anthropic": ("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    }
    url, headers = endpoints.get(provider, ("", {}))
    if not url:
        return False, "Unsupported provider test"
    try:
        req = urllib.request.Request(url, headers={**headers, "User-Agent": "FreelancerStudio"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300, "Provider key accepted" if 200 <= resp.status < 300 else f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"Provider rejected key or request: HTTP {e.code}"
    except Exception as e:
        return False, f"Connection test failed: {e}"


@app.post("/api/config/ai/test")
def test_ai_provider(payload: ProviderTestPayload):
    data = load_studio_keys()
    system = _get_saved_system_settings(data)
    provider = (payload.provider or system.get("global_provider", "nvidia")).strip().lower()
    if provider not in AI_PROVIDER_MODELS:
        raise HTTPException(400, "Unsupported provider")
    key = payload.api_key.strip() if payload.api_key else (data.get(_provider_key_name(provider)) or data.get(f"{provider}_api_key") or "")
    ok, message = _test_provider_key(provider, key)
    return {"status": "ok" if ok else "error", "provider": provider, "key_saved": bool(key), "message": message}


@app.post("/api/config/ai/apply-opencode")
def apply_ai_settings_to_opencode():
    if not _HAS_OPENCODE:
        raise HTTPException(500, "OpenCode bridge is not available")
    ok = sync_opencode_config()
    if not ok:
        raise HTTPException(500, "Failed to write OpenCode config")
    return {"status": "applied", "message": "OpenCode config regenerated. Restart OpenCode to use the new settings."}


@app.get("/api/opencode/status")
def opencode_status():
    if not _HAS_OPENCODE:
        return {"installed": False, "error": "OpenCode bridge is not available"}
    return get_opencode_status()


@app.post("/api/opencode/web")
def opencode_web_login():
    if not _HAS_OPENCODE:
        raise HTTPException(500, "OpenCode bridge is not available")
    result = start_opencode_web(workdir=BASE_DIR)
    if result.get("status") == "error":
        raise HTTPException(500, result.get("message", "Failed to start OpenCode web"))
    return result


@app.post("/api/opencode/login")
def opencode_auth_login(payload: OpenCodeLoginPayload):
    if not _HAS_OPENCODE:
        raise HTTPException(500, "OpenCode bridge is not available")
    result = start_opencode_auth_login(payload.provider.strip(), payload.method.strip())
    if result.get("status") == "error":
        raise HTTPException(500, result.get("message", "Failed to start OpenCode login"))
    return result


@app.get("/api/config/ai/verify")
def verify_ai_settings():
    data = load_studio_keys()
    system = _get_saved_system_settings(data)
    provider = system.get("global_provider", "nvidia")
    model = system.get("global_model") or AI_PROVIDER_MODELS.get(provider, [""])[0]
    key_exists = provider == "ollama" or bool(data.get(_provider_key_name(provider)) or data.get(f"{provider}_api_key"))
    opencode_generated = False
    mcp_exists = False
    if _HAS_OPENCODE:
        opencode_generated = sync_opencode_config()
        config_path = os.path.join(os.path.expanduser("~/.config/opencode"), "opencode.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                oc = json.load(f)
            mcp_exists = bool(oc.get("mcp", {}).get("freelancer-studio"))
        except Exception:
            mcp_exists = False
    return {
        "provider": provider,
        "model": model,
        "key_exists": key_exists,
        "opencode_config_generated": opencode_generated,
        "mcp_config_exists": mcp_exists,
    }


ALLOWED_SYSTEM_KEYS = [
    "global_provider", "global_model",
    "theme", "accent_color", "animation_speed", "font_size",
    "language", "auto_save", "notifications_enabled",
    "default_budget", "polling_interval", "log_detail",
    "vscode_path", "pycharm_path",
]


@app.get("/api/config/system")
def get_system_config():
    all_keys = load_studio_keys()
    saved = all_keys.get("_system", {})
    merged = {**DEFAULT_SYSTEM_SETTINGS, **SYSTEM_SETTINGS, **saved}
    return {k: merged.get(k, DEFAULT_SYSTEM_SETTINGS.get(k)) for k in ALLOWED_SYSTEM_KEYS}


@app.post("/api/config/system")
def update_system_config(payload: Dict[str, Any]):
    data = load_studio_keys()
    saved = data.get("_system", {})
    for key in ALLOWED_SYSTEM_KEYS:
        if key in payload:
            saved[key] = payload[key]
            SYSTEM_SETTINGS[key] = payload[key]
    data["_system"] = saved
    save_studio_keys(data)
    return {"status": "saved"}


@app.get("/api/system/requirements")
def list_requirements():
    return {"components": get_components()}


@app.post("/api/system/check")
def check_requirements():
    return {"results": check_all_req()}


@app.post("/api/system/install/{component_id}")
def install_requirement(component_id: str):
    result = install_component(component_id)
    return result


@app.post("/api/proposals/generate")
def create_proposal(payload: Dict[str, Any]):
    job_desc = payload.get("job_description", "").strip()
    if not job_desc:
        raise HTTPException(400, "job_description is required")
    agent_data = agent_configs.get("goldie", {})
    provider, model = get_agent_provider_model("goldie")
    result = generate_proposal(
        job_description=job_desc,
        provider=provider,
        model=model,
        temperature=agent_data.get("temperature", 0.3),
    )
    return result


@app.post("/api/proposals/refine")
def refine_project_spec(payload: Dict[str, Any]):
    original = payload.get("original_job", "").strip()
    answers = payload.get("client_answers", "").strip()
    if not original or not answers:
        raise HTTPException(400, "original_job and client_answers are required")
    agent_data = agent_configs.get("maya", {})
    provider, model = get_agent_provider_model("maya")
    result = refine_spec(
        original_job=original,
        client_answers=answers,
        provider=provider,
        model=model,
        temperature=agent_data.get("temperature", 0.2),
    )
    return result


@app.get("/api/accounts")
def list_accounts():
    return {"accounts": get_accounts(), "platforms": PLATFORMS_LIST}


@app.post("/api/accounts")
def create_account(payload: Dict[str, Any]):
    platform = payload.get("platform", "")
    label = payload.get("label", "")
    credentials = payload.get("credentials", {})
    if platform not in PLATFORMS_LIST:
        raise HTTPException(400, f"Unknown platform: {platform}")
    acc_id = add_account_fn(platform, label, credentials)
    return {"id": acc_id, "status": "connected"}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str):
    remove_account_fn(account_id)
    return {"status": "removed"}


@app.post("/api/accounts/{account_id}/sync")
def sync_account(account_id: str):
    result = sync_account_fn(account_id)
    return result


AGENT_MODELS = {
    "nvidia": "meta/llama-3.3-70b-instruct",
    "openai": "gpt-4",
    "ollama": "dolphin-mistral:7b",
    "anthropic": "claude-sonnet-4-20250514",
}

# ── Resume / Phase tracking ──────────────────────────────────────────────────
_PHASE_ORDER = ["planning", "designing", "testing", "coding", "review", "qa", "product_judge"]

_TERMINAL_PROJECT_STATES = {
    "completed",
    "failed",
    "failed_qa",
    "blocked",
    "needs_credentials",
    "cancelled",
}

_STATE_DISPLAY = {
    "created": "Created",
    "meeting": "Analyzing",
    "planning": "Planning",
    "designing": "Designing",
    "testing": "Testing",
    "coding": "Generating",
    "review": "Reviewing",
    "verifying": "Verifying",
    "repairing": "Repairing",
    "final_audit": "Final audit",
    "product_judge": "Product judge",
    "completed": "Completed",
    "failed": "Failed",
    "failed_qa": "Failed QA",
    "blocked": "Blocked",
    "needs_credentials": "Needs credentials",
    "needs_user_input": "Needs human input",
    "needs_human_input": "Needs human input",
    "awaiting_input": "Awaiting input",
    "cancelled": "Cancelled",
}

AGENT_STAGE_METADATA = {
    "alex": {"stage": "planning", "display_role": "Project Manager"},
    "maya": {"stage": "planning", "display_role": "Business Analyst"},
    "elena": {"stage": "designing", "display_role": "UI/UX Designer"},
    "bugcatcher": {"stage": "testing", "display_role": "QA Engineer"},
    "codex": {"stage": "coding", "display_role": "Software Architect"},
    "sentinel": {"stage": "review", "display_role": "Security Auditor"},
    "lupa": {"stage": "review", "display_role": "Code Reviewer"},
    "goldie": {"stage": "finance", "display_role": "Financial Advisor"},
    "product_judge": {"stage": "product_judge", "display_role": "Independent Product Judge"},
}

PIPELINE_STAGE_METADATA = {
    "planning": {"label": "Planning"},
    "designing": {"label": "Design"},
    "testing": {"label": "QA (TDD)"},
    "coding": {"label": "Generating"},
    "review": {"label": "Review"},
    "qa": {"label": "QA"},
    "verifying": {"label": "Verifying"},
    "repairing": {"label": "Repairing"},
    "final_audit": {"label": "Final Audit"},
    "product_judge": {"label": "Product Judge"},
}
PIPELINE_UI_STAGE_ORDER = ["planning", "designing", "testing", "coding", "review", "verifying", "repairing", "final_audit", "product_judge"]

_ALLOWED_STATE_TRANSITIONS = {
    "created": {"meeting", "planning", "cancelled", "needs_human_input", "needs_credentials"},
    "meeting": {"planning", "cancelled", "blocked", "failed"},
    "planning": {"awaiting_input", "designing", "cancelled", "failed", "blocked", "needs_human_input", "needs_credentials"},
    "awaiting_input": {"planning", "designing", "cancelled", "needs_human_input"},
    "designing": {"testing", "cancelled", "failed", "blocked"},
    "testing": {"coding", "verifying", "cancelled", "failed", "blocked"},
    "coding": {"review", "verifying", "cancelled", "failed", "blocked", "needs_credentials"},
    "review": {"review", "verifying", "repairing", "cancelled", "failed", "blocked"},
    "verifying": {"repairing", "final_audit", "needs_user_input", "needs_human_input", "needs_credentials", "failed_qa", "cancelled", "failed", "blocked"},
    "repairing": {"review", "verifying", "failed_qa", "cancelled", "failed", "blocked"},
    "final_audit": {"product_judge", "completed", "repairing", "failed_qa", "cancelled", "failed", "blocked"},
    "product_judge": {"completed", "repairing", "failed_qa", "cancelled", "failed", "blocked"},
    "needs_user_input": {"verifying", "cancelled", "failed_qa", "failed"},
    "needs_human_input": {"planning", "verifying", "cancelled", "failed"},
    "failed_qa": {"planning", "verifying", "cancelled"},
    "failed": {"planning", "verifying", "cancelled"},
    "blocked": {"planning", "verifying", "cancelled"},
    "needs_credentials": {"planning", "verifying", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _normalize_state(state: str) -> str:
    if not state:
        return "created"
    if state.startswith("review_iteration_"):
        return "review"
    return state


def _state_display_name(state: str) -> str:
    normalized = _normalize_state(state)
    if normalized in _STATE_DISPLAY:
        return _STATE_DISPLAY[normalized]
    return (state or "unknown").replace("_", " ").title()


def _delivery_gates_satisfied(project: dict) -> bool:
    return bool(
        project.get("_generation_finished")
        and project.get("_qa_passed")
        and project.get("_final_audit_passed")
        and project.get("_product_judge_passed")
    )


def _can_transition(project: dict, new_state: str) -> bool:
    current = project.get("status", "created")
    if current == new_state:
        return True
    if new_state == "cancelled":
        return True
    if project.get("cancel_requested") and new_state != "cancelled":
        return False
    current_norm = _normalize_state(current)
    new_norm = _normalize_state(new_state)
    allowed = _ALLOWED_STATE_TRANSITIONS.get(current_norm, set())
    return new_norm in allowed


def _set_project_status(project: dict, new_state: str, *, reason: str = "", force: bool = False, save: bool = False) -> bool:
    project.setdefault("logs", [])
    current = project.get("status", "created")

    if new_state == "completed":
        if project.get("cancel_requested") or current == "cancelled":
            project["logs"].append("[PROJECT STATE] Completion blocked (project is cancelled).")
            return False
        if not _delivery_gates_satisfied(project):
            project["logs"].append("[PROJECT STATE] Completion blocked (generation, QA, final audit, and Product Judge are mandatory).")
            return False

    if not force and not _can_transition(project, new_state):
        project["logs"].append(f"[PROJECT STATE] Transition blocked: {current} -> {new_state}")
        return False

    if current != new_state:
        project["status"] = new_state
        suffix = f" ({reason})" if reason else ""
        project["logs"].append(f"[PROJECT STATE] {_state_display_name(new_state)}{suffix}")

    if save:
        _save_projects_state()
    else:
        project_state.persist_project_state(project)
    return True


def _reset_delivery_gates(project: dict):
    project["_generation_finished"] = False
    project["_qa_passed"] = False
    project["_final_audit_passed"] = False
    project["_product_judge_passed"] = False
    project_state.persist_project_state(project)


def _mark_generation_finished(project: dict, finished: bool):
    project["_generation_finished"] = bool(finished)
    if not finished:
        project["_qa_passed"] = False
        project["_final_audit_passed"] = False
        project["_product_judge_passed"] = False
    project_state.persist_project_state(project)


def _mark_qa_passed(project: dict, passed: bool):
    project["_qa_passed"] = bool(passed)
    if not passed:
        project["_final_audit_passed"] = False
        project["_product_judge_passed"] = False
    project_state.persist_project_state(project)


def _mark_final_audit_passed(project: dict, passed: bool):
    project["_final_audit_passed"] = bool(passed)
    if not passed:
        project["_product_judge_passed"] = False
    project_state.persist_project_state(project)


def _mark_product_judge_passed(project: dict, passed: bool):
    project["_product_judge_passed"] = bool(passed)
    project_state.persist_project_state(project)


def _record_requirement_gap_assumptions(project: dict) -> list[dict]:
    spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
    gaps = spec.get("requirement_gaps", []) if isinstance(spec, dict) else []
    assumptions = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        if gap.get("severity") in ("blocker", "critical"):
            continue
        assumptions.append(gap)
    if assumptions:
        project["requirement_assumptions"] = assumptions
        project.setdefault("logs", []).append(f"[REQUIREMENT GAPS] Continuing autonomously with {len(assumptions)} recorded non-critical assumption(s).")
    return assumptions


def _requirement_gap_blocker(project: dict) -> tuple[str | None, list[dict]]:
    spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
    gaps = spec.get("requirement_gaps", []) if isinstance(spec, dict) else []
    if not isinstance(gaps, list):
        return None, []
    missing_credentials = [gap for gap in gaps if isinstance(gap, dict) and gap.get("category") == "missing_credential"]
    if missing_credentials:
        return "needs_credentials", missing_credentials
    critical_ambiguity = [
        gap for gap in gaps
        if isinstance(gap, dict) and gap.get("category") == "ambiguity" and gap.get("severity") in ("critical", "blocker")
    ]
    if critical_ambiguity:
        return "needs_human_input", critical_ambiguity
    return None, []


def _apply_requirement_gap_blockers(project: dict) -> bool:
    _record_requirement_gap_assumptions(project)
    state, gaps = _requirement_gap_blocker(project)
    if not state:
        return False
    project["blocking_requirement_gaps"] = gaps
    if state == "needs_credentials":
        project["needs_credentials"] = True
        project["manual_steps"] = [gap.get("suggested_question", "Provide required external credentials.") for gap in gaps]
    else:
        project["needs_human_input"] = True
        project["manual_steps"] = [gap.get("suggested_question", "Clarify critical requirement ambiguity.") for gap in gaps]
    _mark_generation_finished(project, False)
    _set_project_status(project, state, reason="critical requirement gap")
    project.setdefault("logs", []).append(f"[REQUIREMENT GAPS] Blocking before coding: {len(gaps)} critical gap(s) require {state.replace('_', ' ')}.")
    return True


def _is_cancelled(project: dict) -> bool:
    return bool(project.get("cancel_requested") or project.get("status") == "cancelled")


def _abort_if_cancelled(project: dict, stage_label: str) -> bool:
    if not _is_cancelled(project):
        return False
    _set_project_status(project, "cancelled", force=True, reason=stage_label)
    return True


def _ensure_project_contract(project: dict, target_path: str | None = None):
    if project.get("project_spec") and project.get("acceptance_criteria") and project.get("qa_plan"):
        ensure_acceptance_evidence_history(project)
        project.setdefault("logs", []).append("[PROJECT SPEC] Existing structured specification loaded.")
        project["logs"].append(f"[PROFILE DETECTION] Profiles: {', '.join(project.get('project_profiles', []))}")
        project["logs"].append(f"[ACCEPTANCE CRITERIA] {len(project.get('acceptance_criteria', []))} stored criteria loaded.")
        project["logs"].append(f"[QA PLAN] {len(project.get('qa_plan', {}).get('levels', []))} stored QA levels loaded.")
        return {
            "project_spec": project.get("project_spec"),
            "project_profiles": project.get("project_profiles", []),
            "acceptance_criteria": project.get("acceptance_criteria", []),
            "qa_plan": project.get("qa_plan"),
        }
    bundle = ensure_project_spec_bundle(project, project_path=target_path)
    project.setdefault("logs", []).append(f"[PROJECT SPEC] Structured specification created for: {bundle['project_spec'].get('project_goal', 'project')}")
    project["logs"].append(f"[PROFILE DETECTION] Profiles: {', '.join(bundle.get('project_profiles', []))}")
    project["logs"].append(f"[ACCEPTANCE CRITERIA] {len(bundle.get('acceptance_criteria', []))} criteria generated.")
    project["logs"].append(f"[QA PLAN] {len(bundle.get('qa_plan', {}).get('levels', []))} QA levels planned.")
    _save_projects_state()
    return bundle


def _project_contract_ready(project: dict) -> tuple[bool, list[str]]:
    errors = []
    spec = project.get("project_spec")
    criteria = project.get("acceptance_criteria")
    qa_plan = project.get("qa_plan")
    if not isinstance(spec, dict) or not spec.get("original_user_request"):
        errors.append("Missing structured project specification with original_user_request")
    if not isinstance(criteria, list) or not criteria:
        errors.append("Missing acceptance criteria")
    else:
        for criterion in criteria:
            for field in ("id", "title", "description", "priority", "source", "verification_method", "expected_result", "status", "evidence"):
                if field not in criterion:
                    errors.append(f"Acceptance criterion {criterion.get('id', '?')} missing field: {field}")
                    break
    if not isinstance(qa_plan, dict) or not qa_plan.get("levels"):
        errors.append("Missing project-specific QA plan")
    return not errors, errors


def _parse_product_judge_response(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```json"):
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return {"status": "invalid", "objections": [], "parse_error": "Product Judge did not return structured JSON"}
    if not isinstance(parsed, dict):
        return {"status": "invalid", "objections": [], "parse_error": "Product Judge JSON root must be an object"}
    objections = parsed.get("objections", [])
    if not isinstance(objections, list):
        objections = []
    return {"status": str(parsed.get("status", "unknown")).lower(), "objections": objections}


def _valid_product_judge_objections(project: dict, objections: list[dict]) -> list[dict]:
    requirement_ids = {req.get("id") for req in project.get("project_spec", {}).get("requirements", []) if isinstance(req, dict)}
    criterion_ids = {criterion.get("id") for criterion in project.get("acceptance_criteria", []) if isinstance(criterion, dict)}
    valid = []
    for index, objection in enumerate(objections, start=1):
        if not isinstance(objection, dict):
            continue
        requirement_id = str(objection.get("requirement_id") or "").strip()
        criterion_id = str(objection.get("criterion_id") or "").strip()
        if requirement_id and requirement_ids and requirement_id not in requirement_ids:
            continue
        if criterion_id and criterion_ids and criterion_id not in criterion_ids:
            continue
        if not requirement_id and not criterion_id:
            continue
        title = str(objection.get("title") or objection.get("summary") or "").strip()
        if not title:
            continue
        severity = str(objection.get("severity") or "high").lower()
        if severity not in ("blocker", "critical", "high", "medium", "low"):
            severity = "high"
        normalized = dict(objection)
        normalized.update({"index": index, "requirement_id": requirement_id, "criterion_id": criterion_id, "title": title, "severity": severity})
        valid.append(normalized)
    return valid


def _product_judge_objections_to_issues(project: dict, objections: list[dict]) -> list[dict]:
    issues = project.setdefault("issues", [])
    created = []
    existing_keys = {
        (issue.get("source"), issue.get("requirement_id"), issue.get("criterion_id"), issue.get("title"))
        for issue in issues
        if isinstance(issue, dict)
    }
    for objection in objections:
        key = ("product_judge", objection.get("requirement_id", ""), objection.get("criterion_id", ""), objection.get("title", ""))
        if key in existing_keys:
            continue
        issue = Issue(
            id=f"ISSUE-JUDGE-{len([i for i in issues if isinstance(i, dict) and i.get('source') == 'product_judge']) + 1:03d}",
            source="product_judge",
            severity=objection.get("severity", "high"),
            requirement_id=objection.get("requirement_id", ""),
            criterion_id=objection.get("criterion_id", ""),
            title=objection.get("title", "Product Judge objection"),
            evidence={"product_judge_objection": objection, "requires_verifier_evidence": True},
            reproduction=list(objection.get("reproduction", [])) if isinstance(objection.get("reproduction"), list) else [],
            owner="codex",
            status="open",
            attempts=0,
            verification_method=str(objection.get("verification_method") or "product_judge_objection"),
        ).to_dict()
        issues.append(issue)
        existing_keys.add(key)
        created.append(issue)
    return created


def _call_product_judge(bundle: dict, provider: str | None = None, model: str | None = None) -> dict:
    prompt = (
        "You are an independent Product Judge. You cannot edit files or suggest hidden fixes. "
        "Try to prove the product is not ready using only the provided evidence bundle. "
        "Do not include chain-of-thought or hidden reasoning. Return ONLY JSON with: "
        "status ('pass' or 'fail') and objections. Each objection must include requirement_id or criterion_id, severity, title, evidence, reproduction, and verification_method.\n\n"
        f"PRODUCT JUDGE INPUT BUNDLE:\n{json.dumps(bundle, ensure_ascii=False, indent=2)}"
    )
    provider = provider or get_agent_provider_model("lupa")[0]
    model = model or get_agent_provider_model("lupa")[1]
    raw = ask_studio_ai_with_history(
        provider=provider,
        model_name=model,
        system_prompt="Independent Product Judge. Read-only. Return structured JSON only; no hidden reasoning.",
        chat_history=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=2048,
    )
    return _parse_product_judge_response(raw)


def _run_product_judge_stage(project: dict, qa_result: dict | None = None) -> tuple[bool, list[str]]:
    results = project.get("product_judge_results", {})
    results = results if isinstance(results, dict) else {}
    subjective = [
        criterion for criterion in project.get("acceptance_criteria", [])
        if delivery_audit_module._semantic_verifier_plan(criterion, project, project.get("target_path", "")).get("verifier_type") == "product_ui_quality"
    ]
    verdicts = {criterion.get("id", ""): (results.get(criterion.get("id", ""), {}) or {}).get("verdict", "insufficient_evidence") for criterion in subjective}
    report = {
        "status": "passed" if all(verdict in ("approved", "approved_with_nonblocking_notes") for verdict in verdicts.values()) else "not_verified",
        "criteria": verdicts,
        "read_only": True,
    }
    project["product_judge_report"] = report
    project.setdefault("logs", []).append(f"Product Judge evidence checked for {len(subjective)} subjective criterion/criteria.")
    qa_ok = bool((qa_result or {}).get("success"))
    audit_ok = project.get("final_delivery_report", {}).get("status") == "passed"
    if not qa_ok or not audit_ok:
        return False, ["Product Judge pass requires existing QA and final audit evidence"]
    failed = [f"{criterion_id}: {verdict}" for criterion_id, verdict in verdicts.items() if verdict not in ("approved", "approved_with_nonblocking_notes")]
    if failed:
        return False, failed
    return True, []


def _should_execute_phase(project: dict, phase_name: str) -> bool:
    """Return True if `phase_name` should run (not skip due to resume)."""
    saved = project.get("_phase")
    if not saved or saved not in _PHASE_ORDER:
        return True
    try:
        return _PHASE_ORDER.index(phase_name) >= _PHASE_ORDER.index(saved)
    except ValueError:
        return True


def _save_phase(project: dict, phase_name: str):
    """Persist current phase to project state."""
    project["_phase"] = phase_name
    _save_projects_state()


DEFAULT_AGENTS = {
    "alex": {"name": "Alex", "role": "project_manager", "emoji": "👔", "color": "#6366f1", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "maya": {"name": "Maya", "role": "analyst", "emoji": "🎯", "color": "#a855f7", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "elena": {"name": "Elena", "role": "designer", "emoji": "🎨", "color": "#ec4899", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "codex": {"name": "Codex", "role": "developer", "emoji": "💻", "color": "#0ea5e9", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "bugcatcher": {"name": "BugCatcher", "role": "tester", "emoji": "🐛", "color": "#10b981", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "sentinel": {"name": "Sentinel", "role": "security", "emoji": "🛡️", "color": "#ef4444", "enabled": False, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.1},
    "lupa": {"name": "Lupa", "role": "code_reviewer", "emoji": "🔍", "color": "#8b5cf6", "enabled": False, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.1},
    "goldie": {"name": "Goldie", "role": "sales", "emoji": "💰", "color": "#f59e0b", "enabled": True, "builtin": True, "status": "idle", "provider": "nvidia", "model": AGENT_MODELS["nvidia"], "use_global": True, "temperature": 0.2},
    "product_judge": {"name": "Product Judge", "role": "product_judge", "emoji": "⚖", "color": "#f97316", "enabled": True, "builtin": True, "status": "idle", "provider": "", "model": "", "use_global": False, "temperature": 0.3, "top_p": 0.9, "top_k": None, "auto_select_independent": True},
}

_AI_SPEED_HINT = (
    "IMPORTANT: You are an AI assistant, not a human. Projects are completed in minutes, not days or weeks. "
    "When asked about timeline or duration, estimate in minutes to hours maximum (e.g. '5-15 minutes'). "
    "You work at AI speed — fast and efficiently. Never say weeks or months."
)

_AGENT_ROSTER_TEXT = (
    "Authoritative team roster: Alex (project manager/dispatcher), Maya (requirements analyst), "
    "Elena (UI/UX designer), Codex (developer/software architect), BugCatcher (QA engineer), "
    "Sentinel (security auditor), Lupa (code reviewer), Goldie (finance advisor). "
    "Do not invent any other employees, people, names, departments, or specialists. "
    "If the user mentions an unknown person, say that this person is not in the current roster. "
)

_TRUTHFUL_CHAT_GUARD = (
    "Truthfulness rules for chat: rely only on the user message, provided chat history, provided project context, and the authoritative roster. "
    "Do not claim a project exists, is active, or is being worked on unless project context explicitly says so. "
    "If no project context is provided, say that you do not currently have an active project context. "
    "Do not invent team members, task owners, client names, project names, statuses, files, or completed work. "
    "If you made a mistaken claim, acknowledge it briefly and correct it using only known facts. "
)

AGENT_SYSTEM_PROMPTS = {
    "alex": _AI_SPEED_HINT + (
        " You are Alex, an elite senior project manager, dispatcher, and delivery coordinator. "
        "You own the full project flow from planning to delivery: Alex -> Maya -> Elena -> Codex -> BugCatcher -> Sentinel -> Lupa. "
        "Your primary job is to split work into clear, bounded assignments for each specialist, define acceptance criteria, and keep the team synchronized. "
        "For every project, produce a structured execution plan with: scope, assumptions, phase order, owner for each task, dependencies, risks, and definition of done. "
        "When specialists report back, summarize their status, decide whether the project can proceed, and assign the next concrete task. "
        "Require every worker to report to you with: completed work, current status, blockers, risks, files/areas touched, and next recommendation. "
        "Never let work remain vague. If requirements are unclear, assign Maya to clarify. If UI is unclear, assign Elena. If implementation is needed, assign Codex. "
        "If verification is needed, assign BugCatcher. If security risk exists, assign Sentinel. If quality review is needed, assign Lupa. "
        "Speak with authority and professionalism. Use concise structured task breakdowns, status reports, and actionable next steps."
    ),
    "maya": _AI_SPEED_HINT + (
        " You are Maya, an expert business analyst and requirements specialist. "
        "You receive assignments from Alex and report back to Alex. "
        "Your responsibility is requirements discovery, ambiguity removal, scope framing, user stories, acceptance criteria, and business rules. "
        "When analyzing a project, identify goals, users, constraints, out-of-scope items, edge cases, missing information, and measurable success criteria. "
        "Ask precise clarification questions only when they materially change implementation or design. "
        "Your report to Alex must include: requirements summary, assumptions, open questions, acceptance criteria, risks, and recommendation for Elena/Codex/BugCatcher. "
        "Do not design UI in detail or write production code unless Alex explicitly asks. Stay focused on business analysis and requirements quality."
    ),
    "codex": _AI_SPEED_HINT + (
        " You are Codex, a senior software architect and full-stack developer. "
        "You receive implementation assignments from Alex and report back to Alex. "
        "Your responsibility is architecture, implementation, integration, maintainable code, build correctness, and practical technical decisions. "
        "Before coding, restate the task, constraints, file boundaries, dependencies, and likely risks. "
        "When implementing, prefer small correct changes, ESM for JS/React, and concrete runnable code rather than placeholders. "
        "Always consider performance, security, maintainability, error handling, and testability. "
        "Your report to Alex must include: what was built, files/areas touched, assumptions, build/test status, unresolved risks, and what BugCatcher/Sentinel/Lupa should verify next. "
        "Do not expand scope without Alex's approval. If requirements are ambiguous, ask Alex to route back to Maya. If UI direction is missing, ask Alex to route to Elena."
    ),
    "bugcatcher": _AI_SPEED_HINT + (
        " You are BugCatcher, a senior QA engineer and test automation specialist. "
        "You receive verification assignments from Alex and report back to Alex. "
        "Your responsibility is test planning, automated checks, manual verification paths, regression risk, reproducible bug reports, and quality gates. "
        "Review requirements from Maya, design intent from Elena, and implementation from Codex before judging quality. "
        "For every test pass, identify covered paths, untested paths, edge cases, likely regressions, and exact reproduction steps for failures. "
        "Your report to Alex must include: pass/fail summary, tests/checks run, defects found, severity, reproduction steps, recommended owner for fixes, and remaining coverage gaps. "
        "Be meticulous and evidence-based. Do not approve vague or unverified work."
    ),
    "elena": _AI_SPEED_HINT + (
        " You are Elena, a world-class UI/UX designer and creative director. "
        "You receive design assignments from Alex and report back to Alex. "
        "Your responsibility is user experience, information architecture, visual hierarchy, interaction design, responsive behavior, accessibility, and design system consistency. "
        "Translate Maya's requirements into concrete screens, flows, components, states, empty/error/loading states, and visual rules that Codex can implement. "
        "Avoid generic templates; produce a distinct visual direction that fits the product while preserving existing design system constraints. "
        "Your report to Alex must include: UX goals, screen/component plan, layout rules, visual language, accessibility notes, risks, and implementation guidance for Codex. "
        "Do not write backend logic. If requirements are unclear, ask Alex to route back to Maya."
    ),
    "goldie": _AI_SPEED_HINT + (
        " You are Goldie, an elite senior financial analyst and strategic advisor. "
        "You provide expert financial guidance on freelance projects, budgets, pricing, "
        "cost estimation, ROI analysis, and market research. "
        "You can search the web for current financial data, exchange rates, market trends, "
        "and competitor pricing. "
        "Always respond with clear, actionable financial advice. "
        "When asked about a project, analyze its budget, scope, timeline, and provide "
        "concrete recommendations for pricing, cost optimization, and profitability. "
        "Be concise but thorough. Use numbers and percentages where appropriate."
    ),
    "sentinel": _AI_SPEED_HINT + (
        " You are Sentinel, an elite cybersecurity auditor and penetration testing specialist. "
        "You receive security review assignments from Alex and report back to Alex. "
        "Your responsibility is threat modeling, secure configuration review, secrets handling, auth/access-control review, input validation, dependency risk, and OWASP Top 10 coverage. "
        "Analyze code for SQL injection, XSS, CSRF, hardcoded secrets, insecure authentication, path traversal, command injection, insecure deserialization, broken access control, SSRF, and data exposure. "
        "For each finding, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), affected file/area, vulnerable pattern, attack vector, exploit impact, and concrete fix. "
        "Your report to Alex must include: security verdict, findings by severity, must-fix items before release, acceptable residual risks, and recommended owner for fixes. "
        "Be precise and avoid false positives; distinguish confirmed vulnerabilities from hardening recommendations."
    ),
    "lupa": _AI_SPEED_HINT + (
        " You are Lupa, a senior code reviewer and software quality engineer. "
        "You receive final review assignments from Alex and report back to Alex. "
        "Your responsibility is maintainability, readability, architecture quality, cohesion, naming, error handling, performance risks, duplication, documentation, and alignment with project requirements. "
        "Review Codex's implementation after BugCatcher and Sentinel have provided feedback, and identify whether fixes degraded quality or introduced regressions. "
        "For each finding, provide: severity, file/area, current problem, suggested improvement, and why it improves long-term quality. "
        "Your report to Alex must include: overall quality score 1-10, release recommendation, must-fix issues, nice-to-have improvements, and next owner. "
        "Be constructive, specific, and pragmatic; do not request rewrites unless the risk justifies it."
    ),
}

FAST_AGENT_SYSTEM_PROMPTS = {
    "alex": _AI_SPEED_HINT + " " + _AGENT_ROSTER_TEXT + _TRUTHFUL_CHAT_GUARD + " You are Alex, the project dispatcher. Answer briefly, give a direct status or next action, and mention only real roster specialists when relevant.",
    "maya": _AI_SPEED_HINT + " You are Maya, the requirements analyst. Answer briefly with requirements, assumptions, acceptance criteria, or clarifying questions. Report conclusions as if to Alex.",
    "elena": _AI_SPEED_HINT + " You are Elena, the UI/UX designer. Answer briefly with concrete UX, layout, visual, accessibility, or design-system guidance. Report conclusions as if to Alex.",
    "codex": _AI_SPEED_HINT + " You are Codex, the software architect/developer. Answer briefly with practical implementation guidance, code-level advice, risks, and next technical step. Report conclusions as if to Alex.",
    "bugcatcher": _AI_SPEED_HINT + " You are BugCatcher, the QA engineer. Answer briefly with tests, edge cases, reproduction steps, quality risks, and verification advice. Report conclusions as if to Alex.",
    "sentinel": _AI_SPEED_HINT + " You are Sentinel, the security auditor. Answer briefly with security risks, severity, attack vector, and concrete mitigation. Report conclusions as if to Alex.",
    "lupa": _AI_SPEED_HINT + " You are Lupa, the code reviewer. Answer briefly with maintainability, quality, performance, and release-readiness feedback. Report conclusions as if to Alex.",
    "goldie": _AI_SPEED_HINT + " You are Goldie, the finance advisor. Answer briefly with pricing, budget, ROI, and profitability guidance.",
}

def get_agent_prompt(agent_id: str) -> str:
    cfg = agent_configs.get(agent_id, {})
    if cfg.get("custom_prompt"):
        return cfg["custom_prompt"]
    return AGENT_SYSTEM_PROMPTS.get(
        agent_id,
        "You are a professional AI assistant. Be concise and helpful."
    )


def get_fast_agent_prompt(agent_id: str) -> str:
    cfg = agent_configs.get(agent_id, {})
    if cfg.get("custom_prompt"):
        return cfg["custom_prompt"] + "\n\nFast chat mode: answer concisely, skip long plans unless the user asks for detail."
    return FAST_AGENT_SYSTEM_PROMPTS.get(
        agent_id,
        "You are a professional AI assistant. Answer concisely and helpfully."
    )

def get_agent_provider_model(agent_id: str) -> tuple:
    agent = agent_configs.get(agent_id, {})
    default = DEFAULT_AGENTS.get(agent_id, {})
    if agent.get("use_global") is False:
        provider = agent.get("provider", default.get("provider", SYSTEM_SETTINGS["global_provider"]))
        model = agent.get("model", default.get("model", SYSTEM_SETTINGS["global_model"]))
    else:
        provider = SYSTEM_SETTINGS["global_provider"]
        model = SYSTEM_SETTINGS["global_model"]
    return provider, model

DEFAULT_SYSTEM_SETTINGS: Dict[str, Any] = {
    "global_provider": "nvidia",
    "global_model": "meta/llama-3.3-70b-instruct",
    "theme": "dark",
    "accent_color": "#0ea5e9",
    "animation_speed": "normal",
    "font_size": "medium",
    "language": "en",
    "auto_save": True,
    "notifications_enabled": True,
    "default_budget": "500",
    "polling_interval": 3000,
    "log_detail": "normal",
    "vscode_path": "code",
    "pycharm_path": "pycharm",
}

SYSTEM_SETTINGS: Dict[str, Any] = _get_saved_system_settings()


def load_agent_configs() -> Dict[str, Dict[str, Any]]:
    data = load_studio_keys()
    return data.get("_agent_configs", {})


def save_agent_configs(configs: Dict[str, Dict[str, Any]]):
    data = load_studio_keys()
    data["_agent_configs"] = configs
    save_studio_keys(data)


agent_configs = load_agent_configs()


def _configured_product_judge_runtime() -> dict[str, Any]:
    """Resolve a read-only judge identity, preferring an independently configured vision model."""
    cfg = {**DEFAULT_AGENTS["product_judge"], **agent_configs.get("product_judge", {})}
    implementation_provider, implementation_model = get_agent_provider_model("elena")
    provider = ""
    model = ""
    if cfg.get("use_global"):
        provider = SYSTEM_SETTINGS["global_provider"]
        model = SYSTEM_SETTINGS["global_model"]
    elif cfg.get("provider") and cfg.get("model"):
        provider = str(cfg["provider"])
        model = str(cfg["model"])
    elif cfg.get("auto_select_independent", True):
        keys = load_studio_keys()
        candidates = [
            name for name in AI_PROVIDER_MODELS
            if (name == "ollama" or bool(keys.get(_provider_key_name(name)) or keys.get(f"{name}_api_key")))
            and ai_utils.provider_capabilities(name).get("image_input")
        ]
        provider = next((name for name in candidates if name != implementation_provider), "")
        if not provider and implementation_provider in candidates:
            provider = implementation_provider
        if provider:
            models = AI_PROVIDER_MODELS.get(provider, [])
            model = next((item for item in models if provider != implementation_provider or item != implementation_model), models[0] if models else "")
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "provider": provider,
        "model": model,
        "temperature": cfg.get("temperature", 0.3),
        "top_p": cfg.get("top_p", 0.9),
        "top_k": cfg.get("top_k"),
        "use_global": bool(cfg.get("use_global")),
        "implementation_identity": {"agent_id": "elena", "provider": implementation_provider, "model": implementation_model},
    }


delivery_audit_module.configure_product_judge(lambda _project: _configured_product_judge_runtime())

agent_statuses: Dict[str, Dict[str, str]] = {}


@app.get("/api/agents/status")
def get_agent_statuses():
    statuses = {}
    for agent_id, meta in DEFAULT_AGENTS.items():
        st = agent_statuses.get(agent_id, {})
        statuses[agent_id] = {
            "status": st.get("status", "idle"),
            "task": st.get("task", ""),
            "project_id": st.get("project_id", ""),
        }
    return statuses


def set_agent_status(agent_id: str, status: str, task: str = "", project_id: str = ""):
    agent_statuses[agent_id] = {"status": status, "task": task, "project_id": project_id}


def clear_agent_status(agent_id: str):
    agent_statuses.pop(agent_id, None)


@app.get("/api/agents")
def get_agents():
    result = {}
    for agent_id, agent in DEFAULT_AGENTS.items():
        cfg = agent_configs.get(agent_id, {})
        entry = {**agent, "id": agent_id}
        entry.update(AGENT_STAGE_METADATA.get(agent_id, {}))
        entry["enabled"] = cfg.get("enabled", agent.get("enabled", True))
        entry["top_p"] = cfg.get("top_p", agent.get("top_p"))
        entry["top_k"] = cfg.get("top_k", agent.get("top_k"))
        if cfg.get("custom_prompt"):
            entry["custom_prompt"] = cfg["custom_prompt"]
        if cfg.get("save_path"):
            entry["save_path"] = cfg["save_path"]
        if cfg.get("use_global") is False:
            entry["provider"] = cfg.get("provider", agent["provider"])
            entry["model"] = cfg.get("model", agent["model"])
            entry["temperature"] = cfg.get("temperature", agent["temperature"])
            entry["use_global"] = False
            entry["active_provider"] = entry["provider"]
            entry["active_model"] = entry["model"]
        else:
            entry["use_global"] = True
            entry["active_provider"] = SYSTEM_SETTINGS["global_provider"]
            entry["active_model"] = SYSTEM_SETTINGS["global_model"]
        result[agent_id] = entry
    for cid, cagent in agent_configs.get("_custom_agents", {}).items():
        cfg = agent_configs.get(cid, {})
        entry = {**cagent, "id": cid, "builtin": False}
        entry.setdefault("stage", cagent.get("stage", "custom"))
        entry.setdefault("display_role", cagent.get("role", "Custom Agent"))
        entry["enabled"] = cfg.get("enabled", cagent.get("enabled", True))
        entry["top_p"] = cfg.get("top_p", cagent.get("top_p"))
        entry["top_k"] = cfg.get("top_k", cagent.get("top_k"))
        if cfg.get("custom_prompt"):
            entry["custom_prompt"] = cfg["custom_prompt"]
        if cfg.get("save_path"):
            entry["save_path"] = cfg["save_path"]
        if cfg.get("use_global") is False:
            entry["provider"] = cfg.get("provider", cagent.get("provider", SYSTEM_SETTINGS["global_provider"]))
            entry["model"] = cfg.get("model", cagent.get("model", SYSTEM_SETTINGS["global_model"]))
            entry["temperature"] = cfg.get("temperature", cagent.get("temperature", 0.2))
            entry["use_global"] = False
            entry["active_provider"] = entry["provider"]
            entry["active_model"] = entry["model"]
        else:
            entry["use_global"] = True
            entry["active_provider"] = SYSTEM_SETTINGS["global_provider"]
            entry["active_model"] = SYSTEM_SETTINGS["global_model"]
        result[cid] = entry
    return result


@app.get("/api/pipeline/metadata")
def get_pipeline_metadata():
    return {
        "stage_order": PIPELINE_UI_STAGE_ORDER,
        "stages": {
            stage: {"id": stage, **PIPELINE_STAGE_METADATA.get(stage, {"label": _STATE_DISPLAY.get(stage, stage.replace("_", " ").title())})}
            for stage in PIPELINE_UI_STAGE_ORDER
        },
        "agent_stages": AGENT_STAGE_METADATA,
    }


def _all_agent_ids() -> set:
    return set(DEFAULT_AGENTS.keys()) | set(agent_configs.get("_custom_agents", {}).keys())


def _agent_meta(agent_id: str) -> dict:
    if agent_id in DEFAULT_AGENTS:
        return DEFAULT_AGENTS[agent_id]
    custom = agent_configs.get("_custom_agents", {}).get(agent_id, {})
    return custom


@app.post("/api/agents/{agent_id}/config")
def update_agent_config(agent_id: str, payload: Dict[str, Any]):
    if agent_id not in _all_agent_ids():
        raise HTTPException(status_code=404, detail="Agent not found")
    current = agent_configs.get(agent_id, {})
    allowed_keys = ("provider", "model", "temperature", "top_p", "top_k", "use_global", "enabled", "custom_prompt", "save_path")
    for key in allowed_keys:
        if key in payload:
            current[key] = payload[key]
    agent_configs[agent_id] = current
    save_agent_configs(agent_configs)
    return {"status": "success", "agent_id": agent_id}


class CreateAgentPayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    agent_id: str = ""
    name: str = "Custom Agent"
    role: str = "custom"
    provider: str = "nvidia"
    model: str = "nvidia/nemotron-4-340b-instruct"
    temperature: float = 0.2
    custom_prompt: str = ""
    use_global: bool = True
    save_path: str = ""


@app.post("/api/agents")
def create_agent(payload: CreateAgentPayload):
    cid = payload.agent_id or f"custom_{uuid.uuid4().hex[:8]}"
    if cid in _all_agent_ids():
        raise HTTPException(status_code=400, detail=f"Agent ID '{cid}' already exists")
    custom_agents = agent_configs.get("_custom_agents", {})
    custom_agents[cid] = {
        "name": payload.name,
        "role": payload.role,
        "enabled": True,
        "builtin": False,
        "status": "idle",
        "provider": payload.provider,
        "model": payload.model,
        "temperature": payload.temperature,
        "save_path": payload.save_path,
        "use_global": payload.use_global,
    }
    agent_configs["_custom_agents"] = custom_agents
    if payload.custom_prompt:
        agent_configs[cid] = {"custom_prompt": payload.custom_prompt}
    save_agent_configs(agent_configs)
    return {"status": "created", "agent_id": cid, "name": payload.name}


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    if agent_id in DEFAULT_AGENTS:
        agent_configs[agent_id] = {"enabled": False}
        save_agent_configs(agent_configs)
        return {"status": "disabled", "agent_id": agent_id, "note": "Built-in agents cannot be deleted; disabled instead."}
    custom_agents = agent_configs.get("_custom_agents", {})
    if agent_id not in custom_agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    del custom_agents[agent_id]
    agent_configs["_custom_agents"] = custom_agents
    agent_configs.pop(agent_id, None)
    save_agent_configs(agent_configs)
    return {"status": "deleted", "agent_id": agent_id}


@app.post("/api/agents/{agent_id}/chat")
def agent_chat(agent_id: str, payload: AgentChatPayload):
    if agent_id not in _all_agent_ids():
        raise HTTPException(status_code=404, detail="Agent not found")

    meta = _agent_meta(agent_id)
    enabled = agent_configs.get(agent_id, {}).get("enabled", meta.get("enabled", True))
    if not enabled:
        return {"agent_id": agent_id, "agent_name": meta.get("name", agent_id), "reply": f"{meta.get('name', agent_id)} is currently disabled. Enable them in AI Staff Configuration to chat."}

    safe_history_limit = max(0, min(int(payload.history_limit or 0), 20))
    compact_history = [
        {"role": m.get("role", "user"), "content": (m.get("content", "") or "")[:3000]}
        for m in (payload.chat_history or [])[-safe_history_limit:]
        if m.get("role") in ("user", "assistant")
    ]

    unknown_person_text = (payload.message + "\n" + "\n".join(m.get("content", "") for m in compact_history)).lower()
    if agent_id == "alex" and any(name in unknown_person_text for name in ("дмитр", "dmitry", "dmitriy", "dmitri")):
        has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in payload.message)
        reply = (
            "Дмитрия нет в текущем составе AI FreelancerStudio. Это была ошибочная/галлюцинаторная ссылка. "
            "Реальная команда: Alex, Maya, Elena, Codex, BugCatcher, Sentinel, Lupa и Goldie. "
            "Сейчас у меня нет активного контекста проекта, если Context выключен."
            if has_cyrillic else
            "There is no Dmitry in the current AI FreelancerStudio roster. That was an incorrect/hallucinated reference. "
            "The real team is Alex, Maya, Elena, Codex, BugCatcher, Sentinel, Lupa, and Goldie. "
            "I do not have active project context while Context is off."
        )
        return {"agent_id": agent_id, "agent_name": meta.get("name", agent_id), "reply": reply, "fast_mode": payload.fast_mode, "context_used": False}

    if agent_id == "goldie":
        provider, model = get_agent_provider_model("goldie")
        result = goldie_agent.chat_with_goldie(
            message=payload.message,
            chat_history=compact_history,
            provider=provider,
            model_name=model,
        )
        return {"agent_id": "goldie", "agent_name": "Goldie", "reply": result.get("reply", "")}

    # Build project context if project_id is provided
    project_context = ""
    if payload.use_project_context and payload.project_id and payload.project_id in active_projects:
        proj = active_projects[payload.project_id]
        parts = []
        parts.append(f"Current Project: {proj.get('title', 'Untitled')}")
        parts.append(f"Status: {proj.get('status', 'unknown')}")
        parts.append(f"Description: {proj.get('description', 'N/A')}")
        if proj.get("plans"):
            plans = proj["plans"]
            if isinstance(plans, str):
                parts.append(f"\nSprint Plan:\n{plans[:2000]}")
            elif isinstance(plans, dict):
                parts.append(f"\nSprint Plan: {json.dumps(plans, ensure_ascii=False)[:2000]}")
        if proj.get("design_system"):
            ds = proj["design_system"]
            if isinstance(ds, str):
                parts.append(f"\nDesign System:\n{ds[:2000]}")
        if proj.get("tdd_tests"):
            parts.append(f"\nTDD Test Specs: {len(proj['tdd_tests'])} test files defined")
        if proj.get("target_path"):
            parts.append(f"\nProject Path: {proj['target_path']}")
        pipeline_iter = proj.get("pipeline_iteration", 0)
        if pipeline_iter > 0:
            parts.append(f"\nReview Iteration: {pipeline_iter}/3")
            for rev_key in ("bugcatcher_review", "sentinel_review", "lupa_review"):
                for v in range(1, 4):
                    key = f"{rev_key}_v{v}"
                    if proj.get(key):
                        label = rev_key.replace("_", " ").title()
                        parts.append(f"\n{label} v{v}:\n{proj[key][:500]}")
        log_preview = "\n".join(proj.get("logs", [])[-10:])
        if log_preview:
            parts.append(f"\nRecent Pipeline Logs:\n{log_preview}")
        project_context = "\n\n".join(parts)

    prompt = get_fast_agent_prompt(agent_id) if payload.fast_mode else get_agent_prompt(agent_id)
    prompt += "\n\n" + _AGENT_ROSTER_TEXT + _TRUTHFUL_CHAT_GUARD
    if not payload.use_project_context or not payload.project_id:
        prompt += "\nNo active project context was provided for this chat request. Do not claim anyone is working on a specific project."
    if project_context:
        prompt += "\n\n=== CURRENT PROJECT CONTEXT ===\n" + project_context
        prompt += "\n\nYou are working with a team of AI specialists on this project. Reference the work of other team members (Alex's plans, Elena's designs, Codex's code) when relevant. Answer the user's question based on the actual project state."

    provider = payload.provider or get_agent_provider_model(agent_id)[0]
    model = payload.model_name or get_agent_provider_model(agent_id)[1]
    agent_data = agent_configs.get(agent_id, {})
    temperature = min(agent_data.get("temperature", meta.get("temperature", 0.2)), 0.2) if payload.fast_mode else agent_data.get("temperature", meta.get("temperature", 0.2))
    response = ask_studio_ai_with_history(
        provider=provider,
        model_name=model,
        system_prompt=prompt,
        chat_history=compact_history + [{"role": "user", "content": payload.message}],
        temperature=temperature,
        max_tokens=768 if payload.fast_mode else 2048,
    )
    return {"agent_id": agent_id, "agent_name": meta.get("name", agent_id), "reply": response, "fast_mode": payload.fast_mode, "context_used": bool(project_context)}


@app.get("/api/jobs/search")
def search_jobs(q: str = Query(default=""), search_query: str = Query(default=None)):
    query = q or search_query or ""
    results = search_freelance_jobs(query)
    return {"results": results}


@app.get("/api/platforms/suggest")
def suggest_platforms(q: str = Query(default=""), search_query: str = Query(default=None)):
    query = (q or search_query or "").lower()
    if query:
        filtered = [p for p in AVAILABLE_PLATFORMS if query in p["code"].lower() or query in p["name"].lower()]
    else:
        filtered = AVAILABLE_PLATFORMS
    return filtered


@app.get("/api/projects/active/current")
def get_active_projects():
    all_projects = list(active_projects.values())
    return {
        "projects": all_projects,
        "project": all_projects[0] if len(all_projects) == 1 else None,
    }


@app.websocket("/ws/projects/{project_id}/logs")
async def project_logs_ws(websocket: WebSocket, project_id: str):
    await websocket.accept()
    last_count = 0
    sent_snapshot = False
    try:
        while True:
            project = active_projects.get(project_id)
            if not project:
                await websocket.send_json({"type": "error", "message": "Project not found"})
                break
            logs = project.get("logs", [])
            if not sent_snapshot:
                await websocket.send_json({"type": "snapshot", "logs": logs[-500:], "status": project.get("status", "unknown")})
                sent_snapshot = True
            elif len(logs) > last_count:
                for entry in logs[last_count:]:
                    await websocket.send_json({"type": "log", "message": entry, "status": project.get("status", "unknown")})
            last_count = len(logs)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


@app.get("/api/projects/{project_id}/detail")
def get_project_detail(project_id: str):
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@app.get("/api/projects/{project_id}/plan")
def get_project_plan(project_id: str):
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "plans": project.get("plans", ""),
        "design_system": project.get("design_system", ""),
        "target_path": project.get("target_path", ""),
        "status": project.get("status", "unknown"),
    }


@app.post("/api/projects/manual")
def create_manual_project(payload: ManualProjectPayload):
    project_id = str(uuid.uuid4())
    title = payload.title or payload.jobTitle or "Untitled Project"
    desc = payload.initial_description or payload.description or ""
    project = {
        "project_id": project_id,
        "id": project_id,
        "platform": payload.platform if payload.platform != "manual" else "manual",
        "title": title,
        "jobTitle": title,
        "description": desc,
        "budget": payload.budget or "?",
        "status": "created",
        "chat_history": [],
        "logs": [],
    }
    _reset_delivery_gates(project)
    active_projects[project_id] = project
    _save_projects_state()
    return project


@app.post("/api/projects/{project_id}/chat")
def project_chat(project_id: str, payload: ChatPayload):
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    prompt = get_agent_prompt("maya")
    provider, model = get_agent_provider_model("maya")
    history = list(payload.chat_history)
    if payload.message.strip():
        history.append({"role": "user", "content": payload.message.strip()})
    response = ask_studio_ai_with_history(
        provider=provider,
        model_name=model,
        system_prompt=prompt,
        chat_history=history,
        temperature=0.2,
    )
    updated_history = history + [{"role": "assistant", "content": response}]
    project["chat_history"] = updated_history
    return {"status": "success", "response": response, "chat_history": updated_history, "project_id": project_id}


# ── Agent Questions (user-interactive clarifications) ──────────────────────

class AnswerPayload(BaseModel):
    question_id: str
    answer: str


class AgentQuestionPayload(BaseModel):
    question: str
    agent: str = "opencode"


@app.post("/api/projects/{project_id}/agent-question")
def ask_agent_question(project_id: str, payload: AgentQuestionPayload):
    """MCP endpoint: allow external agents (e.g. OpenCode) to ask the human a question."""
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    q_id = _agent_ask_question(project_id, payload.agent, payload.question)
    if not q_id:
        raise HTTPException(400, "Failed to create question")
    # Wait up to 5 minutes for an answer
    import time as _t
    for _ in range(300):
        _t.sleep(1)
        for q in project.get("questions", []):
            if q["id"] == q_id and q["answer"] is not None:
                return {"question_id": q_id, "answer": q["answer"], "answered": True}
    return {"question_id": q_id, "answer": None, "answered": False, "timeout": True}


def _agent_ask_question(project_id: str, agent_name: str, question_text: str, options: list = None):
    """Store a question from an agent, pause the pipeline for user input."""
    project = active_projects.get(project_id)
    if not project:
        return None
    q_id = str(uuid.uuid4())[:8]
    project.setdefault("questions", [])
    # Don't add duplicate questions
    for q in project["questions"]:
        if q["question"] == question_text and q["answer"] is None:
            return q["id"]
    project["questions"].append({
        "id": q_id,
        "agent": agent_name,
        "question": question_text,
        "options": options or [],
        "answer": None,
    })
    project["awaiting_input"] = True
    _set_project_status(project, "awaiting_input")
    project["logs"].append(f"[{agent_name}]: задаёт вопрос — {question_text[:80]}...")
    _save_projects_state()
    return q_id


def _check_questions_answered(project_id: str) -> bool:
    """Check if all pending questions have answers. If yes, clear awaiting flag."""
    project = active_projects.get(project_id)
    if not project:
        return True
    pending = [q for q in project.get("questions", []) if q["answer"] is None]
    if not pending:
        project["awaiting_input"] = False
        project["_clarifications_done"] = True
        if project.get("status") == "awaiting_input":
            _set_project_status(project, project.get("_resume_status", "planning"))
        _save_projects_state()
        return True
    return False


@app.get("/api/projects/{project_id}/questions")
def get_project_questions(project_id: str):
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "awaiting_input": project.get("awaiting_input", False),
        "questions": [q for q in project.get("questions", []) if q["answer"] is None],
    }


@app.post("/api/projects/{project_id}/answer")
def answer_project_question(project_id: str, payload: AnswerPayload):
    project = active_projects.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    for q in project.get("questions", []):
        if q["id"] == payload.question_id and q["answer"] is None:
            q["answer"] = payload.answer
            project["logs"].append(f"[{q['agent']}]: получен ответ — {payload.answer[:80]}...")
            # Resume pipeline if all answered
            _check_questions_answered(project_id)
            _save_projects_state()
            return {"status": "answered", "question_id": payload.question_id}
    raise HTTPException(400, "Question not found or already answered")


# ── Pipeline Integration: question step before generation ──────────────────

def _pipeline_ask_questions(project_id: str, project: dict, spec_text: str, agent_name: str = "alex"):
    """
    After planning, ask the AI if it needs any clarifications.
    Returns list of {id, question, options} if questions were asked.
    """
    provider, model = get_agent_provider_model(agent_name)
    agent_cfg = agent_configs.get(agent_name, {})
    prompt = (
        f"You are a project planner reviewing a new project spec.\n\n"
        f"PROJECT: {project.get('title','')}\n"
        f"DESCRIPTION: {project.get('description','')}\n\n"
        f"SPEC:\n{spec_text[:3000]}\n\n"
        f"Are there any unclear details that would prevent you from generating a complete, working project?\n"
        f"If you need clarification, output a JSON array ONLY, no other text:\n"
        f'[{{\"question\": "...", "options": ["option1", "option2"]}}]\n'
        f"If everything is clear, output just: []\n"
        f"Example questions: which consoles/generations to include, what 3D models to use, color scheme preferences, "
        f"specific features, data sources, etc.\n"
        f"Each question can have suggested answer options, or leave options empty for free-text."
    )
    try:
        raw = ask_studio_ai_with_history(
            provider=provider, model_name=model,
            system_prompt="You are a clarification agent. Ask precise questions to fill gaps in the spec. Output ONLY JSON.",
            chat_history=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        questions = json.loads(raw) if raw.strip() else []
    except Exception:
        questions = []

    asked = []
    for q_data in questions:
        q_text = q_data.get("question", "").strip()
        if q_text:
            q_id = _agent_ask_question(project_id, agent_name, q_text, q_data.get("options", []))
            if q_id:
                asked.append({"id": q_id, "question": q_text, "options": q_data.get("options", [])})
    return asked


@app.post("/api/projects/{project_id}/approve")
def approve_spec_and_start(project_id: str, background_tasks: BackgroundTasks, payload: ProjectApprovePayload = None):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Active project sequence target not found")
    project = active_projects[project_id]
    if project.get("status") in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot approve project in terminal state: {project.get('status')}")
    project["autonomous_mode"] = True if payload is None else bool(payload.autonomous_mode)
    project["cancel_requested"] = False
    _reset_delivery_gates(project)
    if "logs" not in project:
        project["logs"] = []
    if not _set_project_status(project, "meeting"):
        _save_projects_state()
        raise HTTPException(status_code=400, detail="Invalid project state transition to meeting")
    project["logs"].append("Specification confirmed! Core staff assembling for sync alignment...")
    project["logs"].append(f"[DEBUG] Creating thread...")
    t = _threading.Thread(target=async_studio_production_pipeline, args=(project_id,), daemon=True, name=f"pipeline-{project_id[:8]}")
    t.start()
    project["logs"].append(f"[DEBUG] Thread {t.name} started, alive={t.is_alive()}")
    project["_thread_name"] = t.name
    return {"status": "success", "next_status": "meeting"}


@app.post("/api/projects/{project_id}/cancel")
def cancel_project_generation(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    project["cancel_requested"] = True
    session_id = project.get("opencode_session_id")
    cancel_result = {"status": "not_started", "sessions": []}
    if _HAS_OPENCODE:
        try:
            cancel_result = _get_oc_bridge().cancel_session(session_id)
        except Exception as e:
            cancel_result = {"status": "error", "error": str(e), "sessions": []}
    _set_project_status(project, "cancelled", force=True)
    project.setdefault("logs", []).append("[System]: Stop Generation requested. Active OpenCode task cancellation sent.")
    _save_projects_state()
    return {"status": "cancelled", "project_id": project_id, "opencode": cancel_result}


def _build_agent_context(project, sprint_plan=None, design_system=None, tdd_tests=None):
    """Build a context string from all preceding agent outputs."""
    parts = []
    if sprint_plan:
        parts.append(f"=== SPRINT PLAN (by Alex/PM) ===\n{json.dumps(sprint_plan, indent=2, ensure_ascii=False)}")
    if design_system:
        parts.append(f"=== DESIGN SYSTEM (by Elena/Designer) ===\n{json.dumps(design_system, indent=2, ensure_ascii=False)}")
    if tdd_tests:
        test_list = "\n".join([f"  - {t.get('filename','?')}: {t.get('description','')}" for t in tdd_tests])
        parts.append(f"=== TDD TEST FILES (by BugCatcher/QA) ===\n{test_list}")
    return "\n\n".join(parts)


def _default_sprint_plan(project):
    spec = "\n".join([m.get("content","") for m in project.get("chat_history",[]) if m.get("role") in ("user","assistant")]) or project.get("description","")
    is_fullstack = any(w in spec.lower() for w in ["react","frontend","vue","angular","full-stack","full stack"])
    return {
        "tasks": [
            {"id":"t1","owner":"maya","report_to":"alex","title":"Requirements confirmation","description":"Clarify scope, assumptions, user stories, and acceptance criteria","files":[],"priority":"high","estimate":"15m","acceptance":["No unresolved blocking questions","Acceptance criteria documented"]},
            {"id":"t2","owner":"elena","report_to":"alex","title":"Design system and UX flow","description":"Define user flow, layout, components, states, and visual direction","files":["frontend/src/App.jsx","frontend/src/components/"] if is_fullstack else [],"priority":"high","estimate":"30m","acceptance":["Responsive UX plan documented","Implementation guidance ready for Codex"]},
            {"id":"t3","owner":"codex","report_to":"alex","title":"Implementation","description":"Build project structure, configs, dependencies, backend/frontend code","files":["requirements.txt","package.json","backend/","frontend/"] if is_fullstack else ["requirements.txt","backend/"],"priority":"high","estimate":"1h","acceptance":["Project runs locally","No placeholder implementation"]},
            {"id":"t4","owner":"bugcatcher","report_to":"alex","title":"QA verification","description":"Create and run test strategy, validate behavior, report defects","files":["tests/","pytest.ini"],"priority":"high","estimate":"30m","acceptance":["Critical paths verified","Defects reported with reproduction steps"]},
            {"id":"t5","owner":"sentinel","report_to":"alex","title":"Security review","description":"Audit auth, inputs, secrets, dependencies, and OWASP risks","files":["backend/","frontend/",".env.example"],"priority":"medium","estimate":"20m","acceptance":["Security verdict reported","Must-fix issues identified"]},
            {"id":"t6","owner":"lupa","report_to":"alex","title":"Code quality review","description":"Review maintainability, architecture, naming, performance, and release readiness","files":["backend/","frontend/","tests/"],"priority":"medium","estimate":"20m","acceptance":["Quality score reported","Release recommendation provided"]},
        ],
        "milestones": [
            {"name":"Analysis","tasks":["t1"]},{"name":"Design","tasks":["t2"]},{"name":"Build","tasks":["t3"]},{"name":"Verify","tasks":["t4","t5","t6"]},
        ],
        "team_handoff": [
            {"from":"alex","to":"maya","purpose":"Clarify scope and acceptance criteria"},
            {"from":"maya","to":"alex","purpose":"Report requirements status and risks"},
            {"from":"alex","to":"elena","purpose":"Assign UX/design based on confirmed requirements"},
            {"from":"elena","to":"alex","purpose":"Report design decisions and implementation guidance"},
            {"from":"alex","to":"codex","purpose":"Assign implementation with requirements and design context"},
            {"from":"codex","to":"alex","purpose":"Report files built, tests run, blockers, and risks"},
            {"from":"alex","to":"bugcatcher","purpose":"Assign verification and defect reporting"},
            {"from":"alex","to":"sentinel","purpose":"Assign security audit when enabled"},
            {"from":"alex","to":"lupa","purpose":"Assign final quality review when enabled"},
        ],
        "tech_stack": {
            "backend":"FastAPI / Python 3.12","frontend":"React + Vite" if is_fullstack else "FastAPI / Python 3.12","database":"SQLite","key_libraries":["fastapi","uvicorn","pydantic","sqlalchemy","python-dotenv","psycopg2-binary"],
        },
        "architecture":"Standard modular monolith with FastAPI backend and React frontend in Docker." if is_fullstack else "Standard modular monolith with FastAPI backend in Docker.",
        "file_tree":["backend/main.py","backend/models.py","backend/database.py","backend/routes/__init__.py","frontend/src/App.jsx","frontend/src/index.js","frontend/vite.config.js","Dockerfile","docker-compose.yml","requirements.txt","tests/test_api.py"] if is_fullstack else ["backend/main.py","backend/models.py","backend/database.py","backend/routes/__init__.py","backend/api.py","Dockerfile","docker-compose.yml","requirements.txt","tests/test_api.py"],
    }


def _default_design_system():
    return {
        "colors":{"primary":"#2563eb","secondary":"#64748b","accent":"#f59e0b","background":"#0f172a","surface":"#1e293b","text":"#f8fafc","muted":"#94a3b8"},
        "typography":{"font_family":"Inter, system-ui, sans-serif","headings":"font-bold tracking-tight","body":"font-normal leading-relaxed"},
        "spacing":"4px base (tailwind scale)","component_hierarchy":[
            {"name":"Layout","props":["children"],"children":["Header","Main","Footer"],"description":"Page shell"},
            {"name":"Header","props":["title","actions"],"children":["Logo","Nav","UserMenu"],"description":"Top bar"},
            {"name":"Card","props":["title","children","actions"],"children":[],"description":"Content container"},
            {"name":"Button","props":["label","variant","onClick","disabled"],"children":[],"description":"Action trigger"},
            {"name":"Modal","props":["open","onClose","title","children"],"children":[],"description":"Overlay dialog"},
        ],
        "layout":"Single column with centered content, max-w-4xl container, responsive sidebar on desktop.",
        "design_notes":"Slate-based dark theme (default bg: #0f172a), rounded corners (8px), subtle borders (#1e293b)."
    }


def _pipeline_alex_sprint_plan(project):
    project["logs"].append("[DEBUG] _pipeline_alex_sprint_plan: entering function")
    prompt = (
        "You are Alex, a senior project manager and software architect. "
        "Given the project specification below, create a detailed sprint plan with task breakdown, "
        "milestones, tech stack decisions, and file tree.\n\n"
        f"{_CRITICAL_RULES}\n"
        "IMPORTANT: Respond ONLY with a valid JSON object (no markdown, no code fences). "
        'The JSON must use this exact structure:\n'
        '{\n'
        '  "tasks": [{"id":"task-1","owner":"alex|maya|elena|codex|bugcatcher|sentinel|lupa","report_to":"alex","title":"...","description":"...","files":["file1","file2"],"priority":"high|medium|low","estimate":"...","acceptance":["..."]}],\n'
        '  "milestones": [{"name":"...","tasks":["task-1",...]}],\n'
        '  "team_handoff": [{"from":"alex","to":"maya","purpose":"..."}],\n'
        '  "tech_stack": {"backend":"...","frontend":"...","database":"...","key_libraries":[...]},\n'
        '  "architecture":"...",\n'
        '  "file_tree":["backend/main.py",...]\n'
        '}\n\n'
        "Alex must personally assign tasks in production order: Maya (requirements), Elena (design), Codex (implementation), "
        "BugCatcher (QA), Sentinel (security if applicable), Lupa (quality review if applicable). "
        "Every non-Alex task must report_to Alex and include acceptance criteria. "
        "Include team_handoff entries showing how each worker reports status back to Alex.\n\n"
        f"Project title: {project.get('title','')}\n"
        f"Description: {project.get('description','')}\n"
    )
    chat = [m for m in project.get("chat_history",[]) if m.get("role") in ("user","assistant")]
    spec_text = "\n".join([m.get("content","") for m in chat[-4:]])
    prompt += f"\nSpec excerpts:\n{spec_text}\n"
    project["logs"].append("[DEBUG] Calling ask_studio_ai_with_history...")
    provider, model = get_agent_provider_model("alex")
    agent_data = agent_configs.get("alex", {})
    try:
        resp = ask_studio_ai_with_history(provider=provider, model_name=model, system_prompt=prompt, chat_history=[], temperature=agent_data.get("temperature",0.2))
        project["logs"].append(f"[DEBUG] AI returned: {resp[:80] if resp else 'EMPTY'}")
        if resp:
            cleaned = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            plan = json.loads(cleaned)
            if "tasks" in plan:
                project["logs"].append(f"Alex: Sprint plan generated — {len(plan['tasks'])} tasks, {len(plan.get('milestones',[]))} milestones")
                return plan
    except Exception as e:
        project["logs"].append(f"Alex sprint plan parse error: {e}")
    project["logs"].append("Alex: Using default sprint plan (fallback)")
    return _default_sprint_plan(project)


def _pipeline_elena_design(project, sprint_plan):
    context = _build_agent_context(project, sprint_plan=sprint_plan)
    prompt = (
        "You are Elena, a senior UI/UX designer and frontend architect. "
        "Given the project spec and sprint plan below, create a complete design system including colors, "
        "typography, spacing, component hierarchy, and layout wireframe.\n\n"
        f"{_CRITICAL_RULES}\n"
        "CRITICAL: All frontend code MUST use Tailwind CSS utility classes — "
        "design notes must specify exact Tailwind classes for each component. "
        "NEVER suggest plain CSS or separate CSS files.\n\n"
        "IMPORTANT: Respond ONLY with a valid JSON object (no markdown, no code fences). "
        'Use this structure:\n'
        '{\n'
        '  "colors": {"primary":"#...","secondary":"#...","accent":"#...","background":"#...","surface":"#...","text":"#...","muted":"#..."},\n'
        '  "typography": {"font_family":"...","headings":"...","body":"..."},\n'
        '  "spacing":"...",\n'
        '  "component_hierarchy": [{"name":"...","props":[...],"children":[...],"description":"...","tailwind_classes":"..."}],\n'
        '  "layout":"...",\n'
        '  "design_notes":"..."\n'
        '}\n\n'
        f"Context:\n{context}\n"
    )
    provider, model = get_agent_provider_model("elena")
    agent_data = agent_configs.get("elena", {})
    try:
        resp = ask_studio_ai_with_history(provider=provider, model_name=model, system_prompt=prompt, chat_history=[], temperature=agent_data.get("temperature",0.3))
        if resp:
            cleaned = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            ds = json.loads(cleaned)
            if "colors" in ds:
                project["logs"].append(f"Elena: Design system created — {len(ds.get('component_hierarchy',[]))} components defined")
                return ds
    except Exception as e:
        project["logs"].append(f"Elena design parse error: {e}")
    project["logs"].append("Elena: Using default design system (fallback)")
    return _default_design_system()


def _pipeline_bugcatcher_tdd(project, sprint_plan, design_system):
    context = _build_agent_context(project, sprint_plan=sprint_plan, design_system=design_system)
    prompt = (
        "You are BugCatcher, a senior QA engineer who writes tests BEFORE code (TDD). "
        "Given the project spec, sprint plan, and design system, create pytest test files that define "
        "the expected behavior of the application.\n\n"
        f"{_CRITICAL_RULES}\n"
        "CRITICAL RULES FOR TESTS:\n"
        "- ALL test files must be COMPLETE and RUNNABLE — no placeholder tests, no pass statements\n"
        "- Use ONLY pytest (not unittest) — each test file starts with 'import pytest'\n"
        "- Every test function must have a realistic assertion (assert x == y, not assert True)\n"
        "- Use monkeypatch or mocks for external dependencies — no real API calls in tests\n"
        "- Test file names must start with 'test_' and be importable\n"
        "- Include conftest.py with all shared fixtures\n"
        "- Test model assertions MUST use exact field names/types from actual models — verify before writing\n"
        "- Tests MUST only test endpoints that actually exist in the API code\n\n"
        "IMPORTANT: Respond ONLY with a valid JSON object (no markdown, no code fences). "
        'Use this structure:\n'
        '{\n'
        '  "test_files": [\n'
        '    {"filename":"tests/test_main.py","content":"import pytest\\n...","description":"Tests main endpoints"}\n'
        '  ],\n'
        '  "testing_strategy":"...",\n'
        '  "fixtures_needed":[...]\n'
        '}\n\n'
        "Each test file must contain COMPLETE, runnable pytest code with proper imports, fixtures, and assertions. "
        "Write tests that would validate the key functionality described in the spec.\n\n"
        f"Context:\n{context}\n"
    )
    provider, model = get_agent_provider_model("bugcatcher")
    agent_data = agent_configs.get("bugcatcher", {})
    try:
        resp = ask_studio_ai_with_history(provider=provider, model_name=model, system_prompt=prompt, chat_history=[], temperature=agent_data.get("temperature",0.1))
        if resp:
            cleaned = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            tests = json.loads(cleaned)
            test_files = tests.get("test_files",[])
            if test_files:
                project["logs"].append(f"BugCatcher (TDD): {len(test_files)} test files generated before coding")
                return test_files
    except Exception as e:
        project["logs"].append(f"BugCatcher TDD parse error: {e}")
    project["logs"].append("BugCatcher (TDD): No tests generated, Codex will create without test specs")
    return []


def _pipeline_codex_generate(project, sprint_plan, design_system, tdd_tests):
    context = _build_agent_context(project, sprint_plan=sprint_plan, design_system=design_system, tdd_tests=tdd_tests)
    chat = project.get("chat_history",[])
    spec = "\n".join([m.get("content","") for m in chat if m.get("role") in ("user","assistant")]) or project.get("description","")
    enhanced_spec = f"Project: {project.get('title','')}\nDescription: {project.get('description','')}\n\nSpec:\n{spec}\n\n{context}"

    provider, model = get_agent_provider_model("codex")
    agent_data = agent_configs.get("codex", {})
    project_id = project.get("project_id", "")

    def on_progress(task):
        set_agent_status("codex", "working", task, project_id)

    return ai_developer.run_ai_development_cycle(
        project_title=project["title"],
        chat_history=[{"role":"user","content":enhanced_spec}],
        provider=provider,
        model_name=model,
        temperature=agent_data.get("temperature", 0.2),
        progress_callback=on_progress,
    )


def _read_project_files(target_path, generated_data, max_files=10, max_chars=3000):
    files_content = {}
    for fname in list(generated_data.get("files", {}).keys())[:max_files]:
        fpath = os.path.join(target_path, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                files_content[fname] = f.read()[:max_chars]
    return files_content


def _run_review_agent(agent_id, role_label, project, target_path, generated_data, iteration, feedback_context=""):
    project_id = project.get("project_id", "")
    set_agent_status(agent_id, "working", f"Reviewing (iteration {iteration})", project_id)
    project["logs"].append(f"{role_label} reviewing code (iteration {iteration})...")

    files_content = _read_project_files(target_path, generated_data)
    code_dump = "\n\n".join([f"=== {k} ===\n{v}" for k, v in files_content.items()])

    system_prompt = (
        f"You are {role_label}. You are part of an automated review pipeline."
        f" Review the following code carefully."
        f" If you find ANY issues — bugs, vulnerabilities, style problems, design flaws, missing imports, "
        f" incomplete implementations, placeholder code, stub functions, or anything that looks unfinished — "
        f" describe them in detail with file names and suggested fixes."
        f" Be CRITICAL and THOROUGH. Look for: missing error handling, hardcoded values, security issues,"
        f" poor naming, lack of type hints, missing docstrings, TODO comments, pass statements, "
        f" and any clearly broken or incomplete code."
        f" ALSO CHECK ALL OF THE FOLLOWING:"
        f" (1) mixed module systems (ESM import/export mixed with CommonJS require/module.exports),"
        f" (2) missing Tailwind CSS setup (tailwind.config.js, postcss.config.js, @tailwind directives),"
        f" (3) incorrect or non-runnable test files with assertions that don't match actual model fields,"
        f" (4) tests that test endpoints that don't exist in the API code,"
        f" (5) hardcoded configuration values that should use env vars with defaults,"
        f" (6) os.getenv() calls without default values,"
        f" (7) missing dependencies in requirements.txt (python-dotenv, database driver like psycopg2-binary/aiosqlite, pytest, pytest-cov, httpx),"
        f" (8) ESM config files (tailwind.config.js, postcss.config.js) without '\"type\": \"module\"' in package.json,"
        f" (9) import names that don't match the exports of their dependency files,"
        f" (10) async/sync mismatch (sync SQLAlchemy engine used with async queries),"
        f" (11) frontend mentioned in project name/spec but no frontend files generated,"
        f" (12) no frontend directory at all when project has React/Vue/dashboard keywords,"
        f" (13) Jinja2 templates using url_for() instead of direct /static/ paths,"
        f" (14) fitz.open() used with filename instead of fitz.open(stream=..., filetype='pdf'),"
        f" (15) numpy, pandas, scipy, matplotlib, sklearn in requirements.txt (almost never actually needed),"
        f" (16) python-dotenv imported as 'dotenv' instead of 'python-dotenv' in requirements.txt,"
        f" (17) Optional[X] mixed imports (importing from multiple sources instead of only from typing),"
        f" (18) container names in docker-compose.yml starting with digits."
        f" If the code is clean and meets all quality standards, say 'No issues found.'\n\n"
        f" CRITICAL: End your response with exactly one of these lines (nothing after):\n"
        f" ##VERDICT: PASS##\n"
        f" ##VERDICT: FAIL##\n"
        f" Choose PASS only if the code is completely production-ready with zero issues."
        f" If there is even one minor issue, choose FAIL."
    )

    if feedback_context:
        system_prompt += (
            f"\n\nPrevious review feedback that Codex was asked to fix:\n{feedback_context}"
            f"\n\nCheck if the fixes were properly applied."
        )

    prompt = f"PROJECT: {project['title']}\n\nCODE:\n{code_dump}"

    agent_cfg = agent_configs.get(agent_id, {})
    prov, mod = get_agent_provider_model(agent_id)
    try:
        result = ask_studio_ai_with_history(
            provider=prov, model_name=mod,
            system_prompt=system_prompt,
            chat_history=[{"role": "user", "content": prompt}],
            temperature=agent_cfg.get("temperature", 0.1),
        )
        project[f"{agent_id}_review_v{iteration}"] = result
        project["logs"].append(f"{role_label}: review complete — {len(result)} chars")
    except Exception as e:
        result = f"Review error: {e}"
        project["logs"].append(f"{role_label}: review error — {e}")

    has_issues = "##VERDICT: FAIL##" in result
    clear_agent_status(agent_id)
    return {"result": result, "has_issues": has_issues}


def _fix_test_endpoints(target_path, log, project_type="simple"):
    """Fix test file endpoint paths to match actual API routes in the generated code."""
    # 1. Scan all .py files for actual route definitions
    actual_routes = set()  # ("GET", "/api/tasks")
    route_methods = {}
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if not fname.endswith(".py") or fname.startswith("test_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            for m in re.finditer(r'@(?:app|router)\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']', content):
                method = m.group(1).upper()
                path = m.group(2)
                actual_routes.add(path)
                route_methods[path] = method

    if not actual_routes:
        return

    # 2. Find the most representative base path (shortest common prefix)
    base_paths = sorted(actual_routes, key=len)
    real_base = base_paths[0] if base_paths else ""
    # Extract the prefix like /api/tasks from the first route
    parts = real_base.strip("/").split("/")
    base_prefix = "/" + "/".join(parts[:2]) if len(parts) >= 2 else "/" + parts[0] if parts else ""

    # 3. Scan test files and fix wrong endpoints
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if not fname.startswith("test_") or not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except Exception:
                continue
            original = raw
            # Fix common wrong endpoint patterns in test assertions
            # e.g. client.get("/items/1") -> client.get("/api/tasks/1")
            for route in sorted(actual_routes, key=len, reverse=True):
                # Replace specific routes first
                pass
            # Generic: replace /items with real base prefix
            # Test files often use /items, /item, /tasks — map to actual routes
            for wrong_path in ["/items/", "/item/", "/tasks/"]:
                if wrong_path in raw and wrong_path not in " ".join(actual_routes):
                    # Find what the code actually uses
                    for real_route in sorted(actual_routes, key=len):
                        if wrong_path.strip("/").split("/")[0] in real_route or real_route.strip("/").split("/")[0] in wrong_path:
                            raw = raw.replace(wrong_path, real_route.rsplit("/", 1)[0] + "/")
                            break
            if raw != original:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(raw)
                    log(f"[PostProcess]: Fixed test endpoints in {fname}")
                except Exception:
                    pass


def _post_process_code(target_path, log_func=None, project_type="simple"):
    """Auto-fix syntax errors in generated code without calling AI."""
    if not os.path.isdir(target_path):
        return
    def log(msg):
        if log_func: log_func(msg)
    import ast, re as _re

    # Ensure required directories exist (skip templates/static for React/fullstack/API projects)
    for d in ["templates", "static"]:
        if project_type in ("react", "fullstack", "api_db", "api_auth") and d in ("templates", "static"):
            continue
        dp = os.path.join(target_path, d)
        if not os.path.isdir(dp):
            try:
                os.makedirs(dp, exist_ok=True)
                log(f"[PostProcess]: Created missing directory: {d}/")
            except Exception:
                pass

    # Fix template name in code if referenced file doesn't exist
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if fname.endswith(".py"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        raw = f.read()
                except Exception:
                    continue
                original = raw
                # Find template references like get_template('name.html') or 'name.html'
                for m in _re.finditer(r"get_template\(\s*'([^']+)'\)", raw):
                    tname = m.group(1)
                    tpath = os.path.join(target_path, "templates", tname)
                    if not os.path.exists(tpath):
                        # Try to find the actual template file
                        actual = None
                        if os.path.isdir(os.path.join(target_path, "templates")):
                            for tf in os.listdir(os.path.join(target_path, "templates")):
                                if tf.endswith(".html"):
                                    actual = tf
                                    break
                        if actual and actual != tname:
                            raw = raw.replace(f"get_template('{tname}')", f"get_template('{actual}')")
                            log(f"[PostProcess]: Fixed template reference '{tname}' → '{actual}'")
                if raw != original:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(raw)

    # Add __init__.py for package directories (directories with .py files)
    package_dirs = set()
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if fname.endswith(".py") and fname != "__init__.py":
                package_dirs.add(root)
    for d in package_dirs:
        init = os.path.join(d, "__init__.py")
        if not os.path.exists(init):
            try:
                with open(init, "w", encoding="utf-8") as f:
                    f.write("")
                log(f"[PostProcess]: Created missing __init__.py in {os.path.relpath(d, target_path)}")
            except Exception:
                pass

    # Add CORS middleware only for separate frontend/backend projects. Same-origin
    # FastAPI templates/static apps must not get wildcard credentialed CORS.
    has_separate_frontend = any(
        os.path.isfile(os.path.join(root, "package.json"))
        for root, _dirs, files in os.walk(target_path)
        if "package.json" in files
    )
    if project_type in ("react", "fullstack") and has_separate_frontend:
        for root, _dirs, files in os.walk(target_path):
            for fname in files:
                if fname.endswith(".py"):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            raw = f.read()
                    except Exception:
                        continue
                    if "FastAPI" in raw and "CORSMiddleware" not in raw and "add_middleware" not in raw:
                        middleware = "app.add_middleware(\n    CORSMiddleware,\n    allow_origins=[\"*\"],\n    allow_credentials=True,\n    allow_methods=[\"*\"],\n    allow_headers=[\"*\"],\n)"
                        raw, replacements = re.subn(r"(?m)^(app\s*=\s*FastAPI\([^\n]*\))", r"\1\n" + middleware, raw, count=1)
                        if replacements == 0:
                            continue
                        import_line = "from fastapi.middleware.cors import CORSMiddleware\n"
                        lines = raw.splitlines(True)
                        future_indexes = [i for i, line in enumerate(lines) if line.strip().startswith("from __future__ import ")]
                        insert_at = max(future_indexes) + 1 if future_indexes else 0
                        raw = "".join(lines[:insert_at] + [import_line] + lines[insert_at:])
                        try:
                            with open(fpath, "w", encoding="utf-8") as f:
                                f.write(raw)
                            log(f"[PostProcess]: Added CORS middleware to {fname}")
                        except Exception:
                            pass

    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                original = raw

                # Strip BOM, null bytes, leading whitespace garbage
                raw = raw.lstrip("\ufeff\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
                # Remove stray backslash at start (line continuation char in wrong place)
                raw = _re.sub(r'^\\+', '', raw)

                ext = os.path.splitext(fname)[1].lower()

                if ext == ".py":
                    # Try to compile
                    try:
                        compile(raw, fpath, "exec")
                    except SyntaxError as e:
                        # Attempt auto-fix: balance parens/brackets
                        if "unmatched" in str(e) or "was never closed" in str(e) or "invalid syntax" in str(e):
                            lines = raw.split("\n")
                            # Count brackets per line
                            fixed_lines = []
                            for line in lines:
                                opens = line.count("(") + line.count("[") + line.count("{")
                                closes = line.count(")") + line.count("]") + line.count("}")
                                if opens > closes:
                                    line += ")" * (opens - closes)
                                fixed_lines.append(line)
                            raw = "\n".join(fixed_lines)
                        # Re-check
                        try:
                            compile(raw, fpath, "exec")
                        except SyntaxError:
                            raw = original  # revert if still broken

                    # Fix common import errors and runtime issues in .py files
                if ext == ".py":
                    raw = raw.replace("from python_dotenv import", "from dotenv import")
                    raw = raw.replace("import python_dotenv", "import dotenv")
                    raw = raw.replace("python_dotenv.load_dotenv()", "dotenv.load_dotenv()")
                    raw = raw.replace("python_dotenv\\.", "dotenv.")
                    # Fix: from fastapi import FastAPI, CORS — CORS doesn't exist in fastapi
                    raw = re.sub(r'from fastapi import FastAPI, CORS\b', 'from fastapi import FastAPI', raw)
                    raw = re.sub(r'from fastapi import (.*), CORS\b', r'from fastapi import \1', raw)
                    # Remove duplicate CORS calls: CORS(app, ...) when add_middleware(CORSMiddleware, ...) already exists
                    if "add_middleware(CORSMiddleware" in raw:
                        raw = re.sub(r'\n\s*CORS\(app,.*?\)\s*\n', '\n', raw)

                    # Fix common SQLAlchemy async/sync confusion
                    raw = raw.replace("from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine", "from sqlalchemy import create_engine")
                    raw = raw.replace("from sqlalchemy.orm import sessionmaker, declarative_base", "from sqlalchemy.ext.declarative import declarative_base")
                    raw = raw.replace("AsyncSession", "Session")
                    # Fix missing import for Optional types
                    if "Optional[" in raw and "from typing import Optional" not in raw:
                        if "from typing import" in raw:
                            raw = raw.replace("from typing import", "from typing import Optional, ")
                        else:
                            raw = "from typing import Optional\n" + raw
                    # Fix common AI mistakes specific to FastAPI+SQLAlchemy projects
                    if project_type in ("api_db", "api_auth", "fullstack"):
                        # Fix: 'from fastapi import auth' or 'from fastapi import items' — these don't exist in fastapi
                        raw = re.sub(r'from fastapi import (auth|items|tasks|routes)\b', 'from fastapi import APIRouter', raw)
                        raw = re.sub(r'from fastapi import (auth|items|tasks|routes),', 'from fastapi import APIRouter,', raw)
                        # Fix: standalone imports like "from backend.app import routes" when using single-file
                        raw = re.sub(r'from\s+backend\.\w+\.\w+\s+import\s+(routes|items|tasks|auth)', '', raw)
                        # Fix: relative imports that don't work in single-file projects
                        raw = re.sub(r'^from\s+\.\s+import\s+(routes|items|tasks|auth)\s*.*$', '', raw, flags=re.MULTILINE)
                        # Fix: import routes -> remove if file doesn't exist
                        raw = re.sub(r'^\s*import\s+(routes|items|tasks)\s*$', '', raw, flags=re.MULTILINE)
                    # Convert async def to def for sync routes (no await inside)
                    if "async def" in raw and "@app" in raw:
                        # Only convert if there's no await inside the function body
                        # Simple check: if file has @app/async def but no "await", convert all async def to def
                        if "await " not in raw:
                            raw = raw.replace("async def ", "def ")
                    # Fix login endpoint using form params instead of path params
                    if "login" in raw and "OAuth2PasswordRequestForm" not in raw:
                        raw = re.sub(
                            r'def login\((\w+:\s*str\s*,\s*\w+:\s*str)\)',
                            r'def login(\1, form_data: OAuth2PasswordRequestForm = Depends())',
                            raw
                        )
                        raw = re.sub(
                            r'login\((\w+),\s*(\w+)\)',
                            r'login(\1, \2, form_data=form_data)',
                            raw
                        )
                    # Fix get_current_user param type
                    raw = re.sub(
                        r'def get_current_user\((\w+:\s*)\w+\s*=\s*Depends\(security\)\)',
                        r'def get_current_user(\1HTTPAuthorizationCredentials = Depends(security))',
                        raw
                    )
                    raw = raw.replace("uvicorn.run(app, host=HOST, port=PORT, debug=DEBUG)", "uvicorn.run(app, host=HOST, port=PORT)")
                    raw = raw.replace("uvicorn.run(app, host=host, port=port, debug=DEBUG)", "uvicorn.run(app, host=host, port=port)")
                    # Fix fitz.open variants
                    raw = raw.replace('fitz.open("pdf", pdf_file)', "fitz.open(stream=pdf_file)")
                    raw = raw.replace("fitz.open(file.file)", "fitz.open(stream=file.file.read())")
                    raw = raw.replace("fitz.open(file.file.read())", "fitz.open(stream=file.file.read())")
                    raw = raw.replace("fitz.open(stream=file.file.read().read())", "fitz.open(stream=file.file.read())")
                # Fix Jinja2 templates: replace url_for() with direct /static/ paths
                if ext == ".html" and "url_for" in raw:
                    raw = raw.replace("{{ url_for('static', path='", "/static/")
                    raw = raw.replace("{{ url_for('static', filename='", "/static/")
                    raw = raw.replace(") }}", "")
                    raw = raw.replace("}}", "")
                    # Add missing uvicorn.run() if FastAPI app exists but no run call
                    if "FastAPI" in raw and "uvicorn.run" not in raw and "__name__" not in raw:
                        # Extract the variable name after "= FastAPI()" or "FastAPI("
                        m = re.search(r'(\w+)\s*=\s*FastAPI\(', raw)
                        if m:
                            app_var = m.group(1)
                            raw += f"\n\nif __name__ == '__main__':\n    import uvicorn\n    port = int(os.getenv('PORT', 8000))\n    host = os.getenv('HOST', '0.0.0.0')\n    uvicorn.run({app_var}, host=host, port=port)\n"
                            log(f"[PostProcess]: Added missing uvicorn.run() block")

                if ext == ".js" or ext == ".jsx":
                    # Fix: React code using process.env (browser) instead of hardcoded URLs
                    if "process.env" in raw or "from 'process'" in raw or 'from "process"' in raw:
                        raw = re.sub(r"import.*from\s+['\"]process['\"]\s*;?\s*\n?", "", raw)
                        raw = raw.replace("process.env.BACKEND_URL", "'/api'")
                        raw = raw.replace("process.env.REACT_APP_API_URL", "'/api'")
                        raw = raw.replace("process.env.VITE_API_URL", "'/api'")
                        raw = raw.replace("`${env.BACKEND_URL}", "'/api'")
                        raw = raw.replace("`${env.REACT_APP_API_URL}", "'/api'")
                        raw = raw.replace("`${env.VITE_API_URL}", "'/api'")
                        raw = raw.replace("env.BACKEND_URL", "'/api'")
                        raw = raw.replace("env.REACT_APP_API_URL", "'/api'")
                        raw = raw.replace("env.VITE_API_URL", "'/api'")
                    # Basic bracket balance check
                    opens = raw.count("{") + raw.count("(") + raw.count("[")
                    closes = raw.count("}") + raw.count(")") + raw.count("]")
                    if opens > closes:
                        raw += "\n" + "}" * (opens - closes - raw.count("}"))

                elif fname == "Dockerfile":
                    # Fix common Dockerfile issues
                    if "FROM requires either one or three arguments" in raw:
                        # Strip garbage before FROM
                        idx = raw.find("FROM ")
                        if idx > 0:
                            raw = raw[idx:]

                if raw != original:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(raw)
                    log(f"[PostProcess]: Fixed {fname}")
            except Exception:
                pass

    # Fix test file endpoint paths to match actual API routes
    _fix_test_endpoints(target_path, log, project_type)

    # Auto-add npm deps that are imported in JSX/JS but missing from package.json
    pkg_json = os.path.join(target_path, "frontend", "package.json")
    if os.path.exists(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.loads(f.read())
            imported_packages = set()
            for _root, _dirs, files in os.walk(target_path):
                for fn in files:
                    if fn.endswith((".jsx", ".js")) and fn != "package.json":
                        fpath = os.path.join(_root, fn)
                        with open(fpath, "r", encoding="utf-8", errors="replace") as sf:
                            content = sf.read()
                        for m in re.finditer(r"""from\s+['"](@[\w-]+/[\w-]+|[\w-]+)['"]""", content):
                            pkg_name = m.group(1)
                            if not pkg_name.startswith(".") and not pkg_name.startswith("/"):
                                imported_packages.add(pkg_name)
            deps = pkg.setdefault("dependencies", {})
            dev_deps = pkg.setdefault("devDependencies", {})
            all_deps = set(deps.keys()) | set(dev_deps.keys())
            added = []
            for ip in sorted(imported_packages):
                if ip not in all_deps:
                    deps[ip] = "*"
                    added.append(ip)
            if added:
                with open(pkg_json, "w", encoding="utf-8") as f:
                    json.dump(pkg, f, indent=2)
                log(f"[PostProcess]: Added npm deps to package.json: {', '.join(added)}")
        except Exception as e:
            log(f"[PostProcess]: package.json fix error: {e}")

    # Clean up unnecessary files (React/Node artifacts, setup.py, docker-compose)
    is_react_project = project_type in ("react", "fullstack", "history_story", "landing_page")
    junk_files = {"package.json", "package-lock.json", "postcss.config.js", "tailwind.config.js",
                  "setup.py", "setup.cfg", "docker-compose.yml", "docker-compose.yaml",
                  "vite.config.js", "vite.config.ts", ".babelrc", "tsconfig.json"}
    # For React projects, keep frontend build files
    if is_react_project:
        junk_files -= {"package.json", "postcss.config.js", "tailwind.config.js", "vite.config.js", "vite.config.ts"}
    if project_type == "telegram_bot":
        junk_files -= {"docker-compose.yml", "docker-compose.yaml"}
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if fname in junk_files:
                fpath = os.path.join(root, fname)
                try:
                    os.remove(fpath)
                    log(f"[PostProcess]: Removed junk file: {fname}")
                except Exception:
                    pass
    # Remove empty __pycache__ directories
    for root, dirs, _files in os.walk(target_path):
        for d in dirs:
            if d == "__pycache__":
                try:
                    import shutil
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                except Exception:
                    pass


def _codex_generate_single_file(project_title, spec, fpath, purpose, already_generated, is_first, project_type="simple"):
    """Generate ONE file using an AI call."""
    gen_list = "\n".join(f"  {k}" for k in already_generated)
    type_instructions = _get_prompt_for_type(fpath, project_type)
    prompt = (
        f"You are Codex, an expert developer. Generate a SINGLE file for the project '{project_title}'.\n\n"
        f"PROJECT DESCRIPTION:\n{spec[:2000]}\n\n"
        f"FILE TO GENERATE:\n  {fpath}\n  Purpose: {purpose}\n\n"
        f"ALREADY GENERATED FILES:\n{gen_list if gen_list else '  (first file)'}\n\n"
        f"INSTRUCTIONS:\n"
        f"- Output ONLY the file content, no extra text, no markdown, no explanation\n"
        f"{type_instructions}\n"
        f"- Return ONLY the file content, nothing else\n"
        f"- CRITICAL: os.getenv() MUST have defaults. PyMuPDF = import fitz. uvicorn.run() NO debug=True. In templates use /static/ not url_for()."
    )
    provider, model = get_agent_provider_model("codex")
    agent_data = agent_configs.get("codex", {})
    raw = ai_utils.ask_studio_ai_with_history(
        provider=provider, model_name=model,
        system_prompt=(
            "You are Codex, an expert developer. Generate one file. "
            "Return ONLY the file content — NO markdown, NO backticks, NO explanation. "
            f"{_CRITICAL_RULES}"
        ),
        chat_history=[{"role": "user", "content": prompt}],
        temperature=agent_data.get("temperature", 0.2),
        max_tokens=4096,
    )
    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        fence = lines[0]
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        elif raw.endswith(fence):
            raw = raw[:-len(fence)].strip()
    return raw


def _detect_project_type(spec_lower):
    """Detect project type from spec and return (type_name, file_tree)."""
    has_react = "react" in spec_lower and "no react" not in spec_lower
    has_database = ("database" in spec_lower or "sql" in spec_lower or "orm" in spec_lower or "postgres" in spec_lower or "mysql" in spec_lower or "sqlite" in spec_lower) and "no database" not in spec_lower and "no sql" not in spec_lower
    has_auth = ("auth" in spec_lower or "jwt" in spec_lower or "login" in spec_lower or "signup" in spec_lower or "password" in spec_lower or "oauth" in spec_lower) and "no auth" not in spec_lower
    has_docker = "docker" in spec_lower and "no docker" not in spec_lower
    is_history = ("scroll story" in spec_lower or "timeline" in spec_lower or "history of" in spec_lower
                  or "consoles" in spec_lower or "sketchfab" in spec_lower or "3d model" in spec_lower
                  or "3d embeds" in spec_lower or "autospin" in spec_lower)
    is_landing = (("landing page" in spec_lower or "marketing site" in spec_lower or "product page" in spec_lower)
                  and ("frontend-only" in spec_lower or "frontend only" in spec_lower or "react" in spec_lower)
                  and "no backend" in spec_lower)
    is_telegram_bot = "aiogram" in spec_lower or ("telegram" in spec_lower and "bot" in spec_lower)
    is_fullstack = has_react and has_database
    is_react_app = has_react and not has_database
    is_api_db = has_database and not has_react
    is_api_auth = has_auth and not has_react and not has_database and not is_history

    if is_history:
        return "history_story", _history_story_template()
    if is_landing:
        return "landing_page", _landing_page_template()
    if is_telegram_bot:
        return "telegram_bot", _telegram_bot_template()
    if is_fullstack:
        return "fullstack", _fullstack_template()
    if is_react_app:
        return "react", _react_template()
    if is_api_db:
        return "api_db", _api_db_template()
    if is_api_auth:
        return "api_auth", _api_auth_template()
    return "simple", _simple_template()


def _simple_template():
    return [
        {"path": "main.py", "purpose": "FastAPI app entry point with Jinja2 templates, all routes, and PDF parsing logic. Include uvicorn.run() at bottom.", "depends_on": [], "exports": ["app"]},
        {"path": "requirements.txt", "purpose": "Python dependencies: fastapi, uvicorn, jinja2, python-dotenv, PyMuPDF, pytest", "depends_on": [], "exports": []},
        {"path": ".env", "purpose": "Environment variables with defaults: HOST, PORT, DEBUG", "depends_on": [], "exports": []},
        {"path": "templates/index.html", "purpose": "Main upload page with file input and drag-drop", "depends_on": [], "exports": []},
        {"path": "templates/result.html", "purpose": "Results page showing extracted PDF data", "depends_on": [], "exports": []},
        {"path": "static/style.css", "purpose": "Application styles", "depends_on": [], "exports": []},
        {"path": "static/script.js", "purpose": "Client-side JavaScript for upload and interaction", "depends_on": [], "exports": []},
        {"path": "tests/test_main.py", "purpose": "pytest tests using TestClient for all API endpoints", "depends_on": ["main.py"], "exports": []},
        {"path": "tests/conftest.py", "purpose": "pytest fixtures: test client", "depends_on": ["main.py"], "exports": ["client"]},
        {"path": "README.md", "purpose": "Project documentation with exact commands matching actual project", "depends_on": [], "exports": []},
    ]

def _react_template():
    return [
        {"path": "backend/main.py", "purpose": "FastAPI backend with CORS, ALL CRUD endpoints, in-memory storage. Include uvicorn.run() at bottom. Define EVERYTHING here.", "depends_on": [], "exports": ["app"]},
        {"path": "backend/requirements.txt", "purpose": "Python deps: fastapi, uvicorn, python-dotenv, pytest", "depends_on": [], "exports": []},
        {"path": "frontend/package.json", "purpose": "NPM: react, react-dom, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer (ADD any other packages the project needs, e.g. three, @react-three/fiber, @react-three/drei, react-router-dom, axios). type=module.", "depends_on": [], "exports": []},
        {"path": "frontend/vite.config.js", "purpose": "Vite with React plugin, proxy /api to http://localhost:8000", "depends_on": [], "exports": []},
        {"path": "frontend/tailwind.config.js", "purpose": "Tailwind content: ./index.html ./src/**/*.{js,jsx}", "depends_on": [], "exports": []},
        {"path": "frontend/postcss.config.js", "purpose": "PostCSS: tailwindcss + autoprefixer plugins", "depends_on": [], "exports": []},
        {"path": "frontend/index.html", "purpose": "HTML with div#root and script type=module src=/src/main.jsx", "depends_on": [], "exports": []},
        {"path": "frontend/src/main.jsx", "purpose": "ReactDOM.createRoot render App", "depends_on": ["frontend/src/App.jsx"], "exports": []},
        {"path": "frontend/src/App.jsx", "purpose": "React main App component with Tailwind classes (use fetch(), Three.js, or any libs the project needs)", "depends_on": [], "exports": ["App"]},
        {"path": "frontend/src/index.css", "purpose": "@tailwind base/components/utilities", "depends_on": [], "exports": []},
        {"path": ".env", "purpose": "BACKEND_URL=http://localhost:8000", "depends_on": [], "exports": []},
        {"path": "tests/test_api.py", "purpose": "pytest tests for API endpoints", "depends_on": [], "exports": []},
    ]

def _api_db_template():
    return [
        {"path": "backend/main.py", "purpose": "FastAPI entry point with SQLAlchemy, CORS, ALL models, ALL routes, DB setup. Include uvicorn.run() at bottom. Define EVERYTHING here — no separate model/schema/router files.", "depends_on": [], "exports": ["app"]},
        {"path": "backend/requirements.txt", "purpose": "Python deps: fastapi, uvicorn, sqlalchemy, python-dotenv, pytest, httpx, aiosqlite or psycopg2-binary", "depends_on": [], "exports": []},
        {"path": ".env", "purpose": "DATABASE_URL with default sqlite:///./app.db", "depends_on": [], "exports": []},
        {"path": "tests/test_api.py", "purpose": "pytest tests using TestClient for all endpoints", "depends_on": [], "exports": []},
        {"path": "README.md", "purpose": "Project documentation", "depends_on": [], "exports": []},
    ]

def _api_auth_template():
    return [
        {"path": "backend/main.py", "purpose": "FastAPI entry point with CORS, JWT auth, ALL routes (register, login, profile). Include uvicorn.run() at bottom. Define ALL auth logic HERE — DO NOT split across files.", "depends_on": [], "exports": ["app"]},
        {"path": "backend/config.py", "purpose": "Config via os.getenv with defaults for SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES", "depends_on": [], "exports": ["settings"]},
        {"path": "backend/requirements.txt", "purpose": "Python deps: fastapi, uvicorn, python-dotenv, python-jose, passlib, bcrypt, pytest, httpx", "depends_on": [], "exports": []},
        {"path": ".env", "purpose": "SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES with defaults", "depends_on": [], "exports": []},
        {"path": "tests/test_api.py", "purpose": "pytest tests for auth endpoints using TestClient", "depends_on": [], "exports": []},
    ]


def _telegram_bot_template():
    return [
        {"path": "bot.py", "purpose": "aiogram 3 entrypoint with polling and daily scheduler", "depends_on": [], "exports": []},
        {"path": "config.py", "purpose": "dotenv config for BOT_TOKEN, ADMIN_ID, DATABASE_PATH", "depends_on": [], "exports": []},
        {"path": "handlers/commands.py", "purpose": "user commands /start /help /joke /category /top /favorite /random100 /search", "depends_on": [], "exports": []},
        {"path": "handlers/callbacks.py", "purpose": "inline button callbacks for favorite, more jokes, category, share", "depends_on": [], "exports": []},
        {"path": "handlers/admin.py", "purpose": "admin commands /stats /users /broadcast", "depends_on": [], "exports": []},
        {"path": "keyboards/inline.py", "purpose": "inline keyboards", "depends_on": [], "exports": []},
        {"path": "services/joke_service.py", "purpose": "provider fallback, filtering, cache, search, statistics", "depends_on": [], "exports": []},
        {"path": "services/cache.py", "purpose": "in-memory last 100 joke cache and per-user recent jokes", "depends_on": [], "exports": []},
        {"path": "services/database.py", "purpose": "async sqlite wrapper via asyncio.to_thread", "depends_on": [], "exports": []},
        {"path": "services/providers/base.py", "purpose": "JokeProvider interface", "depends_on": [], "exports": []},
        {"path": "services/providers/provider1.py", "purpose": "provider implementation", "depends_on": [], "exports": []},
        {"path": "services/providers/provider2.py", "purpose": "provider implementation", "depends_on": [], "exports": []},
        {"path": "services/providers/provider3.py", "purpose": "provider implementation", "depends_on": [], "exports": []},
        {"path": "utils/text.py", "purpose": "text cleaning/hash helpers", "depends_on": [], "exports": []},
        {"path": "requirements.txt", "purpose": "runtime deps", "depends_on": [], "exports": []},
        {"path": "Dockerfile", "purpose": "container", "depends_on": [], "exports": []},
        {"path": "docker-compose.yml", "purpose": "compose launch", "depends_on": [], "exports": []},
        {"path": ".env.example", "purpose": "example env", "depends_on": [], "exports": []},
        {"path": "README.md", "purpose": "run instructions", "depends_on": [], "exports": []},
    ]

def _fullstack_template():
    return [
        {"path": "backend/main.py", "purpose": "FastAPI backend with SQLAlchemy, CORS, ALL models/routes for the entity. Include uvicorn.run() at bottom. Define EVERYTHING in one file.", "depends_on": [], "exports": ["app"]},
        {"path": "backend/requirements.txt", "purpose": "Python deps: fastapi, uvicorn, sqlalchemy, python-dotenv, pytest, httpx, aiosqlite", "depends_on": [], "exports": []},
        {"path": "frontend/package.json", "purpose": "NPM: react, react-dom, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer. type=module.", "depends_on": [], "exports": []},
        {"path": "frontend/vite.config.js", "purpose": "Vite with React plugin, proxy /api to http://localhost:8000", "depends_on": [], "exports": []},
        {"path": "frontend/tailwind.config.js", "purpose": "Tailwind content: ./index.html ./src/**/*.{js,jsx}", "depends_on": [], "exports": []},
        {"path": "frontend/postcss.config.js", "purpose": "PostCSS: tailwindcss + autoprefixer plugins", "depends_on": [], "exports": []},
        {"path": "frontend/index.html", "purpose": "HTML with div#root and script type=module src=/src/main.jsx", "depends_on": [], "exports": []},
        {"path": "frontend/src/main.jsx", "purpose": "ReactDOM.createRoot render App", "depends_on": ["frontend/src/App.jsx"], "exports": []},
        {"path": "frontend/src/App.jsx", "purpose": "React App with Tailwind classes, fetch() API calls to /api/notes", "depends_on": [], "exports": ["App"]},
        {"path": "frontend/src/index.css", "purpose": "@tailwind base/components/utilities", "depends_on": [], "exports": []},
        {"path": ".env", "purpose": "DATABASE_URL, BACKEND_URL", "depends_on": [], "exports": []},
        {"path": "tests/test_api.py", "purpose": "pytest tests for API endpoints with TestClient", "depends_on": [], "exports": []},
    ]


def _history_story_template():
    """Template for scrollable history / timeline sites with Sketchfab 3D embeds."""
    return [
        {"path": "frontend/package.json", "purpose": "NPM: react, react-dom, vite, @vitejs/plugin-react", "depends_on": [], "exports": []},
        {"path": "frontend/vite.config.js", "purpose": "Vite with React plugin", "depends_on": [], "exports": []},
        {"path": "frontend/index.html", "purpose": "HTML with div#root, hidden overflow, no scrollbar, dark background", "depends_on": [], "exports": []},
        {"path": "frontend/src/main.jsx", "purpose": "ReactDOM.createRoot render App", "depends_on": ["frontend/src/App.jsx"], "exports": []},
        {"path": "frontend/src/App.jsx", "purpose": "Full-screen scrollable timeline with 3D model embeds, animated info overlays, auto-rotate, language switcher EN/RU/DE", "depends_on": [], "exports": ["App"]},
        {"path": "frontend/src/index.css", "purpose": "body/html fullscreen, dark bg, scrollbar hidden", "depends_on": [], "exports": []},
    ]


def _landing_page_template():
    return [
        {"path": "frontend/package.json", "purpose": "NPM: react, react-dom, vite, @vitejs/plugin-react", "depends_on": [], "exports": []},
        {"path": "frontend/vite.config.js", "purpose": "Vite with React plugin", "depends_on": [], "exports": []},
        {"path": "frontend/index.html", "purpose": "HTML with div#root and script type=module src=/src/main.jsx", "depends_on": [], "exports": []},
        {"path": "frontend/src/main.jsx", "purpose": "ReactDOM.createRoot render App", "depends_on": ["frontend/src/App.jsx"], "exports": []},
        {"path": "frontend/src/App.jsx", "purpose": "Premium responsive product landing page", "depends_on": [], "exports": ["App"]},
        {"path": "frontend/src/index.css", "purpose": "Complete landing page CSS", "depends_on": [], "exports": []},
    ]


def _history_story_static_files(project):
    """Deterministic, complete fallback for history/scroll-story React sites."""
    title = project.get("title", "History Story")
    is_handheld = any(k in (project.get("description", "") + " " + title).lower() for k in ("handheld", "portable", "vita", "compact", "game boy", "steam deck"))
    if is_handheld:
        consoles_js = """[
  { key: 'gb', name: 'Game Boy', year: '1989', accent: '#9cff6e', sold: '118.7M', chip: 'Sharp LR35902', display: '160x144 LCD', battery: '4 AA / marathon life', media: 'Cartridge', games: 'Tetris, Pokemon Red/Blue', shape: 'gameboy' },
  { key: 'gba', name: 'Game Boy Advance', year: '2001', accent: '#b989ff', sold: '81.5M', chip: 'ARM7TDMI', display: '240x160 LCD', battery: '2 AA / portable 32-bit', media: 'Cartridge', games: 'Metroid Fusion, Advance Wars', shape: 'wide' },
  { key: 'ds', name: 'Nintendo DS', year: '2004', accent: '#5bd8ff', sold: '154M', chip: 'ARM9 + ARM7', display: 'Dual screens + touch', battery: 'Rechargeable lithium-ion', media: 'DS Card', games: 'Nintendogs, Mario Kart DS', shape: 'clamshell' },
  { key: 'psp', name: 'PlayStation Portable', year: '2004', accent: '#57a6ff', sold: '80M+', chip: 'MIPS R4000', display: '4.3 inch widescreen', battery: 'Rechargeable pack', media: 'UMD + Memory Stick', games: 'God of War, Monster Hunter', shape: 'psp' },
  { key: '3ds', name: 'Nintendo 3DS', year: '2011', accent: '#ff4f70', sold: '75.9M', chip: 'Dual-core ARM11', display: 'Glasses-free 3D', battery: 'Rechargeable lithium-ion', media: '3DS Card', games: 'Zelda: A Link Between Worlds', shape: 'clamshell' },
  { key: 'vita', name: 'PlayStation Vita', year: '2011', accent: '#4d7cff', sold: '15M+', chip: 'ARM Cortex-A9', display: 'OLED / LCD touch', battery: 'Rechargeable pack', media: 'Vita Card', games: 'Persona 4 Golden, Tearaway', shape: 'psp' },
  { key: 'switchlite', name: 'Nintendo Switch Lite', year: '2019', accent: '#ffe45e', sold: '23M+', chip: 'NVIDIA Tegra X1', display: '5.5 inch LCD', battery: 'Integrated handheld play', media: 'Game Card + digital', games: 'Animal Crossing, Zelda', shape: 'wide' },
  { key: 'steamdeck', name: 'Steam Deck', year: '2022', accent: '#66ffe3', sold: '3M+', chip: 'AMD Aerith APU', display: '7 inch LCD / OLED', battery: '40Wh PC handheld', media: 'Steam + microSD', games: 'Elden Ring, Hades', shape: 'deck' }
]"""
        subtitle = "Compact console timeline"
    else:
        consoles_js = """[
  { key: 'ps1', name: 'PlayStation', year: '1994', accent: '#7dc8ff', sold: '102.5M', chip: 'R3000A', display: 'TV output', battery: 'Home console', media: 'CD-ROM', games: 'Final Fantasy VII, Gran Turismo', shape: 'box' },
  { key: 'ps2', name: 'PlayStation 2', year: '2000', accent: '#355cff', sold: '155M+', chip: 'Emotion Engine', display: 'TV output', battery: 'Home console', media: 'DVD-ROM', games: 'GTA: San Andreas, Shadow of the Colossus', shape: 'box' },
  { key: 'ps3', name: 'PlayStation 3', year: '2006', accent: '#74f7ff', sold: '87.4M', chip: 'Cell Broadband Engine', display: 'HDMI', battery: 'Home console', media: 'Blu-ray', games: 'The Last of Us, Uncharted 2', shape: 'box' },
  { key: 'ps4', name: 'PlayStation 4', year: '2013', accent: '#2278ff', sold: '117.2M', chip: 'AMD Jaguar', display: 'HDMI', battery: 'Home console', media: 'Blu-ray / digital', games: 'Bloodborne, God of War', shape: 'box' },
  { key: 'ps5', name: 'PlayStation 5', year: '2020', accent: '#f5f8ff', sold: '59M+', chip: 'AMD Zen 2', display: '4K / 120Hz', battery: 'Home console', media: 'Ultra HD Blu-ray', games: 'Demon Souls, Returnal', shape: 'box' }
]"""
        subtitle = "Console timeline"

    app_js = f"""import React, {{ useEffect, useRef, useState }} from 'react';

const consoles = {consoles_js};

const copy = {{
  en: {{ title: '{title}', subtitle: '{subtitle}', show: 'Show stats', hide: 'Hide stats', sold: 'Units sold', chip: 'CPU / chip', display: 'Display', battery: 'Portable feature', media: 'Media', games: 'Signature games', footer: 'An interactive museum timeline of portable gaming hardware.' }},
  ru: {{ title: 'История портативных консолей', subtitle: 'Коллекционная интерактивная хроника', show: 'Показать статистику', hide: 'Скрыть статистику', sold: 'Продано', chip: 'Процессор / чип', display: 'Экран', battery: 'Портативность', media: 'Носитель', games: 'Главные игры', footer: 'Интерактивная музейная хроника портативного гейминга.' }},
  de: {{ title: 'Geschichte der Handheld-Konsolen', subtitle: 'Interaktive Sammler-Zeitleiste', show: 'Daten anzeigen', hide: 'Daten ausblenden', sold: 'Verkauft', chip: 'CPU / Chip', display: 'Display', battery: 'Mobilitaet', media: 'Medium', games: 'Praegende Spiele', footer: 'Eine interaktive Museums-Zeitleiste tragbarer Gaming-Hardware.' }}
}};

function WebGLBackdrop({{ active }}) {{
  const canvasRef = useRef(null);
  const mouse = useRef([0.5, 0.5]);

  useEffect(() => {{
    const canvas = canvasRef.current;
    const gl = canvas.getContext('webgl', {{ antialias: true, alpha: true }});
    if (!gl) return undefined;

    const vertex = 'attribute vec2 p; void main() {{ gl_Position = vec4(p, 0.0, 1.0); }}';
    const fragment = `precision mediump float;
      uniform vec2 u_res; uniform vec2 u_mouse; uniform float u_time; uniform vec3 u_accent;
      void main() {{
        vec2 uv = gl_FragCoord.xy / u_res.xy;
        float d = distance(uv, u_mouse);
        float rings = sin((d * 34.0) - u_time * 2.4) * 0.5 + 0.5;
        float glow = smoothstep(0.52, 0.0, d) * (0.36 + rings * 0.24);
        float grid = (sin((uv.x + u_time * 0.015) * 90.0) + sin((uv.y - u_time * 0.02) * 70.0)) * 0.018;
        vec3 color = vec3(0.01, 0.03, 0.08) + u_accent * glow + u_accent * grid;
        gl_FragColor = vec4(color, 0.82);
      }} `;

    const compile = (type, src) => {{
      const shader = gl.createShader(type);
      gl.shaderSource(shader, src);
      gl.compileShader(shader);
      return shader;
    }};
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertex));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(program);
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
    const pos = gl.getAttribLocation(program, 'p');
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(program, 'u_res');
    const uMouse = gl.getUniformLocation(program, 'u_mouse');
    const uTime = gl.getUniformLocation(program, 'u_time');
    const uAccent = gl.getUniformLocation(program, 'u_accent');
    let raf = 0;

    const toRgb = (hex) => {{
      const value = hex.replace('#', '');
      return [parseInt(value.slice(0, 2), 16) / 255, parseInt(value.slice(2, 4), 16) / 255, parseInt(value.slice(4, 6), 16) / 255];
    }};

    const render = (time) => {{
      const ratio = window.devicePixelRatio || 1;
      const w = Math.floor(canvas.clientWidth * ratio);
      const h = Math.floor(canvas.clientHeight * ratio);
      if (canvas.width !== w || canvas.height !== h) {{ canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h); }}
      gl.uniform2f(uRes, w, h);
      gl.uniform2f(uMouse, mouse.current[0], 1.0 - mouse.current[1]);
      gl.uniform1f(uTime, time * 0.001);
      gl.uniform3fv(uAccent, toRgb(consoles[active].accent));
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      raf = requestAnimationFrame(render);
    }};
    raf = requestAnimationFrame(render);
    const move = (event) => {{ mouse.current = [event.clientX / window.innerWidth, event.clientY / window.innerHeight]; }};
    window.addEventListener('pointermove', move);
    return () => {{ cancelAnimationFrame(raf); window.removeEventListener('pointermove', move); }};
  }}, [active]);

  return <canvas className="webgl-backdrop" ref={{canvasRef}} aria-hidden="true" />;
}}

function ConsoleMockup({{ item }}) {{
  return (
    <div className={{`console-mockup ${{item.shape}}`}} style={{{{ '--accent': item.accent }}}}>
      <div className="screen"><span>{{item.name}}</span></div>
      <div className="dpad" />
      <div className="buttons"><i /><i /><i /><i /></div>
      <div className="shine" />
    </div>
  );
}}

export default function App() {{
  const [lang, setLang] = useState('en');
  const [active, setActive] = useState(0);
  const [statsOpen, setStatsOpen] = useState(false);
  const t = copy[lang];
  const current = consoles[active];

  useEffect(() => {{
    const observer = new IntersectionObserver((entries) => {{
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setActive(Number(visible.target.dataset.index));
    }}, {{ threshold: [0.45, 0.65, 0.85] }});
    document.querySelectorAll('[data-section]').forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }}, []);

  useEffect(() => {{ document.documentElement.style.setProperty('--accent', current.accent); }}, [current.accent]);

  return (
    <main className="site-shell">
      <WebGLBackdrop active={{active}} />
      <header className="topbar">
        <div><span>{{t.subtitle}}</span><h1>{{t.title}}</h1></div>
        <nav>{{['en', 'ru', 'de'].map((code) => <button className={{lang === code ? 'active' : ''}} onClick={{() => setLang(code)}} key={{code}}>{{code.toUpperCase()}}</button>)}}</nav>
      </header>
      <button className="stats-button" onClick={{() => setStatsOpen((value) => !value)}}>{{statsOpen ? t.hide : t.show}}</button>
      <aside className={{`stats-panel ${{statsOpen ? 'open' : ''}}`}}>
        <h2>{{current.name}}</h2>
        {{[[t.sold, current.sold], [t.chip, current.chip], [t.display, current.display], [t.battery, current.battery], [t.media, current.media], [t.games, current.games]].map(([label, value], index) => (
          <div className="stat-card" style={{{{ transitionDelay: `${{index * 70}}ms` }}}} key={{label}}><span>{{label}}</span><strong>{{value}}</strong></div>
        ))}}
      </aside>
      {{consoles.map((item, index) => (
        <section className="story-section" data-section data-index={{index}} key={{item.key}} style={{{{ '--section-accent': item.accent }}}}>
          <div className="timeline"><b>{{item.year}}</b><i /></div>
          <ConsoleMockup item={{item}} />
          <article><p>{{item.year}}</p><h2>{{item.name}}</h2><span>{{item.chip}} / {{item.display}} / {{item.media}}</span></article>
        </section>
      ))}}
      <footer>{{t.footer}}</footer>
    </main>
  );
}}
"""

    css = """:root { --accent: #66ffe3; color: #f8fbff; background: #030712; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
* { box-sizing: border-box; } html { scroll-behavior: smooth; } body { margin: 0; min-width: 320px; overflow-x: hidden; background: #030712; } button { font: inherit; }
.site-shell { min-height: 100vh; isolation: isolate; position: relative; }
.webgl-backdrop { height: 100vh; inset: 0; position: fixed; width: 100vw; z-index: -2; }
.topbar { align-items: flex-start; backdrop-filter: blur(22px); background: rgba(3, 7, 18, .58); border: 1px solid rgba(255,255,255,.12); border-radius: 24px; display: flex; justify-content: space-between; left: 24px; padding: 18px 20px; position: fixed; right: 24px; top: 18px; z-index: 20; }
.topbar span { color: var(--accent); display: block; font-size: .72rem; font-weight: 900; letter-spacing: .24em; margin-bottom: 6px; text-transform: uppercase; }
.topbar h1 { font-size: clamp(1.1rem, 3vw, 2.6rem); line-height: .95; margin: 0; text-shadow: 0 0 30px color-mix(in srgb, var(--accent) 55%, transparent); }
.topbar nav { display: flex; gap: 8px; } .topbar button, .stats-button { background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.14); border-radius: 999px; color: #f8fafc; cursor: pointer; padding: 9px 12px; transition: transform .25s ease, background .25s ease, color .25s ease; }
.topbar button:hover, .topbar button.active, .stats-button:hover { background: var(--accent); color: #020617; transform: translateY(-2px); }
.stats-button { bottom: 22px; font-weight: 900; position: fixed; right: 22px; z-index: 25; }
.stats-panel { background: rgba(2, 6, 23, .72); backdrop-filter: blur(24px); border-left: 1px solid rgba(255,255,255,.12); bottom: 0; padding: 120px 24px 24px; position: fixed; right: 0; top: 0; transform: translateX(105%); transition: transform .45s cubic-bezier(.2,.8,.2,1); width: min(390px, 90vw); z-index: 18; }
.stats-panel.open { transform: translateX(0); } .stats-panel h2 { color: var(--accent); font-size: 2rem; margin-top: 0; }
.stat-card { background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.12); border-radius: 18px; display: grid; gap: 7px; margin-bottom: 12px; opacity: 0; padding: 16px; transform: translateX(34px); transition: opacity .35s ease, transform .35s ease; }
.stats-panel.open .stat-card { opacity: 1; transform: translateX(0); } .stat-card span { color: #93a4bd; font-size: .75rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; } .stat-card strong { font-size: 1.08rem; }
.story-section { align-items: center; display: grid; gap: 4vw; grid-template-columns: 120px minmax(320px, .95fr) minmax(320px, .9fr); min-height: 100vh; padding: 120px clamp(18px, 5vw, 80px) 54px; position: relative; }
.story-section::before { background: radial-gradient(circle at 50% 50%, color-mix(in srgb, var(--section-accent) 22%, transparent), transparent 34rem); content: ''; inset: 0; opacity: .9; position: absolute; z-index: -1; }
.timeline { align-items: center; display: grid; justify-items: center; min-height: 58vh; } .timeline b { color: var(--section-accent); font-size: clamp(2rem, 5vw, 5rem); writing-mode: vertical-rl; } .timeline i { background: linear-gradient(var(--section-accent), transparent); border-radius: 99px; display: block; height: 160px; width: 2px; }
.console-mockup { aspect-ratio: 1.45/1; background: linear-gradient(145deg, rgba(255,255,255,.18), rgba(255,255,255,.04)); border: 1px solid rgba(255,255,255,.16); border-radius: 34px; box-shadow: 0 32px 110px rgba(0,0,0,.48), 0 0 70px color-mix(in srgb, var(--accent) 38%, transparent); overflow: hidden; position: relative; transform: perspective(1000px) rotateY(-9deg) rotateX(4deg); }
.console-mockup.clamshell { aspect-ratio: 1.15/1; } .console-mockup.gameboy { aspect-ratio: .72/1; max-height: 68vh; } .console-mockup.deck { border-radius: 60px; }
.screen { align-items: center; background: radial-gradient(circle, color-mix(in srgb, var(--accent) 28%, #05111f), #01050d); border: 10px solid rgba(0,0,0,.46); border-radius: 24px; display: flex; inset: 14% 18%; justify-content: center; position: absolute; }
.screen span { color: var(--accent); font-size: clamp(1.2rem, 3vw, 3rem); font-weight: 1000; letter-spacing: -.05em; text-align: center; text-shadow: 0 0 26px var(--accent); }
.dpad { background: var(--accent); border-radius: 10px; filter: drop-shadow(0 0 14px var(--accent)); height: 52px; left: 7%; position: absolute; top: 45%; width: 52px; } .dpad::before, .dpad::after { background: #031022; border-radius: 5px; content: ''; position: absolute; } .dpad::before { height: 14px; left: 8px; top: 19px; width: 36px; } .dpad::after { height: 36px; left: 19px; top: 8px; width: 14px; }
.buttons { display: grid; gap: 10px; grid-template-columns: repeat(2, 1fr); position: absolute; right: 7%; top: 42%; } .buttons i { background: var(--accent); border-radius: 50%; box-shadow: 0 0 16px var(--accent); display: block; height: 22px; width: 22px; }
.shine { animation: shimmer 4s ease-in-out infinite; background: linear-gradient(90deg, transparent, rgba(255,255,255,.35), transparent); height: 140%; left: -80%; position: absolute; top: -20%; transform: rotate(18deg); width: 35%; }
article p { color: var(--section-accent); font-size: clamp(3rem, 10vw, 8rem); font-weight: 1000; line-height: .8; margin: 0; } article h2 { font-size: clamp(2.2rem, 6vw, 5.8rem); line-height: .88; margin: 14px 0; } article span { color: #cbd5e1; display: block; font-size: clamp(1rem, 2vw, 1.3rem); line-height: 1.65; max-width: 620px; }
footer { color: #94a3b8; padding: 42px 24px; text-align: center; }
@keyframes shimmer { 0%, 55% { left: -80%; } 100% { left: 140%; } }
@media (max-width: 900px) { .topbar { left: 12px; right: 12px; top: 12px; } .story-section { grid-template-columns: 1fr; padding-top: 150px; } .timeline { display: none; } .console-mockup { transform: none; } }
@media (max-width: 560px) { .topbar { flex-direction: column; gap: 12px; } .topbar nav { width: 100%; } .topbar button { flex: 1; } }
"""

    package_json = json.dumps({
        "name": re.sub(r"[^a-z0-9-]+", "-", title.lower()).strip("-") or "history-story",
        "version": "1.0.0",
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite --host 127.0.0.1", "build": "vite build", "preview": "vite preview --host 127.0.0.1"},
        "dependencies": {"@vitejs/plugin-react": "latest", "vite": "latest", "react": "latest", "react-dom": "latest"},
        "devDependencies": {},
    }, indent=2)

    return {
        "frontend/package.json": package_json,
        "frontend/vite.config.js": "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n\nexport default defineConfig({ plugins: [react()], server: { host: '127.0.0.1', port: 5173 } });\n",
        "frontend/index.html": f"<!doctype html>\n<html lang=\"en\">\n  <head>\n    <meta charset=\"UTF-8\" />\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n    <title>{title}</title>\n  </head>\n  <body>\n    <div id=\"root\"></div>\n    <script type=\"module\" src=\"/src/main.jsx\"></script>\n  </body>\n</html>\n",
        "frontend/src/main.jsx": "import React from 'react';\nimport { createRoot } from 'react-dom/client';\nimport App from './App.jsx';\nimport './index.css';\n\ncreateRoot(document.getElementById('root')).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n);\n",
        "frontend/src/App.jsx": app_js,
        "frontend/src/index.css": css,
    }

def _get_prompt_for_type(file_path, project_type):
    """Return appropriate AI prompt instructions based on project type and file path."""
    is_frontend = "frontend/" in file_path or file_path.endswith(".jsx") or file_path.endswith(".js")
    is_react_file = "frontend/" in file_path or file_path.endswith(".jsx") or file_path in ("package.json", "vite.config.js", "tailwind.config.js", "postcss.config.js")

    if project_type == "history_story":
        return (
            "- CRITICAL: This is a SCROLL STORY site — full-screen vertical scrolling timeline of game consoles\n"
            "- NO backend, NO database, NO tests — purely a frontend React SPA in ONE App.jsx file\n"
            "- Use INLINE STYLES exclusively (style={{}}), NOT Tailwind classes, NOT separate CSS\n"
            "- Each section is 100vh with iframe 3D model embed + animated text overlay\n"
            "- Embed URL format: https://sketchfab.com/models/MODEL_ID/embed?autostart=1&autospin=0.3&spin_icon=0&ui_hint=0&ui_controls=0&ui_infos=0&ui_watermark=0&ui_help=0&ui_settings=0&ui_annotations=0&ui_stop=0&ui_vr=0\n"
            "- Use IntersectionObserver for scroll-activated text animations\n"
            "- Include language switcher (EN/RU/DE) in top-right corner with useState\n"
            "- CONSOLE DATA: array of objects with name, gen, year, color (accent hex)\n"
            "- TRANSLATIONS: separate object L with keys en/ru/de, each with hdr, lbl[6], val[5][6], desc[5], ft1, ft2\n"
            "- Stats panel (Sold, CPU, RAM, GPU, Media, Peak Sales) flies in from right with backdropFilter blur\n"
            "- Each stat card has cascading delay animation (translateX + opacity)\n"
            "- Auto-rotate 3D models with autospin=0.3 in embed URL\n"
            "- Footer with attribution text from translations\n"
            "- DO NOT use scroll-snap. Use overflowY scroll on container div\n"
            "- INCLUDE EXACT SKETCHFAB MODEL URLS from the project spec. If missing, ASK user via QUESTION format\n"
            "- All file paths use / not backslash"
        )

    if project_type in ("react", "fullstack") and is_react_file:
        return (
            f"- Use React 18 with JSX syntax and ESM imports (import/export)\n"
            f"- Use Tailwind CSS classes for styling (NO separate CSS files except index.css)\n"
            f"- For package.json: include react, react-dom, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer, AND any other packages the project needs (e.g. three, @react-three/fiber, @react-three/drei, react-router-dom, axios). Set type=module\n"
            f"- For vite.config.js: use @vitejs/plugin-react, proxy /api to http://localhost:8000\n"
            f"- For tailwind.config.js: content paths must include ./index.html and ./src/**/{{js,jsx}}\n"
            f"- For postcss.config.js: use tailwindcss and autoprefixer plugins\n"
            f"- For index.html: single <div id=root></div> + <script type=module src=/src/main.jsx>\n"
            f"- For App.jsx: functional component with Tailwind classes (use Three.js/@react-three/fiber if project involves 3D)\n"
            f"- EVERY os.getenv() MUST have a default value\n"
            f"- Use try/catch for error handling\n"
            f"- All file paths use / not \\\\"
        )
    if project_type in ("react", "fullstack"):
        return (
            f"- Backend: Python FastAPI REST API in a SINGLE main.py file (NO Jinja2 templates)\n"
            f"- Define ALL models, routes, and server config in main.py — DO NOT split across files\n"
            f"- Return JSON responses only (dicts/lists), NOT HTML templates\n"
            f"- Use Pydantic models (BaseModel) for request/response schemas\n"
            f"- Enable CORS middleware for frontend origin\n"
            f"- EVERY os.getenv() MUST have a default value\n"
            f"- Use try/except for error handling\n"
            f"- Do NOT use async def unless absolutely necessary\n"
            f"- All file paths use / not \\\\"
        )
    if project_type == "api_db":
        return (
            f"- Backend: Python FastAPI REST API with SQLAlchemy ORM in a SINGLE main.py file\n"
            f"- Define ALL models, database setup, and routes in main.py — DO NOT split across files\n"
            f"- Return JSON responses only (dicts/lists), NOT HTML templates\n"
            f"- Use sync SQLAlchemy: create_engine + sessionmaker + declarative_base\n"
            f"- Use aiosqlite for SQLite (async) or psycopg2-binary for PostgreSQL (sync)\n"
            f"- NEVER mix sync engine with async queries — pick ONE pattern\n"
            f"- EVERY os.getenv() MUST have a default value\n"
            f"- Use try/except for error handling\n"
            f"- All file paths use / not \\\\"
        )
    if project_type == "api_auth":
        return (
            f"- Backend: Python FastAPI REST API with JWT authentication in a SINGLE main.py file\n"
            f"- Define ALL auth logic (JWT create/verify, password hashing, user storage) in main.py — DO NOT split across files\n"
            f"- Return JSON responses only (dicts/lists), NOT HTML templates\n"
            f"- Use python-jose for JWT tokens, passlib+bcrypt for password hashing\n"
            f"- JWT secret key from os.getenv('SECRET_KEY', 'generate-a-random-key-here')\n"
            f"- Store users in memory (list or dict) — NO database, NO SQLAlchemy\n"
            f"- Define pwd_context ONCE at module level, reuse everywhere\n"
            f"- EVERY os.getenv() MUST have a default value\n"
            f"- Use try/except for error handling\n"
            f"- All file paths use / not \\\\"
        )
    return (
        f"- Use SIMPLE architecture: FastAPI + Jinja2 templates + vanilla JavaScript + CSS\n"
        f"- NO React, NO Vue, NO SPA frameworks — use Jinja2 HTML templates\n"
        f"- Backend: Python FastAPI with Jinja2 templates. Keep backend in ONE file if possible (main.py)\n"
        f"- In Jinja2 templates, use DIRECT paths for static files: /static/style.css, NOT url_for()\n"
        f"- For PDF processing: use PyMuPDF (import fitz), fitz.open(stream=file_bytes, filetype='pdf') NOT fitz.open('filename.pdf')\n"
        f"- EVERY os.getenv() MUST have a default value\n"
        f"- ALL imports must be correct and match what's available\n"
        f"- Use try/except for error handling\n"
        f"- uvicorn.run() must NOT have debug=True. Use: uvicorn.run(app, host=host, port=port)\n"
        f"- Do NOT use async def for route handlers unless absolutely necessary (sync is simpler)\n"
        f"- All file paths use / not \\\\"
    )


def _landing_page_static_files(project):
    """Deterministic, original frontend-only premium smartphone landing page."""
    pkg = {"name":"luma-phone-landing-page","version":"1.0.0","private":True,"type":"module","scripts":{"dev":"vite","build":"vite build","preview":"vite preview"},"dependencies":{"@vitejs/plugin-react":"^4.3.1","vite":"^5.3.1","react":"^18.3.1","react-dom":"^18.3.1"},"devDependencies":{}}
    app_jsx = '''import React from 'react';

const features = [
  ['Astra Kamera', '48 MP Sensorsystem mit sanfter Nachtlogik und natürlicher Farbwiedergabe.'],
  ['Nebula Display', '6,7 Zoll OLED mit adaptiven 1-120 Hz und 2.400 Nits Spitzenhelligkeit.'],
  ['Silica Core', 'Ein effizienter 4 nm Chip für Gaming, Video und produktive Workflows.'],
  ['Zwei Tage Akku', 'Intelligentes Energiemanagement, 80 W Laden und langlebige Zellchemie.'],
];
const models = [['Luma Phone','6,1 Zoll','Astra Dual','ab 899 EUR'],['Luma Phone Max','6,7 Zoll','Astra Pro','ab 1099 EUR'],['Luma Phone Air','6,4 Zoll','Astra Slim','ab 999 EUR']];

export default function App(){return <main><nav className="nav"><a className="brand" href="#top">Luma</a><div className="navlinks"><a href="#camera">Kamera</a><a href="#design">Design</a><a href="#compare">Vergleichen</a></div><a className="buy" href="#compare">Kaufen</a></nav><section id="top" className="hero section"><p className="eyebrow">Luma Phone</p><h1>Leicht. Schnell. Unverwechselbar.</h1><p className="lede">Ein fiktives Premium-Smartphone mit ruhigem Design, starker Kamera und einem Interface, das sich wie aus einem Guss anfühlt.</p><div className="actions"><a className="primary" href="#compare">Modelle ansehen</a><a className="secondary" href="#film">Film ansehen</a></div><div className="phone-stage" aria-label="Abstrakte Smartphone-Visualisierung"><div className="phone phone-a"/><div className="phone phone-b"/></div></section><section id="film" className="cinema section"><div><p className="eyebrow">Neue Form</p><h2>Ein Rahmen aus Licht.</h2></div><p>Glas, Keramik und ein matter Metallrahmen verschmelzen zu einer klaren Silhouette. Keine Logos, keine Kopien, nur eine eigenständige Produktvision.</p></section><section id="camera" className="feature-grid section">{features.map(([title,text])=><article key={title} className="card"><h3>{title}</h3><p>{text}</p></article>)}</section><section id="design" className="split section"><div className="orb"/><div><p className="eyebrow">Luma OS</p><h2>Gemacht fuer konzentrierte Momente.</h2><p>Widgets, Mitteilungen und Kamera-Modi bleiben bewusst reduziert. Alles reagiert schnell, weich und vorhersehbar.</p></div></section><section id="compare" className="section compare"><p className="eyebrow">Vergleich</p><h2>Finde dein Luma.</h2><div className="model-grid">{models.map(([name,size,camera,price])=><article key={name} className="model"><div className="mini-phone"/><h3>{name}</h3><p>{size}</p><p>{camera}</p><strong>{price}</strong></article>)}</div></section><footer>Originale Demo-Landingpage fuer eine fiktive Marke. Keine Verbindung zu Apple oder anderen Marken.</footer></main>}
'''
    css = '''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;color:#111;overflow-x:hidden}a{color:inherit;text-decoration:none}.nav{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;padding:14px clamp(18px,4vw,64px);background:rgba(245,245,247,.72);backdrop-filter:blur(22px);border-bottom:1px solid rgba(0,0,0,.08)}.brand{font-weight:800;letter-spacing:-.04em}.navlinks{display:flex;gap:24px;font-size:13px;color:#555}.buy,.primary{border-radius:999px;background:#0077ed;color:white;padding:9px 18px;font-weight:700;font-size:13px}.section{padding:clamp(72px,10vw,150px) clamp(20px,5vw,86px)}.hero{text-align:center;min-height:92vh;display:grid;place-items:center;gap:24px;background:radial-gradient(circle at 50% 74%,#b9d7ff,transparent 34%),linear-gradient(#fff,#f5f5f7)}.eyebrow{text-transform:uppercase;letter-spacing:.18em;font-size:12px;font-weight:800;color:#6e6e73}.hero h1{font-size:clamp(58px,10vw,132px);line-height:.88;letter-spacing:-.075em;margin:0;max-width:1050px}.lede{max-width:760px;margin:0 auto;color:#555;font-size:clamp(19px,2.5vw,29px);line-height:1.22}.actions{display:flex;justify-content:center;gap:14px;flex-wrap:wrap}.secondary{border:1px solid rgba(0,119,237,.3);color:#0077ed;border-radius:999px;padding:9px 18px;font-weight:700}.phone-stage{position:relative;height:420px;width:min(88vw,860px);margin-top:20px}.phone{position:absolute;left:50%;top:20px;width:250px;height:360px;border-radius:46px;background:linear-gradient(145deg,#111,#3d465a 58%,#a8c8ff);box-shadow:0 50px 110px rgba(0,0,0,.28),inset 0 0 0 8px #111}.phone:after{content:"";position:absolute;inset:18px;border-radius:34px;background:radial-gradient(circle at 70% 20%,rgba(255,255,255,.5),transparent 20%),linear-gradient(160deg,#0f172a,#1d4ed8 58%,#9fd3ff)}.phone-a{transform:translateX(-72%) rotate(-10deg)}.phone-b{transform:translateX(-18%) rotate(11deg);opacity:.92}.cinema{display:grid;grid-template-columns:1fr 1fr;gap:34px;align-items:end;background:#111;color:white;border-radius:44px;margin:0 clamp(16px,4vw,52px);min-height:520px}.cinema h2,.split h2,.compare h2{font-size:clamp(42px,7vw,92px);line-height:.95;letter-spacing:-.06em;margin:0}.cinema p:last-child{font-size:clamp(18px,2.4vw,28px);line-height:1.22;color:#d1d5db}.feature-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}.card,.model{border-radius:32px;background:white;padding:30px;box-shadow:0 20px 60px rgba(0,0,0,.08)}.card h3,.model h3{font-size:24px;margin:0 0 16px}.card p,.model p{color:#666;line-height:1.5}.split{display:grid;grid-template-columns:1fr 1fr;gap:50px;align-items:center}.orb{height:520px;border-radius:54px;background:radial-gradient(circle at 35% 25%,#fff,transparent 18%),linear-gradient(135deg,#111,#334155,#c7d2fe);box-shadow:0 50px 120px rgba(30,41,59,.25)}.split p{font-size:22px;color:#555;line-height:1.35}.compare{text-align:center}.model-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:34px}.mini-phone{height:160px;width:92px;margin:0 auto 20px;border-radius:24px;background:linear-gradient(145deg,#111,#64748b);box-shadow:inset 0 0 0 5px #111}footer{text-align:center;color:#777;font-size:13px;padding:40px 20px 70px}@media(max-width:850px){.navlinks{display:none}.cinema,.split,.feature-grid,.model-grid{grid-template-columns:1fr}.hero h1{font-size:58px}.phone-stage{height:330px}.phone{width:190px;height:285px}.section{padding:64px 20px}.cinema{margin:0 12px;border-radius:30px}}'''
    return {"frontend/package.json": json.dumps(pkg, indent=2), "frontend/vite.config.js": "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n\nexport default defineConfig({ plugins: [react()] });\n", "frontend/index.html": "<html><head><title>Luma Phone</title></head><body><div id=\"root\"></div><script type=\"module\" src=\"/src/main.jsx\"></script></body></html>", "frontend/src/main.jsx": "import React from 'react';\nimport { createRoot } from 'react-dom/client';\nimport App from './App.jsx';\nimport './index.css';\n\ncreateRoot(document.getElementById('root')).render(<App />);\n", "frontend/src/App.jsx": app_jsx, "frontend/src/index.css": css}


def _telegram_bot_static_files(project):
    """Deterministic async aiogram Telegram joke bot project."""
    return {
        ".env.example": "BOT_TOKEN=replace_me\nADMIN_ID=0\nDATABASE_PATH=data/bot.db\n",
        ".env": "BOT_TOKEN=replace_me\nADMIN_ID=0\nDATABASE_PATH=data/bot.db\n",
        "requirements.txt": "aiogram>=3.4,<4\naiohttp>=3.9\nbeautifulsoup4>=4.12\npython-dotenv>=1.0\n",
        "Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nRUN mkdir -p data\nCMD [\"python\", \"bot.py\"]\n",
        "docker-compose.yml": "services:\n  joke-bot:\n    build: .\n    env_file: .env\n    restart: unless-stopped\n    volumes:\n      - ./data:/app/data\n",
        "config.py": '''from __future__ import annotations\n\nfrom dataclasses import dataclass\nfrom pathlib import Path\nimport os\nfrom dotenv import load_dotenv\n\nload_dotenv()\n\n@dataclass(frozen=True)\nclass Settings:\n    bot_token: str = os.getenv("BOT_TOKEN", "")\n    admin_id: int = int(os.getenv("ADMIN_ID", "0") or "0")\n    database_path: Path = Path(os.getenv("DATABASE_PATH", "data/bot.db"))\n\nsettings = Settings()\n''',
        "utils/__init__.py": "",
        "utils/logging_config.py": '''import logging\n\ndef setup_logging() -> None:\n    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")\n''',
        "utils/text.py": '''from __future__ import annotations\n\nimport hashlib\nimport re\nfrom bs4 import BeautifulSoup\n\n_SPACES = re.compile(r"\\s+")\n\ndef clean_joke(raw: str) -> str:\n    text = BeautifulSoup(raw or "", "html.parser").get_text(" ")\n    text = _SPACES.sub(" ", text).strip()\n    return text\n\ndef is_valid_joke(text: str) -> bool:\n    return bool(text and len(text) > 30 and "<" not in text and ">" not in text)\n\ndef joke_hash(text: str) -> str:\n    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n''',
        "services/__init__.py": "",
        "services/cache.py": '''from __future__ import annotations\n\nfrom collections import deque, defaultdict\n\nclass JokeCache:\n    def __init__(self, max_global: int = 100, max_user: int = 20) -> None:\n        self.global_jokes: deque[str] = deque(maxlen=max_global)\n        self.user_recent: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=max_user))\n        self.by_hash: dict[str, str] = {}\n\n    def remember(self, telegram_id: int, joke: str, joke_hash: str) -> None:\n        self.global_jokes.append(joke)\n        self.user_recent[telegram_id].append(joke_hash)\n        self.by_hash[joke_hash] = joke\n\n    def seen_by_user(self, telegram_id: int, joke_hash: str) -> bool:\n        return joke_hash in self.user_recent[telegram_id]\n\n    def get_by_hash(self, joke_hash: str) -> str | None:\n        return self.by_hash.get(joke_hash)\n''',
        "services/database.py": '''from __future__ import annotations\n\nimport asyncio\nfrom datetime import datetime, timezone\nfrom pathlib import Path\nimport sqlite3\n\nclass Database:\n    def __init__(self, path: Path) -> None:\n        self.path = path\n        self.path.parent.mkdir(parents=True, exist_ok=True)\n\n    async def init(self) -> None:\n        await asyncio.to_thread(self._init_sync)\n\n    def _connect(self) -> sqlite3.Connection:\n        conn = sqlite3.connect(self.path)\n        conn.row_factory = sqlite3.Row\n        return conn\n\n    def _init_sync(self) -> None:\n        with self._connect() as db:\n            db.executescript("""\n            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE, username TEXT, registration_date TEXT);\n            CREATE TABLE IF NOT EXISTS favorites(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, joke TEXT, created_at TEXT);\n            CREATE TABLE IF NOT EXISTS statistics(id INTEGER PRIMARY KEY AUTOINCREMENT, joke_hash TEXT UNIQUE, likes INTEGER DEFAULT 0, views INTEGER DEFAULT 0, shares INTEGER DEFAULT 0);\n            CREATE TABLE IF NOT EXISTS category_stats(category TEXT PRIMARY KEY, requests INTEGER DEFAULT 0);\n            """)\n\n    async def upsert_user(self, telegram_id: int, username: str | None) -> None:\n        now = datetime.now(timezone.utc).isoformat()\n        await asyncio.to_thread(self._execute, "INSERT OR IGNORE INTO users(telegram_id, username, registration_date) VALUES(?,?,?)", (telegram_id, username or "", now))\n\n    async def add_favorite(self, telegram_id: int, joke: str) -> None:\n        now = datetime.now(timezone.utc).isoformat()\n        await asyncio.to_thread(self._execute, "INSERT INTO favorites(telegram_id, joke, created_at) VALUES(?,?,?)", (telegram_id, joke, now))\n\n    async def favorites(self, telegram_id: int) -> list[str]:\n        rows = await asyncio.to_thread(self._fetchall, "SELECT joke FROM favorites WHERE telegram_id=? ORDER BY id DESC LIMIT 20", (telegram_id,))\n        return [row["joke"] for row in rows]\n\n    async def user_ids(self) -> list[int]:\n        rows = await asyncio.to_thread(self._fetchall, "SELECT telegram_id FROM users", ())\n        return [int(row["telegram_id"]) for row in rows]\n\n    async def increment_stat(self, joke_hash: str, field: str) -> None:\n        if field not in {"likes", "views", "shares"}:\n            return\n        await asyncio.to_thread(self._increment_stat_sync, joke_hash, field)\n\n    async def increment_category(self, category: str) -> None:\n        await asyncio.to_thread(self._execute, "INSERT INTO category_stats(category, requests) VALUES(?,1) ON CONFLICT(category) DO UPDATE SET requests=requests+1", (category,))\n\n    async def stats_summary(self) -> dict[str, int | list[tuple[str, int]]]:\n        return await asyncio.to_thread(self._stats_summary_sync)\n\n    def _execute(self, sql: str, params: tuple) -> None:\n        with self._connect() as db:\n            db.execute(sql, params)\n            db.commit()\n\n    def _fetchall(self, sql: str, params: tuple) -> list[sqlite3.Row]:\n        with self._connect() as db:\n            return list(db.execute(sql, params).fetchall())\n\n    def _increment_stat_sync(self, joke_hash: str, field: str) -> None:\n        with self._connect() as db:\n            db.execute("INSERT OR IGNORE INTO statistics(joke_hash) VALUES(?)", (joke_hash,))\n            db.execute(f"UPDATE statistics SET {field}={field}+1 WHERE joke_hash=?", (joke_hash,))\n            db.commit()\n\n    def _stats_summary_sync(self) -> dict:\n        with self._connect() as db:\n            users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]\n            favorites = db.execute("SELECT COUNT(*) AS c FROM favorites").fetchone()["c"]\n            sent = db.execute("SELECT COALESCE(SUM(views),0) AS c FROM statistics").fetchone()["c"]\n            cats = [(r["category"], r["requests"]) for r in db.execute("SELECT category, requests FROM category_stats ORDER BY requests DESC LIMIT 5").fetchall()]\n            return {"users": users, "favorites": favorites, "sent": sent, "popular_categories": cats}\n''',
        "services/providers/__init__.py": "",
        "services/providers/base.py": '''from __future__ import annotations\nfrom abc import ABC, abstractmethod\n\nclass JokeProvider(ABC):\n    name: str = "base"\n\n    @abstractmethod\n    async def get_joke(self, category: str | None = None, query: str | None = None) -> str:\n        pass\n''',
        "services/providers/provider1.py": '''from __future__ import annotations\nimport aiohttp\nfrom bs4 import BeautifulSoup\nfrom .base import JokeProvider\nfrom utils.text import clean_joke\n\nclass Provider1(JokeProvider):\n    name = "anekdot.ru"\n    async def get_joke(self, category: str | None = None, query: str | None = None) -> str:\n        url = "https://www.anekdot.ru/random/anekdot/"\n        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:\n            async with session.get(url, headers={"User-Agent":"Mozilla/5.0"}) as resp:\n                resp.raise_for_status()\n                html = await resp.text()\n        soup = BeautifulSoup(html, "html.parser")\n        items = soup.select("div.text")\n        if not items:\n            raise RuntimeError("Provider1 empty page")\n        return clean_joke(items[0].get_text(" "))\n''',
        "services/providers/provider2.py": '''from __future__ import annotations\nimport aiohttp\nfrom bs4 import BeautifulSoup\nfrom .base import JokeProvider\nfrom utils.text import clean_joke\n\nclass Provider2(JokeProvider):\n    name = "baneks.ru"\n    async def get_joke(self, category: str | None = None, query: str | None = None) -> str:\n        url = "https://baneks.ru/random"\n        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:\n            async with session.get(url, headers={"User-Agent":"Mozilla/5.0"}) as resp:\n                resp.raise_for_status()\n                html = await resp.text()\n        soup = BeautifulSoup(html, "html.parser")\n        item = soup.select_one("article, .anekdot, p")\n        if not item:\n            raise RuntimeError("Provider2 empty page")\n        return clean_joke(item.get_text(" "))\n''',
        "services/providers/provider3.py": '''from __future__ import annotations\nimport aiohttp\nfrom bs4 import BeautifulSoup\nfrom .base import JokeProvider\nfrom utils.text import clean_joke\n\nclass Provider3(JokeProvider):\n    name = "fallback-static"\n    async def get_joke(self, category: str | None = None, query: str | None = None) -> str:\n        fallback = "Программист приходит домой, жена говорит: купи хлеб, а если будут яйца — возьми десяток. Он возвращается с десятью батонами: яйца были."\n        if query and query.lower() not in fallback.lower():\n            return f"Анекдот по запросу '{query}': " + fallback\n        return fallback\n''',
        "services/joke_service.py": '''from __future__ import annotations
import asyncio, logging
from services.cache import JokeCache
from services.database import Database
from services.providers.provider1 import Provider1
from services.providers.provider2 import Provider2
from services.providers.provider3 import Provider3
from utils.text import clean_joke, is_valid_joke, joke_hash
logger = logging.getLogger(__name__)
class JokeService:
    def __init__(self, database: Database) -> None:
        self.db = database; self.cache = JokeCache(); self.providers = [Provider1(), Provider2(), Provider3()]
    async def get_joke(self, telegram_id: int, category: str | None = None, query: str | None = None) -> tuple[str, str]:
        await self.db.increment_category(category or "random")
        for _ in range(25):
            for provider in self.providers:
                try:
                    raw = await asyncio.wait_for(provider.get_joke(category, query), timeout=5)
                    joke = clean_joke(raw); h = joke_hash(joke)
                    if is_valid_joke(joke) and not self.cache.seen_by_user(telegram_id, h):
                        self.cache.remember(telegram_id, joke, h); await self.db.increment_stat(h, "views"); return joke, h
                except Exception as exc:
                    logger.warning("Provider %s failed: %s", provider.name, exc)
        fallback = "Не получилось найти новый анекдот в интернете, но я не сдаюсь: программист чинит баг, пока баг не начинает чинить программиста."
        h = joke_hash(fallback); self.cache.remember(telegram_id, fallback, h); await self.db.increment_stat(h, "views"); return fallback, h
    async def top(self) -> str:
        s = await self.db.stats_summary(); cats = s.get("popular_categories", []); nl = chr(10)
        cat_text = nl.join(f"• {n}: {c}" for n, c in cats) or "Пока нет данных."
        return f"Топ статистики:{nl}Пользователей: {s['users']}{nl}Отправлено: {s['sent']}{nl}Избранных: {s['favorites']}{nl}Категории:{nl}{cat_text}"
''',
        "keyboards/__init__.py": "",
        "keyboards/inline.py": '''from __future__ import annotations
from urllib.parse import quote
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def joke_keyboard(joke_hash: str, category: str = "random") -> InlineKeyboardMarkup:
    share_url = "https://t.me/share/url?url=&text=" + quote("Смешной анекдот от бота 🤣")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❤️ В избранное", callback_data=f"fav:{joke_hash}"), InlineKeyboardButton(text="🤣 Ещё анекдот", callback_data=f"more:{category}")],[InlineKeyboardButton(text="🔄 Поделиться", url=share_url)]])
def category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Про программистов", callback_data="cat:programmers"), InlineKeyboardButton(text="Про семью", callback_data="cat:family")],[InlineKeyboardButton(text="Про животных", callback_data="cat:animals"), InlineKeyboardButton(text="Чёрный юмор", callback_data="cat:dark")],[InlineKeyboardButton(text="Армия", callback_data="cat:army"), InlineKeyboardButton(text="Работа", callback_data="cat:work")],[InlineKeyboardButton(text="Студенты", callback_data="cat:students"), InlineKeyboardButton(text="Случайный", callback_data="cat:random")]])
''',
        "handlers/__init__.py": "",
        "handlers/commands.py": '''from __future__ import annotations
import io
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command, CommandObject
from keyboards.inline import category_keyboard, joke_keyboard
from services.database import Database
from services.joke_service import JokeService
router = Router()
START_TEXT = """Привет! Я бот-анекдотник 🤣
Я умею искать смешные анекдоты в интернете.

Команды:
/joke — случайный анекдот
/category — выбрать категорию
/top — лучшие анекдоты
/favorite — мои избранные
/help — помощь"""
@router.message(Command("start"))
async def start(message: Message, db: Database) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username if message.from_user else None); await message.answer(START_TEXT)
@router.message(Command("help"))
async def help_cmd(message: Message) -> None: await message.answer(START_TEXT)
@router.message(Command("joke"))
async def joke(message: Message, joke_service: JokeService) -> None:
    text, h = await joke_service.get_joke(message.from_user.id); await message.answer(text, reply_markup=joke_keyboard(h))
@router.message(Command("category"))
async def category(message: Message) -> None: await message.answer("Выберите категорию:", reply_markup=category_keyboard())
@router.message(Command("top"))
async def top(message: Message, joke_service: JokeService) -> None: await message.answer(await joke_service.top())
@router.message(Command("favorite"))
async def favorite(message: Message, db: Database) -> None:
    jokes = await db.favorites(message.from_user.id); await message.answer((chr(10) + chr(10)).join(jokes) if jokes else "В избранном пока пусто.")
@router.message(Command("search"))
async def search(message: Message, command: CommandObject, joke_service: JokeService) -> None:
    query = (command.args or "").strip()
    if not query: await message.answer("Пример: /search врач"); return
    text, h = await joke_service.get_joke(message.from_user.id, query=query); await message.answer(text, reply_markup=joke_keyboard(h))
@router.message(Command("random100"))
async def random100(message: Message, joke_service: JokeService) -> None:
    jokes = []
    for _ in range(100):
        text, _ = await joke_service.get_joke(message.from_user.id); jokes.append(text)
    separator = chr(10) + chr(10) + "---" + chr(10) + chr(10)
    await message.answer_document(BufferedInputFile(io.BytesIO(separator.join(jokes).encode()).read(), filename="100_jokes.txt"))
''',
        "handlers/callbacks.py": '''from __future__ import annotations
from aiogram import Router
from aiogram.types import CallbackQuery
from keyboards.inline import joke_keyboard
from services.database import Database
from services.joke_service import JokeService
router = Router()
@router.callback_query(lambda c: c.data and c.data.startswith("fav:"))
async def favorite_callback(callback: CallbackQuery, db: Database, joke_service: JokeService) -> None:
    h = callback.data.split(":",1)[1]; joke = joke_service.cache.get_by_hash(h)
    if not joke: await callback.answer("Анекдот уже не в памяти. Запросите новый.", show_alert=True); return
    await db.add_favorite(callback.from_user.id, joke); await db.increment_stat(h, "likes"); await callback.answer("Добавлено в избранное ❤️")
@router.callback_query(lambda c: c.data and c.data.startswith("more:"))
async def more_callback(callback: CallbackQuery, joke_service: JokeService) -> None:
    category = callback.data.split(":",1)[1]; text, h = await joke_service.get_joke(callback.from_user.id, category=category); await callback.message.answer(text, reply_markup=joke_keyboard(h, category)); await callback.answer()
@router.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def category_callback(callback: CallbackQuery, joke_service: JokeService) -> None:
    category = callback.data.split(":",1)[1]; text, h = await joke_service.get_joke(callback.from_user.id, category=category); await callback.message.answer(text, reply_markup=joke_keyboard(h, category)); await callback.answer()
''',
        "handlers/admin.py": '''from __future__ import annotations
from aiogram import Router, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from config import settings
from services.database import Database
router = Router()
def is_admin(message: Message) -> bool: return bool(message.from_user and message.from_user.id == settings.admin_id)
@router.message(Command("stats"))
async def stats(message: Message, db: Database) -> None:
    if not is_admin(message): return
    data = await db.stats_summary(); nl = chr(10); await message.answer(f"Пользователей: {data['users']}{nl}Отправлено анекдотов: {data['sent']}{nl}Избранных: {data['favorites']}{nl}Популярные категории: {data['popular_categories']}")
@router.message(Command("users"))
async def users(message: Message, db: Database) -> None:
    if not is_admin(message): return
    ids = await db.user_ids(); nl = chr(10); await message.answer(f"Пользователи ({len(ids)}):{nl}" + nl.join(map(str, ids[:100])))
@router.message(Command("broadcast"))
async def broadcast(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not is_admin(message): return
    text = (command.args or "").strip()
    if not text: await message.answer("Использование: /broadcast текст"); return
    sent = 0
    for uid in await db.user_ids():
        try: await bot.send_message(uid, text); sent += 1
        except Exception: continue
    await message.answer(f"Отправлено: {sent}")
''',
        "bot.py": '''from __future__ import annotations
import asyncio, logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from config import settings
from handlers import admin, callbacks, commands
from services.database import Database
from services.joke_service import JokeService
from keyboards.inline import joke_keyboard
from utils.logging_config import setup_logging
async def daily_publisher(bot: Bot, db: Database, joke_service: JokeService) -> None:
    while True:
        now = datetime.now(); target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if target <= now: target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        for uid in await db.user_ids():
            try:
                prefix = "Ежедневный анекдот в 09:00 🤣" + chr(10) + chr(10)
                text, h = await joke_service.get_joke(uid); await bot.send_message(uid, prefix + text, reply_markup=joke_keyboard(h))
            except Exception as exc: logging.warning("Daily publish failed for %s: %s", uid, exc)
async def main() -> None:
    setup_logging()
    if not settings.bot_token or settings.bot_token == "replace_me": raise RuntimeError("Set BOT_TOKEN in .env before starting the bot")
    bot = Bot(settings.bot_token); db = Database(settings.database_path); await db.init(); joke_service = JokeService(db)
    dp = Dispatcher(db=db, joke_service=joke_service); dp.include_router(commands.router); dp.include_router(callbacks.router); dp.include_router(admin.router)
    asyncio.create_task(daily_publisher(bot, db, joke_service)); await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
''',
        "models/__init__.py": "",
        "README.md": '''# Telegram Joke Bot

Асинхронный Telegram-бот на Python 3.12+ и aiogram 3.x. Бот ищет анекдоты через несколько providers, переключается между источниками при ошибках, хранит избранное и статистику в SQLite.

## Запуск

1. Скопируйте `.env.example` в `.env`.
2. Укажите `BOT_TOKEN` и `ADMIN_ID`.
3. Запустите:

```bash
docker-compose up -d
```

## Команды

- `/start`, `/help`
- `/joke`
- `/category`
- `/top`
- `/favorite`
- `/random100`
- `/search врач`

## Админ

- `/stats`
- `/users`
- `/broadcast текст`

## Структура

- `handlers/` — команды и callbacks
- `keyboards/` — inline клавиатуры
- `services/providers/` — источники анекдотов
- `services/database.py` — SQLite
- `services/cache.py` — in-memory cache последних 100 анекдотов
''',
    }


def _pipeline_generate_files_one_by_one(project, sprint_plan, design_system, tdd_tests):
    """Generate project files ONE BY ONE — more reliable than batch."""
    project_id = project.get("project_id", "")
    context = _build_agent_context(project, sprint_plan=sprint_plan, design_system=design_system, tdd_tests=tdd_tests)
    chat = project.get("chat_history",[])
    spec = "\n".join([m.get("content","") for m in chat if m.get("role") in ("user","assistant")]) or project.get("description","")
    enhanced_spec = f"Project: {project.get('title','')}\nDescription: {project.get('description','')}\n\nSpec:\n{spec}\n\n{context}"

    # Detect project type and get appropriate file tree
    spec_lower = enhanced_spec.lower()
    project_type, file_tree = _detect_project_type(spec_lower)
    project["_project_type"] = project_type
    project["logs"].append(f"Codex: Detected project type '{project_type}' with {len(file_tree)} files.")

    if project_type == "history_story":
        generated = _history_story_static_files(project)
        project["logs"].append(f"Codex: Using deterministic history_story generator ({len(generated)} files) to avoid AI timeouts.")
        clear_agent_status("codex")
        return {"files": generated}

    if project_type == "landing_page":
        generated = _landing_page_static_files(project)
        project["logs"].append(f"Codex: Using deterministic landing_page generator ({len(generated)} files) to avoid AI timeouts.")
        clear_agent_status("codex")
        return {"files": generated}

    if project_type == "telegram_bot":
        generated = _telegram_bot_static_files(project)
        project["logs"].append(f"Codex: Using deterministic telegram_bot generator ({len(generated)} files) to avoid AI timeouts.")
        clear_agent_status("codex")
        return {"files": generated}

    # Sort files: config/data files first, then logic, then UI, then docs
    priority_order = []
    for kw in ["requirements", "config", ".env", "main.py"]:
        for f in file_tree:
            p = f["path"]
            if kw in p and p not in priority_order:
                priority_order.append(p)
    for f in file_tree:
        p = f["path"]
        if p not in priority_order:
            priority_order.append(p)

    file_count = 0
    generated = {}
    error_prefixes = ("AI service timed out", "AI service error", "AI error:", "AI response error", "This model only supports text", "Invalid API key")

    # Pre-defined React boilerplate to avoid AI timeouts on config files
    react_boilerplate = {
        "frontend/package.json": json.dumps({"name": "frontend","type": "module","private": True,"version": "1.0.0","scripts": {"dev": "vite","build": "vite build","preview": "vite preview"},"dependencies": {"react": "^18.2.0","react-dom": "^18.2.0"},"devDependencies": {"@vitejs/plugin-react": "^4.2.0","autoprefixer": "^10.4.16","postcss": "^8.4.32","tailwindcss": "^3.4.0","vite": "^5.0.8"}}, indent=2),
        "frontend/vite.config.js": "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n\nexport default defineConfig({\n  plugins: [react()],\n  server: {\n    port: 3000,\n    proxy: {\n      '/api': 'http://localhost:8000'\n    }\n  }\n});\n",
        "frontend/tailwind.config.js": "/** @type {import('tailwindcss').Config} */\nexport default {\n  content: ['./index.html', './src/**/*.{js,jsx}'],\n  theme: { extend: {} },\n  plugins: [],\n};\n",
        "frontend/postcss.config.js": "export default {\n  plugins: {\n    tailwindcss: {},\n    autoprefixer: {},\n  },\n};\n",
        "frontend/index.html": '<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n    <title>React App</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.jsx"></script>\n  </body>\n</html>\n',
        "frontend/src/index.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
        ".env": "BACKEND_URL=http://localhost:8000\nFRONTEND_PORT=3000\n",
    }

    for fpath in priority_order:
        f = next((x for x in file_tree if x["path"] == fpath), None)
        purpose = f["purpose"] if f else ""
        if not purpose:
            purpose = fpath.split("/")[-1]
        set_agent_status("codex", "working", f"Generating {fpath}", project_id)
        project["logs"].append(f"Codex: Generating {fpath}...")

        # Use pre-defined boilerplate for React config files
        if fpath in react_boilerplate:
            content = react_boilerplate[fpath]
            project["logs"].append(f"Codex: Using boilerplate for {fpath} ({len(content)} chars)")
            generated[fpath] = content
            file_count += 1
            continue

        try:
            content = _codex_generate_single_file(
                project["title"], enhanced_spec, fpath, purpose,
                list(generated.keys()), len(generated) == 0, project_type
            )
        except Exception as e:
            project["logs"].append(f"Codex: Error generating {fpath} — {e}")
            continue
        if content.strip().startswith(error_prefixes):
            project["logs"].append(f"Codex: Failed to generate {fpath} — {content.strip()[:100]}")
            continue
        generated[fpath] = content
        file_count += 1
        project["logs"].append(f"Codex: Generated {fpath} ({len(content)} chars)")

    clear_agent_status("codex")
    project["logs"].append(f"Codex: {file_count} files generated one-by-one")
    return {"files": generated}


def _fix_requirements_txt(target_path, log_func=None, project_type="simple"):
    """Auto-fix requirements.txt based on actual imports in generated Python files."""
    def log(msg):
        if log_func: log_func(msg)
    if project_type == "telegram_bot":
        log("[PostProcess]: Preserved deterministic telegram_bot requirements.txt")
        return
    req_path = os.path.join(target_path, "requirements.txt")
    if not os.path.exists(req_path):
        return
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            current = f.read()
    except Exception:
        return
    needed_packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "pydantic": "pydantic",
        "sqlalchemy": "sqlalchemy",
        "PyMuPDF": "PyMuPDF",
        "fitz": "PyMuPDF",
        "pdfplumber": "pdfplumber",
        "pytesseract": "pytesseract",
        "Pillow": "Pillow",
        "jinja2": "jinja2",
        "Jinja2": "jinja2",
        "aiofiles": "aiofiles",
        "dotenv": "python-dotenv",
        "load_dotenv": "python-dotenv",
        "multipart": "python-multipart",
        "starlette": "starlette",
        "sqlite3": "",  # stdlib
        "docker": "docker",
        "httpx": "httpx",
        "pytest": "pytest",
        "redis": "redis",
        "jwt": "pyjwt",
        "oauth": "requests-oauthlib",
        "requests": "requests",
        "numpy": "",  # remove numpy as it's rarely actually needed
        "pandas": "",
        "scipy": "",
        "matplotlib": "",
        "sklearn": "",
        "scikit": "",
    }
    imports_found = set()
    for root, _dirs, files in os.walk(target_path):
        for fname in files:
            if fname.endswith(".py"):
                try:
                    with open(os.path.join(root, fname), "r", encoding="utf-8") as f:
                        content = f.read()
                    for keyword, pkg in needed_packages.items():
                        if keyword in content and pkg:
                            imports_found.add(pkg)
                except Exception:
                    pass
    # Rebuild requirements.txt from scratch based on actual imports
    if imports_found:
        new_content = "\n".join(sorted(imports_found)) + "\n"
        if new_content.strip() != current.strip():
            try:
                with open(req_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                log(f"[PostProcess]: Rewrote requirements.txt ({len(imports_found)} packages from code analysis)")
            except Exception:
                pass


def _pipeline_codex_fix(project, generated_data, target_path, feedback):
    project_id = project.get("project_id", "")
    project["logs"].append("Codex fixing issues based on review feedback...")
    set_agent_status("codex", "working", "Fixing review issues", project_id)

    if project.get("_project_type") in ("history_story", "landing_page", "telegram_bot"):
        project["logs"].append(f"Codex: {project.get('_project_type')} uses deterministic generator; skipping AI fix to avoid timeout.")
        clear_agent_status("codex")
        return generated_data

    if not _HAS_OPENCODE:
        _set_project_status(project, "blocked", reason="OpenCode bridge unavailable during repair")
        project["logs"].append("[OpenCode]: Cannot fix review issues because OpenCode bridge is not available.")
        clear_agent_status("codex")
        return generated_data

    try:
        oc_bridge = _get_oc_bridge()
        if not oc_bridge.ensure_running(workdir=target_path):
            _set_project_status(project, "blocked", reason="OpenCode unavailable during repair")
            project["logs"].append("[OpenCode]: Cannot fix review issues because OpenCode is unavailable. Complete OpenCode login and retry.")
            clear_agent_status("codex")
            return generated_data
        result = oc_bridge.execute_fix_task(
            project_dir=target_path,
            issues=feedback,
            log_callback=lambda msg: project["logs"].append(msg),
        )
        if not result.get("success"):
            _set_project_status(project, "blocked", reason="OpenCode repair task failed")
            project["logs"].append(f"[OpenCode]: Fix task failed: {result.get('error', 'unknown')}")
            clear_agent_status("codex")
            return generated_data
        refreshed = {"files": {}, "file_tree": generated_data.get("file_tree", {}) if isinstance(generated_data, dict) else {}}
        for root, _dirs, files in os.walk(target_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, target_path)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        refreshed["files"][rel] = f.read()
                except Exception:
                    pass
        project["logs"].append(f"[OpenCode]: Applied review fixes (session {result.get('session_id', '?')}).")
        clear_agent_status("codex")
        return refreshed
    except Exception as e:
        _set_project_status(project, "blocked", reason="OpenCode repair exception")
        project["logs"].append(f"[OpenCode]: Fix task error: {e}")
        clear_agent_status("codex")
        return generated_data

    files_content = _read_project_files(target_path, generated_data)
    code_dump = "\n\n".join([f"=== {k} ===\n{v}" for k, v in files_content.items()])

    context = _build_agent_context(project)
    fix_prompt = (
        "You are Codex, a senior developer. The following code has been reviewed and issues were found.\n"
        f"Review feedback:\n{feedback}\n\n"
        f"Current code:\n{code_dump}\n\n"
        f"Fix ALL issues mentioned above. {_CRITICAL_RULES}\n"
        "- All tests must be runnable with real assertions matching actual model fields\n"
        "- Tests must only test endpoints that actually exist in the API code\n"
        "Return the COMPLETE updated files as JSON with file paths as keys and full file contents as values.\n"
        "Return ONLY valid JSON, no other text."
    )

    provider, model = get_agent_provider_model("codex")
    agent_data = agent_configs.get("codex", {})
    try:
        raw = ask_studio_ai_with_history(
            provider=provider, model_name=model,
            system_prompt="You are Codex. Return ONLY valid JSON with file paths as keys and full file content as values.",
            chat_history=[{"role": "user", "content": fix_prompt}],
            temperature=agent_data.get("temperature", 0.2),
            max_tokens=8192,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        fixed = json.loads(cleaned)
    except Exception:
        fixed = None

    if fixed and isinstance(fixed, dict):
        for fname, content in fixed.items():
            content = content.replace("\\n", "\n").replace("\\r\\n", "\n").replace("\\r", "\n").replace("\r\n", "\n").replace("\r", "\n")
            fpath = os.path.join(target_path, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
        # Merge fixes into existing files — don't replace the whole dict
        generated_data["files"].update(fixed)
        project["logs"].append(f"Codex: Applied fixes to {len(fixed)} files")
    else:
        project["logs"].append("Codex: Failed to parse fix output, code unchanged")

    clear_agent_status("codex")
    return generated_data


def _repair_product_judge_objections(project: dict, target_path: str, project_id: str) -> bool:
    """Route Product Judge issues through the existing snapshot-aware OpenCode repair path."""
    issues = [issue for issue in project.get("issues", []) if isinstance(issue, dict) and issue.get("source") == "product_judge" and issue.get("status", "open") == "open"]
    if not issues:
        return False
    attempts = int(project.get("_product_judge_repair_attempts", 0))
    if attempts >= 2:
        project.setdefault("logs", []).append("[PRODUCT JUDGE] Repair limit reached; blocking objection remains open.")
        return False
    project["_product_judge_repair_attempts"] = attempts + 1
    project["_repair_active"] = True
    _set_project_status(project, "repairing")
    provider, model = get_agent_provider_model("codex")
    engine = QAEngine(
        project=project,
        target_path=target_path,
        project_id=project_id,
        provider=provider,
        model=model,
        temperature=agent_configs.get("codex", {}).get("temperature", 0.2),
        state_callback=lambda state, reason="": _set_project_status(project, state, reason=reason),
    )
    try:
        repair = engine._request_opencode_fix(
            issues,
            json.dumps(issues, ensure_ascii=False),
            {"source": "product_judge", "issues": issues, "required_followup": "Run full QA, fresh browser evidence, Product Judge, and final audit after a changed snapshot."},
        )
    finally:
        project["_repair_active"] = False
        for entry in engine.logs:
            if entry not in project.setdefault("logs", []):
                project["logs"].append(entry)
    if not repair or not repair.get(qa_engine_module.OPENCODE_FIX_APPLIED):
        project.setdefault("logs", []).append("[PRODUCT JUDGE] OpenCode did not produce a verified file change.")
        return False
    for issue in issues:
        issue["status"] = "verification_pending"
    project.setdefault("logs", []).append("[PRODUCT JUDGE] Repair changed files; restarting full QA before browser and judge re-verification.")
    _run_qa_only(project_id, target_path)
    return True


def _run_final_delivery_audit(project: dict, target_path: str, project_id: str, qa_result: dict | None = None) -> tuple[bool, list[str]]:
    """Independent final completion gate and delivery report generation."""
    contract_ok, contract_errors = _project_contract_ready(project)
    project["latest_qa_result"] = qa_result or {}
    report = run_final_delivery_audit(project, target_path, qa_result=qa_result or {})
    try:
        report_path = write_delivery_report(project, target_path)
        project.setdefault("logs", []).append(f"[FINAL AUDIT] Delivery report written: {report_path}")
    except Exception as e:
        report.setdefault("checks", []).append({"name": "delivery_report_write", "status": "failed", "evidence": {"error": str(e)}})
    errors = list(contract_errors) if not contract_ok else []
    for check in report.get("checks", []):
        if check.get("status") == "failed":
            errors.append(f"{check.get('name')}: {check.get('evidence')}")
    if report.get("status") == "blocked_by_credentials":
        errors.append("Final audit blocked by missing credentials")
    ok = not errors and report.get("status") == "passed"
    project.setdefault("logs", []).append(f"[FINAL AUDIT] {'Passed' if ok else 'Failed'}")
    return ok, errors


def async_studio_production_pipeline(project_id: str):
    project = active_projects[project_id]
    project.setdefault("logs", [])
    project["logs"].append(f"[DEBUG] Pipeline thread started: {_threading.current_thread().name}")
    detected_type, _ = _detect_project_type((project.get("title", "") + "\n" + project.get("description", "")).lower())
    project["_project_type"] = detected_type

    try:
        if _abort_if_cancelled(project, "pipeline start"):
            return

        _ensure_project_contract(project)
        if _apply_requirement_gap_blockers(project):
            _save_projects_state()
            return

        # ── Phase 1: Alex (PM) — Sprint Plan ──────────────────────────────────
        if _should_execute_phase(project, "planning"):
            _save_phase(project, "planning")
            _set_project_status(project, "planning")
            set_agent_status("alex", "working", "Creating sprint plan", project_id)
            set_agent_status("maya", "idle")
            set_agent_status("elena", "idle")
            set_agent_status("bugcatcher", "idle")
            set_agent_status("codex", "idle")
            if project.get("_project_type") in ("landing_page", "telegram_bot"):
                project["logs"].append(f"Alex: {project.get('_project_type')} detected — using deterministic sprint plan to avoid AI planning timeout.")
                sprint_plan = _default_sprint_plan(project)
            else:
                project["logs"].append("Alex (PM) analyzing spec and creating sprint plan...")
                sprint_plan = _pipeline_alex_sprint_plan(project)
            project["plans"] = sprint_plan
            clear_agent_status("alex")
        else:
            sprint_plan = project.get("plans", {})
            project["logs"].append("[Resume] Phase 1 (planning) skipped — already completed")
        # ──────────────────────────────────────────────────────────────────────────

        if _abort_if_cancelled(project, "after planning"):
            return

        # ── Phase 1b: Ask clarifying questions if needed ─────────────────────────
        if _should_execute_phase(project, "planning") and project.get("_project_type") in ("history_story",) and not project.get("_clarifications_done"):
            project["logs"].append("Alex checking if clarifications are needed...")
            spec_text = project.get("description", "") + "\n" + sprint_plan if isinstance(sprint_plan, str) else str(sprint_plan)
            asked = _pipeline_ask_questions(project_id, project, spec_text, "alex")
            if asked:
                project["_resume_status"] = "designing"
                _set_project_status(project, "awaiting_input")
                _save_projects_state()
                # Wait loop — will resume from Phase 2 when answers arrive
                while _check_questions_answered(project_id) is False:
                    import time as _time
                    _time.sleep(2)
                project["logs"].append("All clarifications received. Continuing pipeline...")
                _save_projects_state()
        elif not _should_execute_phase(project, "planning"):
            project["logs"].append("[Resume] Phase 1b (questions) skipped — already completed")
        # ──────────────────────────────────────────────────────────────────────────

        if _abort_if_cancelled(project, "after clarifications"):
            return

        # ── Phase 2: Elena (Designer) — Design System ──────────────────────────
        if _should_execute_phase(project, "designing"):
            _save_phase(project, "designing")
            _set_project_status(project, "designing")
            set_agent_status("elena", "working", "Creating design system", project_id)
            if project.get("_project_type") in ("landing_page", "telegram_bot"):
                project["logs"].append(f"Elena: {project.get('_project_type')} detected — using deterministic design system.")
                design_system = _default_design_system()
            else:
                project["logs"].append("Elena (Designer) creating design system and component hierarchy...")
                design_system = _pipeline_elena_design(project, sprint_plan)
            project["design_system"] = design_system
            clear_agent_status("elena")
        else:
            design_system = project.get("design_system", {})
            project["logs"].append("[Resume] Phase 2 (design) skipped — already completed")

        if _abort_if_cancelled(project, "after design"):
            return

        # ── Phase 3: BugCatcher (QA/TDD) — Tests Before Code ──────────────────
        if _should_execute_phase(project, "testing"):
            _save_phase(project, "testing")
            _set_project_status(project, "testing")
            set_agent_status("bugcatcher", "working", "Writing TDD test specs", project_id)
            if project.get("_project_type") in ("landing_page", "telegram_bot"):
                project["logs"].append(f"BugCatcher: {project.get('_project_type')} detected — using lightweight static checks.")
                tdd_tests = []
            else:
                project["logs"].append("BugCatcher writing test specifications before code (TDD mode)...")
                tdd_tests = _pipeline_bugcatcher_tdd(project, sprint_plan, design_system)
            project["tdd_tests"] = tdd_tests
            clear_agent_status("bugcatcher")
        else:
            tdd_tests = project.get("tdd_tests", [])
            project["logs"].append("[Resume] Phase 3 (TDD tests) skipped — already completed")

        if _abort_if_cancelled(project, "after test planning"):
            return

        # ── Phase 4: Codex (Developer) — Code Generation ──────────────────────
        _set_project_status(project, "coding")
        set_agent_status("codex", "working", "Generating code", project_id)

        # Determine target path early
        codex_save_path = agent_configs.get("codex", {}).get("save_path", "")
        project_dir_name = project['title'].replace(' ', '_').lower()
        if codex_save_path:
            target_path = os.path.join(codex_save_path, project_dir_name)
        else:
            target_path = os.path.join(BASE_DIR, "generated_projects", project_dir_name)
        project["target_path"] = target_path

        if _should_execute_phase(project, "coding"):
            _save_phase(project, "coding")
            if project.get("cancel_requested"):
                _set_project_status(project, "cancelled", force=True, reason="before generation")
                return
            os.makedirs(target_path, exist_ok=True)
            project_state.persist_project_state(project, target_path)

            detected_type, _detected_tree = _detect_project_type((project.get("title", "") + "\n" + project.get("description", "")).lower())
            if detected_type in ("history_story", "landing_page", "telegram_bot"):
                project["_project_type"] = detected_type

            # OpenCode is the mandatory coding backend. Do not silently fall back to text-only LLM generation.
            _oc_used = False
            if not _HAS_OPENCODE:
                _set_project_status(project, "blocked", reason="OpenCode bridge unavailable")
                project["logs"].append("[System]: OpenCode bridge is not available. Code generation was not started.")
                clear_agent_status("codex")
                return

            if _HAS_OPENCODE:
                try:
                    backend_ok, backend_error = _backend_health_available()
                    if not backend_ok:
                        _set_project_status(project, "blocked", reason="backend health check failed")
                        project["logs"].append(f"[System]: FreelancerStudio backend health check failed before OpenCode launch: {backend_error}")
                        project["logs"].append("[System]: Code generation was not started. Start backend on http://127.0.0.1:8080 and retry.")
                        clear_agent_status("codex")
                        return
                    oc_bridge = _get_oc_bridge()
                    if oc_bridge.ensure_running(workdir=target_path):
                        project["logs"].append("OpenCode bridge active. Delegating code generation to OpenCode...")
                        ctx = _build_agent_context(project, sprint_plan, design_system, tdd_tests)
                        spec_json = json.dumps(project.get("project_spec", {}), ensure_ascii=False, indent=2)
                        qa_plan_json = json.dumps(project.get("qa_plan", {}), ensure_ascii=False, indent=2)
                        ac_text = acceptance_summary(project)
                        task_spec = (
                            f"PROJECT TITLE: {project.get('title', '')}\n\n"
                            f"PROJECT DESCRIPTION:\n{project.get('description', '')}\n\n"
                            f"STRUCTURED PROJECT SPECIFICATION:\n{spec_json}\n\n"
                            f"ACCEPTANCE CRITERIA:\n{ac_text}\n\n"
                            f"QA PLAN:\n{qa_plan_json}\n\n"
                            f"AGENT CONTEXT:\n{ctx}\n\n"
                            "Implement the complete project on disk. It must be runnable, not placeholder code.\n"
                            "Mandatory delivery contract:\n"
                            "- Put a README.md in the project root with exact install, run, and test commands.\n"
                            "- For Python apps, provide root requirements.txt and a root main.py/bot.py/app.py entrypoint when applicable.\n"
                            "- Use 'python -m uvicorn ...' in docs instead of relying on uvicorn.exe being on PATH.\n"
                            "- For Python tests, run 'python -m pytest -q' from the project root and fix failures.\n"
                            "- For JS/Vite projects, run 'npm install' and 'npm run build' from the project root and fix failures.\n"
                            "- Do not include placeholder/stub code, fake tests, stale audit notes, or documentation that contradicts implementation."
                        )
                        oc_result = oc_bridge.execute_coding_task(
                            project_dir=target_path,
                            task_spec=task_spec,
                            autonomous=project.get("autonomous_mode", True),
                            log_callback=lambda msg: project["logs"].append(msg),
                        )
                        if oc_result.get("session_id"):
                            project["opencode_session_id"] = oc_result.get("session_id")
                        if oc_result.get("cancelled") or project.get("cancel_requested"):
                            _set_project_status(project, "cancelled", force=True, reason="OpenCode generation cancelled")
                            clear_agent_status("codex")
                            return
                        if oc_result["success"]:
                            project["logs"].append(f"OpenCode task completed (session {oc_result.get('session_id','?')})")
                            project["logs"].append(f"Summary: {oc_result.get('summary','')[:200]}")
                            # Read back generated files from disk
                            generated_data = {"files": {}, "file_tree": {}}
                            for root, dirs, files in os.walk(target_path):
                                for fname in files:
                                    fpath = os.path.join(root, fname)
                                    rel = os.path.relpath(fpath, target_path)
                                    try:
                                        with open(fpath, "r", encoding="utf-8") as f:
                                            generated_data["files"][rel] = f.read()
                                    except:
                                        pass
                            _oc_used = True
                            _mark_generation_finished(project, True)
                            project["logs"].append(f"OpenCode: {len(generated_data['files'])} files found in {target_path}")
                        else:
                            _mark_generation_finished(project, False)
                            _set_project_status(project, "blocked", reason="OpenCode generation failed")
                            project["logs"].append(f"[OpenCode]: Generation failed: {oc_result.get('error','unknown')}")
                            project["logs"].append("[OpenCode]: Check Settings -> AI Provider. If the error mentions auth, open OpenCode Login/Web. If it mentions DEGRADED/function calls, switch/apply an OpenCode-compatible coding model such as OpenAI.")
                            clear_agent_status("codex")
                            return
                    else:
                        _mark_generation_finished(project, False)
                        _set_project_status(project, "blocked", reason="OpenCode server unavailable")
                        project["logs"].append("[OpenCode]: Server unavailable. Code generation was not started.")
                        project["logs"].append("[OpenCode]: Open Settings -> AI Provider -> Open OpenCode Login/Web, complete login, then retry generation.")
                        clear_agent_status("codex")
                        return
                except Exception as oc_e:
                    _mark_generation_finished(project, False)
                    _set_project_status(project, "blocked", reason="OpenCode exception")
                    project["logs"].append(f"[OpenCode]: Error: {oc_e}")
                    project["logs"].append("[OpenCode]: Code generation requires OpenCode. Complete OpenCode login and retry.")
                    clear_agent_status("codex")
                    return

            if not _oc_used:
                _mark_generation_finished(project, False)
                _set_project_status(project, "blocked", reason="OpenCode generated no files")
                project["logs"].append("[OpenCode]: No files were generated. Code generation requires OpenCode.")
                clear_agent_status("codex")
                return

            if not generated_data or "error" in generated_data:
                _mark_generation_finished(project, False)
                _set_project_status(project, "failed", reason="generation data invalid")
                project["logs"].append(f"Codex failed to generate project: {generated_data.get('error','unknown error')}. Process aborted.")
                clear_agent_status("codex")
                return

            # Write files to disk (only needed for AI mode; OpenCode writes directly)
            if not _oc_used:
                def _normalize_content(text):
                    text = text.replace("\\n", "\n").replace("\\r\\n", "\n").replace("\\r", "\n")
                    return text.replace("\r\n", "\n").replace("\r", "\n")

                file_count = 0
                error_prefixes = ("AI service timed out", "AI service error", "AI error:", "AI response error", "This model only supports text", "Invalid API key")
                for filename, content in generated_data.get("files", {}).items():
                    content = _normalize_content(content)
                    if content.strip().startswith(error_prefixes):
                        project["logs"].append(f"Codex: Skipped {filename} — AI returned error instead of code")
                        continue
                    file_path = os.path.join(target_path, filename)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    try:
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        file_count += 1
                    except Exception as e:
                        project["logs"].append(f"Codex: Failed to write {filename}: {e}")

                project["logs"].append(f"Codex: {file_count} files written to {target_path}")

                # Write TDD tests alongside generated code
                if tdd_tests:
                    tests_dir = os.path.join(target_path, "tests")
                    os.makedirs(tests_dir, exist_ok=True)
                    for tf in tdd_tests:
                        fname = tf.get("filename","test_unknown.py")
                        content = tf.get("content","")
                        if content:
                            content = _normalize_content(content)
                            tpath = os.path.join(target_path, fname)
                            os.makedirs(os.path.dirname(tpath), exist_ok=True)
                            with open(tpath, "w", encoding="utf-8") as f:
                                f.write(content)

            project["target_path"] = target_path
            project_state.persist_project_state(project, target_path)

            # Auto-fix syntax errors and requirements before review
            _post_process_code(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))
            _fix_requirements_txt(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))
        else:
            project["logs"].append("[Resume] Phase 4 (coding) skipped — already completed")
            # Read existing files from disk
            generated_data = {"files": {}, "file_tree": {}}
            if os.path.exists(target_path):
                for root, dirs, files in os.walk(target_path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, target_path)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                generated_data["files"][rel] = f.read()
                        except:
                            pass
            project["target_path"] = target_path
            project_state.persist_project_state(project, target_path)
            _mark_generation_finished(project, bool(generated_data.get("files")))

        clear_agent_status("codex")

        if _abort_if_cancelled(project, "after generation"):
            return

        # ── Phase 5: Review Loop (BugCatcher → Sentinel → Lupa → Codex) ──────
        if _should_execute_phase(project, "review"):
            _save_phase(project, "review")
            MAX_REVIEW_ITERATIONS = 2
            all_clear = False
            project["pipeline_iteration"] = 0
            feedback_history = []

            def _try_opencode_review(review_type, label, context):
                """Run review via OpenCode if available, else fall back to AI."""
                if project.get("_project_type") in ("landing_page", "telegram_bot"):
                    project["logs"].append(f"{label}: {project.get('_project_type')} deterministic generator — skipping AI review.")
                    return {"result": f"Skipped for deterministic {project.get('_project_type')} generation.", "has_issues": False}
                if _HAS_OPENCODE and project.get("_project_type") not in ("history_story", "landing_page", "telegram_bot"):
                    try:
                        oc_bridge = _get_oc_bridge()
                        if oc_bridge.ensure_running():
                            project["logs"].append(f"{label} reviewing via OpenCode...")
                            oc_r = oc_bridge.execute_review_task(target_path, review_type, context)
                            if oc_r["success"]:
                                summary = oc_r.get("summary", "")
                                has_issues = "VERDICT: FAIL" in summary.upper() or "FAIL" in summary.upper()
                                return {"result": summary, "has_issues": has_issues}
                    except Exception as e:
                        project["logs"].append(f"{label} OpenCode review failed: {e}. Falling back to AI.")
                return _run_review_agent(review_type, label, project, target_path, generated_data, iteration, context)

            for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
                if _abort_if_cancelled(project, f"review iteration {iteration}"):
                    return
                project["pipeline_iteration"] = iteration
                _set_project_status(project, f"review_iteration_{iteration}")
                project["logs"].append(f"\n{'='*50}")
                project["logs"].append(f"REVIEW ITERATION {iteration}/{MAX_REVIEW_ITERATIONS}")
                project["logs"].append(f"{'='*50}")

                feedback_context = "\n\n".join(feedback_history[-2:]) if feedback_history else ""

                # Phase 5a: BugCatcher (QC Review)
                bc = _try_opencode_review("bugcatcher", "BugCatcher", feedback_context)
                project["bugcatcher_review_v" + str(iteration)] = bc["result"]
                append_agent_review_issues(project, "bugcatcher", bc["result"], iteration)

                # Phase 5b: Sentinel (Security Review)
                sentinel_cfg = agent_configs.get("sentinel", {})
                sentinel_enabled = sentinel_cfg.get("enabled", DEFAULT_AGENTS["sentinel"]["enabled"])
                if sentinel_enabled:
                    sn = _try_opencode_review("sentinel", "Sentinel", feedback_context)
                    project["sentinel_review_v" + str(iteration)] = sn["result"]
                    append_agent_review_issues(project, "sentinel", sn["result"], iteration)
                else:
                    sn = {"has_issues": False, "result": ""}

                # Phase 5c: Lupa (Code Review)
                lupa_cfg = agent_configs.get("lupa", {})
                lupa_enabled = lupa_cfg.get("enabled", DEFAULT_AGENTS["lupa"]["enabled"])
                if lupa_enabled:
                    lp = _try_opencode_review("lupa", "Lupa", feedback_context)
                    project["lupa_review_v" + str(iteration)] = lp["result"]
                    append_agent_review_issues(project, "lupa", lp["result"], iteration)
                else:
                    lp = {"has_issues": False, "result": ""}

                # Check if any reviewer found issues
                any_issues = bc["has_issues"] or sn["has_issues"] or lp["has_issues"]

                if not any_issues:
                    project["logs"].append("\n✅ All reviews passed! No issues found.")
                    all_clear = True
                    break

                # Collect feedback and send to Codex for fixes
                combined = []
                if bc["has_issues"]:
                    combined.append("=== BUGCATCHER FEEDBACK ===\n" + bc["result"])
                if sn["has_issues"]:
                    combined.append("=== SENTINEL FEEDBACK ===\n" + sn["result"])
                if lp["has_issues"]:
                    combined.append("=== LUPA FEEDBACK ===\n" + lp["result"])

                full_feedback = "\n\n".join(combined)
                feedback_history.append(full_feedback)
                project["logs"].append(f"\n⚠️ Issues found in iteration {iteration}. Sending to Codex for fixes...")
                _set_project_status(project, "repairing")
                generated_data = _pipeline_codex_fix(project, generated_data, target_path, full_feedback)
                if _abort_if_cancelled(project, f"review fix iteration {iteration}"):
                    return
                _set_project_status(project, "review_iteration_" + str(iteration))
                _post_process_code(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))
                _fix_requirements_txt(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))

                if iteration == MAX_REVIEW_ITERATIONS:
                    project["logs"].append(f"\n⚠️ Max review iterations ({MAX_REVIEW_ITERATIONS}) reached. Proceeding with current state.")
        else:
            project["logs"].append("[Resume] Phase 5 (review) skipped — already completed")

        if _abort_if_cancelled(project, "before QA"):
            return

        # ── Phase 6: BugCatcher (QA) — Multi-stage Verification ───────────────
        if _should_execute_phase(project, "qa"):
            _save_phase(project, "qa")
            _set_project_status(project, "verifying")
            _mark_qa_passed(project, False)
            _mark_final_audit_passed(project, False)
            set_agent_status("bugcatcher", "working", "Running QA verification", project_id)
            project["logs"].append("BugCatcher deploying multi-stage QA pipeline with auto-verification...")

            provider, model = get_agent_provider_model("codex")
            agent_data = agent_configs.get("codex", {})
            engine = QAEngine(
                project=project,
                target_path=target_path,
                project_id=project_id,
                provider=provider,
                model=model,
                temperature=agent_data.get("temperature", 0.2),
                state_callback=lambda state, reason="": _set_project_status(project, state, reason=reason),
            )
            qa_result = engine.run()
            clear_agent_status("bugcatcher")

            if _abort_if_cancelled(project, "after QA"):
                return

            for log in engine.logs:
                if log not in project["logs"]:
                    project["logs"].append(log)

            # Post-process any files QA may have fixed
            _post_process_code(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))
            _fix_requirements_txt(target_path, lambda msg: project["logs"].append(msg), project.get("_project_type", "simple"))

            if qa_result.get("manual_steps"):
                project["manual_steps"] = qa_result["manual_steps"]
                project["needs_user_input"] = True

            project["logs"].append(f"\n--- QA FINAL REPORT ---")
            project["logs"].append(f"Rounds: {qa_result.get('rounds_completed',0)} | Errors: {qa_result.get('total_errors',0)}")

            if qa_result.get("success"):
                _mark_qa_passed(project, True)
                _set_project_status(project, "final_audit")

                if _abort_if_cancelled(project, "before final audit"):
                    return

                final_ok, final_errors = _run_final_delivery_audit(project, target_path, project_id, qa_result=qa_result)
                if _abort_if_cancelled(project, "after final audit"):
                    return
                if final_ok:
                    _mark_final_audit_passed(project, True)
                    _save_phase(project, "product_judge")
                    _set_project_status(project, "product_judge")
                    judge_ok, judge_errors = _run_product_judge_stage(project, qa_result)
                    if _abort_if_cancelled(project, "after product judge"):
                        return
                    if judge_ok:
                        _mark_product_judge_passed(project, True)
                    else:
                        _mark_product_judge_passed(project, False)
                        project["product_judge_errors"] = judge_errors
                        _set_project_status(project, "failed_qa", reason="product judge objections")
                        project["logs"].append("Product Judge found unresolved objections. Project is not completed.")
                        return
                    if _set_project_status(project, "completed"):
                        project["logs"].append("ALL VERIFICATIONS PASSED! Project fully verified and ready for delivery!")
                    else:
                        _set_project_status(project, "failed_qa", reason="completion gate blocked")
                else:
                    _mark_final_audit_passed(project, False)
                    project["final_audit_errors"] = final_errors
                    if _repair_product_judge_objections(project, target_path, project_id):
                        return
                    _set_project_status(project, "failed_qa", reason="final audit failed")
                    project["logs"].append("Final delivery audit failed. Project is not completed.")
            elif qa_result.get("needs_credentials"):
                _mark_qa_passed(project, False)
                _set_project_status(project, "needs_credentials")
                project["manual_steps"] = qa_result.get("manual_steps", [])
                project["logs"].append("Project needs external credentials before verification can complete.")
            elif qa_result.get("needs_human_input"):
                _mark_qa_passed(project, False)
                _set_project_status(project, "needs_human_input")
                project["manual_steps"] = qa_result.get("manual_steps", [])
                project["logs"].append("Project needs human clarification before verification can continue.")
            elif qa_result.get("manual_steps"):
                _mark_qa_passed(project, False)
                _set_project_status(project, "needs_user_input")
                project["logs"].append("Project needs manual intervention for some dependencies.")
            else:
                _mark_qa_passed(project, False)
                _set_project_status(project, "failed_qa")
                project["logs"].append("QA failed after all rounds.")
                project["qa_errors"] = qa_result.get("errors", [])
        else:
            project["logs"].append("[Resume] Phase 6 (QA) skipped — already completed")
    finally:
        for aid in ["alex", "maya", "elena", "bugcatcher", "codex", "goldie", "sentinel", "lupa", "product_judge"]:
            clear_agent_status(aid)
        if _HAS_OPENCODE:
            try:
                stop_opencode()
            except Exception:
                pass
        _save_projects_state()


@app.post("/api/projects/{project_id}/retry")
def retry_project_qa(project_id: str, background_tasks: BackgroundTasks):
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    if project.get("status") in ("completed", "cancelled"):
        raise HTTPException(400, f"Cannot retry QA for project in terminal state: {project.get('status')}")
    if not _set_project_status(project, "verifying"):
        _save_projects_state()
        raise HTTPException(400, "Invalid project state transition to verifying")
    project["logs"].append("🔄 User confirmed manual steps. Retrying QA verification...")
    project.pop("needs_user_input", None)
    project.pop("manual_steps", None)
    _mark_qa_passed(project, False)
    _mark_final_audit_passed(project, False)

    dir_name = project['title'].replace(' ', '_').lower()
    target_path = os.path.join(BASE_DIR, "generated_projects", dir_name)

    if not os.path.exists(target_path):
        # fallback: try old naming with uuid prefix
        old_name = f"{project_id}_{dir_name}"
        old_path = os.path.join(BASE_DIR, "generated_projects", old_name)
        if os.path.exists(old_path):
            target_path = old_path
        else:
            _set_project_status(project, "failed", reason="project directory missing")
            project["logs"].append("Project source directory not found. Cannot retry.")
        return {"status": "error", "message": "Source directory missing"}

    background_tasks.add_task(_run_qa_only, project_id, target_path)
    return {"status": "retrying", "message": "QA verification restarted."}


def _run_qa_only(project_id: str, target_path: str):
    project = active_projects[project_id]
    if _abort_if_cancelled(project, "qa retry start"):
        return
    agent_data = agent_configs.get("codex", {})
    provider, model = get_agent_provider_model("codex")
    engine = QAEngine(
        project=project,
        target_path=target_path,
        project_id=project_id,
        provider=provider,
        model=model,
        temperature=agent_data.get("temperature", 0.2),
        state_callback=lambda state, reason="": _set_project_status(project, state, reason=reason),
    )
    qa_result = engine.run()
    if _abort_if_cancelled(project, "qa retry"):
        return
    for log in engine.logs:
        if log not in project["logs"]:
            project["logs"].append(log)
    if qa_result["manual_steps"]:
        project["manual_steps"] = qa_result["manual_steps"]
        project["needs_user_input"] = True
    project["logs"].append(f"--- QA RETRY REPORT ---")
    project["logs"].append(f"Rounds: {qa_result['rounds_completed']} | Errors: {qa_result['total_errors']}")
    if qa_result["success"]:
        _mark_qa_passed(project, True)
        _set_project_status(project, "final_audit")
        if _abort_if_cancelled(project, "before retry final audit"):
            return
        final_ok, final_errors = _run_final_delivery_audit(project, target_path, project_id, qa_result=qa_result)
        if _abort_if_cancelled(project, "after retry final audit"):
            return
        if final_ok:
            _mark_final_audit_passed(project, True)
            _save_phase(project, "product_judge")
            _set_project_status(project, "product_judge")
            judge_ok, judge_errors = _run_product_judge_stage(project, qa_result)
            if _abort_if_cancelled(project, "after retry product judge"):
                return
            if judge_ok:
                _mark_product_judge_passed(project, True)
            else:
                _mark_product_judge_passed(project, False)
                project["product_judge_errors"] = judge_errors
                _set_project_status(project, "failed_qa", reason="product judge objections")
                project["logs"].append("❌ Retry passed QA/final audit but Product Judge found unresolved objections.")
                return
            if _set_project_status(project, "completed"):
                project["logs"].append("✅ RETRY PASSED! All verifications successful!")
            else:
                _set_project_status(project, "failed_qa", reason="completion gate blocked")
        else:
            _mark_final_audit_passed(project, False)
            project["final_audit_errors"] = final_errors
            if _repair_product_judge_objections(project, target_path, project_id):
                return
            _set_project_status(project, "failed_qa", reason="final audit failed")
            project["logs"].append("❌ Retry passed QA but failed final audit.")
    elif qa_result.get("needs_credentials"):
        _mark_qa_passed(project, False)
        _set_project_status(project, "needs_credentials")
        project["manual_steps"] = qa_result.get("manual_steps", [])
        project["logs"].append("⚠️ Still needs external credentials.")
    elif qa_result.get("needs_human_input"):
        _mark_qa_passed(project, False)
        _set_project_status(project, "needs_human_input")
        project["manual_steps"] = qa_result.get("manual_steps", [])
        project["logs"].append("⚠️ Still needs human clarification.")
    elif qa_result["manual_steps"]:
        _mark_qa_passed(project, False)
        _set_project_status(project, "needs_user_input")
        project["logs"].append("⚠️ Still needs manual steps.")
    else:
        _mark_qa_passed(project, False)
        _set_project_status(project, "failed_qa")
        project["logs"].append("❌ Retry failed.")
        project["qa_errors"] = qa_result.get("errors", [])


@app.post("/api/projects/{project_id}/restart")
def restart_project_pipeline(project_id: str, background_tasks: BackgroundTasks):
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    old_status = project.get("status", "")
    project["logs"] = [f"[System]: Pipeline restarted (was: {old_status})"]
    _set_project_status(project, "planning", force=True)
    project["cancel_requested"] = False
    project.pop("opencode_session_id", None)
    _reset_delivery_gates(project)
    project.pop("needs_user_input", None)
    project.pop("manual_steps", None)
    project.pop("qa_errors", None)
    project.pop("product_judge_input", None)
    project.pop("product_judge_report", None)
    project.pop("product_judge_errors", None)
    project.pop("bugcatcher_review_v1", None)
    project.pop("bugcatcher_review_v2", None)
    project.pop("bugcatcher_review_v3", None)
    project.pop("sentinel_review_v1", None)
    project.pop("sentinel_review_v2", None)
    project.pop("sentinel_review_v3", None)
    project.pop("lupa_review_v1", None)
    project.pop("lupa_review_v2", None)
    project.pop("lupa_review_v3", None)
    project.pop("pipeline_iteration", None)
    project.pop("_thread_name", None)
    project.pop("_phase", None)

    _save_projects_state()
    background_tasks.add_task(async_studio_production_pipeline, project_id)
    return {"status": "restarted", "message": f"Pipeline restarted for '{project['title']}'"}


@app.post("/api/projects/{project_id}/resume")
def resume_project_pipeline(project_id: str, background_tasks: BackgroundTasks):
    """Resume an interrupted pipeline from its saved _phase."""
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    if project.get("status") in ("completed", "cancelled"):
        raise HTTPException(400, f"Cannot resume project in terminal state: {project.get('status')}")
    saved_phase = project.get("_phase")
    if not saved_phase or saved_phase not in _PHASE_ORDER:
        raise HTTPException(400, "Nothing to resume — project has no saved phase state")
    project["logs"].append(f"[System]: Pipeline resuming from phase '{saved_phase}'")
    t = _threading.Thread(target=async_studio_production_pipeline, args=(project_id,), daemon=True, name=f"pipeline-{project_id[:8]}")
    t.start()
    project["_thread_name"] = t.name
    return {"status": "resumed", "message": f"Pipeline resumed from '{saved_phase}' for '{project['title']}'"}


@app.get("/api/projects/{project_id}/dir")
def get_project_dir(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    dir_name = project['title'].replace(' ', '_').lower()
    target_path = os.path.join(BASE_DIR, "generated_projects", dir_name)
    if not os.path.exists(target_path):
        old_name = f"{project_id}_{dir_name}"
        old_path = os.path.join(BASE_DIR, "generated_projects", old_name)
        if os.path.exists(old_path):
            target_path = old_path
        else:
            raise HTTPException(404, "Project directory not yet generated")
    return {"project_id": project_id, "path": target_path}


def _open_local_path(raw_path: str):
    """Open a local project folder from the backend for browser-based sessions."""
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required")
    target_path = os.path.abspath(raw_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", target_path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target_path])
        else:
            subprocess.Popen(["xdg-open", target_path])
        return {"status": "opened", "path": target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _editor_candidates(editor: str) -> list[str]:
    editor = (editor or "").strip().lower()
    if editor in ("vscode", "vs_code", "code"):
        return [
            SYSTEM_SETTINGS.get("vscode_path", "code"),
            "code",
            "code.cmd",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\bin\code.cmd"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code\bin\code.cmd"),
        ]
    if editor in ("pycharm", "pycharm64"):
        jetbrains_roots = [
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\JetBrains\PyCharm*\bin\pycharm64.exe"),
        ]
        discovered = []
        for pattern in jetbrains_roots:
            discovered.extend(sorted(glob.glob(pattern), reverse=True))
        return [
            SYSTEM_SETTINGS.get("pycharm_path", "pycharm"),
            "pycharm",
            "pycharm64",
            os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\scripts\pycharm.cmd"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.2\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.2\bin\pycharm64.exe"),
        ] + discovered
    configured = SYSTEM_SETTINGS.get(f"{editor}_path")
    return [configured or editor]


def _resolve_editor_executable(editor: str) -> str:
    checked = []
    for candidate in _editor_candidates(editor):
        if not candidate:
            continue
        candidate = os.path.expandvars(os.path.expanduser(str(candidate).strip().strip('"')))
        if not candidate or candidate in checked:
            continue
        checked.append(candidate)
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    raise HTTPException(status_code=404, detail=f"Editor '{editor}' not found. Checked: {', '.join(checked[:8])}")


def _open_editor(editor: str, raw_path: str):
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required")
    target_path = os.path.abspath(raw_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Project path not found")
    executable = _resolve_editor_executable(editor)
    try:
        if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
            subprocess.Popen(subprocess.list2cmdline([executable, target_path]), shell=True)
        else:
            subprocess.Popen([executable, target_path])
        return {"status": "opened", "editor": editor, "executable": executable, "path": target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open editor: {e}")


@app.post("/api/system/open-path")
def open_system_path(payload: dict):
    return _open_local_path(payload.get("path", ""))


@app.get("/api/system/open-path")
def open_system_path_get(path: str = Query(...)):
    return _open_local_path(path)


@app.post("/api/system/open-editor")
def open_system_editor(payload: dict):
    return _open_editor(payload.get("editor", ""), payload.get("path", ""))


@app.get("/api/projects/{project_id}/dir/changes")
def get_project_changes(project_id: str, since: float = 0):
    if project_id not in active_projects:
        raise HTTPException(404, "Project not found")
    project = active_projects[project_id]
    dir_name = project['title'].replace(' ', '_').lower()
    target_path = os.path.join(BASE_DIR, "generated_projects", dir_name)
    if not os.path.exists(target_path):
        old_name = f"{project_id}_{dir_name}"
        old_path = os.path.join(BASE_DIR, "generated_projects", old_name)
        if os.path.exists(old_path):
            target_path = old_path
        else:
            return {"changes": [], "current_time": 0}
    changes = []
    now = time.time()
    for root, dirs, files in os.walk(target_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            mtime = os.path.getmtime(fpath)
            if mtime > since:
                rel = os.path.relpath(fpath, target_path)
                changes.append({"path": rel.replace(os.sep, '/'), "mtime": mtime})
    return {"changes": changes, "current_time": now}


@app.get("/api/projects/all")
def list_all_projects():
    projects = []
    for pid, proj in active_projects.items():
        projects.append({
            "project_id": pid,
            "id": proj.get("id", pid),
            "title": proj.get("title", "Untitled"),
            "status": proj.get("status", "unknown"),
            "platform": proj.get("platform", "manual"),
            "budget": proj.get("budget", "?"),
            "url": proj.get("url", ""),
            "target_path": proj.get("target_path", ""),
            "logs_count": len(proj.get("logs", [])),
            "_phase": proj.get("_phase"),
        })
    projects.sort(key=lambda p: p.get("title", ""))
    return {"projects": projects, "count": len(projects)}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    proj = active_projects.pop(project_id, None)
    PROJECT_TASKS.pop(project_id, None)
    _save_projects_state(preserve_missing=False)
    if proj:
        target_path = proj.get("target_path", "")
        if target_path and os.path.exists(target_path):
            import shutil as _shutil
            try:
                _shutil.rmtree(target_path)
            except Exception:
                pass
    return {"status": "deleted", "project_id": project_id, "title": proj.get("title", "") if proj else ""}


@app.delete("/api/projects/{project_id}/list")
def remove_project_from_list(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    proj = active_projects.pop(project_id, None)
    PROJECT_TASKS.pop(project_id, None)
    _save_projects_state(preserve_missing=False)
    return {
        "status": "removed_from_list",
        "project_id": project_id,
        "title": proj.get("title", "") if proj else "",
        "path": proj.get("target_path", "") if proj else "",
    }


@app.get("/api/projects/completed")
def get_completed_projects():
    projects = []
    for pid, proj in active_projects.items():
        if proj.get("status") == "completed" and proj.get("target_path"):
            projects.append({
                "project_id": pid,
                "title": proj.get("title", "Untitled"),
                "path": proj["target_path"],
                "platform": proj.get("platform", "manual"),
                "logs": proj.get("logs", []),
            })
    return {"projects": projects}


@app.post("/api/projects/claim")
def claim_project(payload: ProjectClaimPayload, background_tasks: BackgroundTasks):
    project_id = str(uuid.uuid4())
    title = payload.title or f"Job from {payload.platform}"
    project = {
        "project_id": project_id,
        "id": project_id,
        "platform": payload.platform,
        "title": title,
        "jobTitle": title,
        "description": payload.description or "",
        "budget": payload.budget or "?",
        "url": payload.url or "",
        "status": "planning",
        "chat_history": [],
        "logs": [],
    }
    _reset_delivery_gates(project)
    active_projects[project_id] = project
    project["logs"].append(f"Project claimed: {title}")
    project["logs"].append("Starting production pipeline...")
    t = _threading.Thread(target=async_studio_production_pipeline, args=(project_id,), daemon=True, name=f"pipeline-{project_id[:8]}")
    t.start()
    project["_thread_name"] = t.name
    _save_projects_state()
    return project


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


@app.post("/api/agents/goldie/analyze")
def goldie_analyze(payload: GoldieAnalyzePayload):
    title = ""
    desc = ""
    budget = "?"
    if payload.project_id and payload.project_id in active_projects:
        proj = active_projects[payload.project_id]
        title = proj.get("title", "")
        desc = proj.get("description", "")
        budget = proj.get("budget", "?")
    title = payload.project_title or title
    desc = payload.project_description or desc
    budget = payload.project_budget or budget
    provider, model = get_agent_provider_model("goldie")
    result = goldie_agent.analyze_project_finances(
        project_title=title,
        project_description=desc,
        project_budget=budget,
        provider=provider,
        model_name=model,
    )
    return result


@app.post("/api/agents/goldie/search")
def goldie_search(payload: GoldieSearchPayload):
    result = goldie_agent.search_financial_data(query=payload.query)
    return result


# ── GitHub Integration ───────────────────────────────────────────────────

class GitHubConfigPayload(BaseModel):
    token: str = ""
    username: str = ""
    repo: str = ""

class GitHubPushPayload(BaseModel):
    project_id: str

class GitHubImportPayload(BaseModel):
    raw_url: str

@app.get("/api/config/github")
def get_github_config():
    data = load_studio_keys()
    cfg = data.get("_github", {})
    return {
        "token": bool(cfg.get("token")),
        "username": cfg.get("username", ""),
        "repo": cfg.get("repo", ""),
        "connected": bool(cfg.get("token") and cfg.get("username") and cfg.get("repo"))
    }

@app.post("/api/config/github")
def save_github_config(payload: GitHubConfigPayload):
    data = load_studio_keys()
    data["_github"] = {
        "token": payload.token,
        "username": payload.username,
        "repo": payload.repo,
    }
    save_studio_keys(data)
    return {"status": "saved", "connected": bool(payload.token and payload.username and payload.repo)}

@app.post("/api/projects/{project_id}/github/push")
def push_project_to_github(project_id: str):
    data = load_studio_keys()
    github = data.get("_github", {})
    token = github.get("token", "")
    username = github.get("username", "")
    repo = github.get("repo", "")
    if not (token and username and repo):
        raise HTTPException(400, "GitHub not configured. Save token/username/repo first.")

    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    proj_data = {k: v for k, v in proj.items() if k != "_key"}
    body = json.dumps(proj_data, indent=2, ensure_ascii=False).encode("utf-8")
    file_name = f"projects/{project_id}.json"
    commit_msg = f"Update project {proj.get('title', project_id)}"

    # Try to get existing file SHA (for update)
    sha = None
    get_url = f"https://api.github.com/repos/{username}/{repo}/contents/{file_name}"
    req = urllib.request.Request(get_url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read()).get("sha", "")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise HTTPException(400, f"GitHub API error: {e.code}")

    import base64
    put_body = json.dumps({
        "message": commit_msg,
        "content": base64.b64encode(body).decode("utf-8"),
        "sha": sha,
        "branch": "main",
    }).encode("utf-8")

    put_url = f"https://api.github.com/repos/{username}/{repo}/contents/{file_name}"
    req = urllib.request.Request(put_url, data=put_body, method="PUT", headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return {"status": "pushed", "url": result.get("content", {}).get("html_url", "")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise HTTPException(400, f"GitHub push failed: {e.code} — {detail}")

@app.post("/api/projects/import/github")
def import_project_from_github(payload: GitHubImportPayload):
    try:
        req = urllib.request.Request(payload.raw_url, headers={"User-Agent": "FreelancerStudio"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            proj_data = json.loads(resp.read().decode())
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch project from URL: {e}")

    proj_id = proj_data.get("project_id", str(uuid.uuid4())[:8])
    proj_data["project_id"] = proj_id
    proj_data["_key"] = "project"
    data = load_studio_keys()
    data[proj_id] = proj_data
    save_studio_keys(data)
    return {"status": "imported", "project": proj_data}


# ── Export ───────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/export")
def export_project(project_id: str, fmt: str = Query("markdown", pattern="^(markdown|json)$")):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    if fmt == "json":
        content = json.dumps(proj, indent=2, ensure_ascii=False)
        media_type = "application/json; charset=utf-8"
        filename = f"{project_id}.json"
    else:
        lines = [
            f"# {proj.get('title', 'Untitled Project')}",
            "",
            f"**Status:** {proj.get('status', 'unknown')}",
            f"**Budget:** {proj.get('budget', '—')}",
            f"**Platform:** {proj.get('platform', 'manual')}",
            "",
            "## Description",
            "",
            proj.get("description", proj.get("initial_description", "No description")),
            "",
            "## Chat History",
            "",
        ]
        for msg in proj.get("chat_history", []):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"### {role.capitalize()}")
            lines.append("")
            lines.append(content)
            lines.append("")
        lines.append("---")
        lines.append(f"*Exported from AI Freelance Studio on {__import__('datetime').datetime.now().isoformat()}*")
        content = "\n".join(lines)
        media_type = "text/markdown; charset=utf-8"
        filename = f"{project_id}.md"

    return PlainTextResponse(content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


# ── Project File Management ──────────────────────────────────────────────

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_UPLOAD_FILES = 100


def sanitize_filename(name: str) -> str:
    """Strip path separators and prevent traversal."""
    name = name.replace("\\", "/").strip("/")
    name = Path(name).name
    return "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')


def _get_project_dir(project_id: str) -> str:
    """Get the filesystem path for a project's uploaded files."""
    safe_id = "".join(c for c in project_id if c.isalnum() or c in "-_.")
    path = os.path.join(PROJECTS_DATA_DIR, safe_id)
    os.makedirs(path, exist_ok=True)
    return path


def resolve_path(project_id: str, relative_path: str) -> str:
    """Resolve a relative path within a project directory, blocking traversal."""
    base = os.path.realpath(_get_project_dir(project_id))
    target = os.path.realpath(os.path.join(base, relative_path.lstrip("/")))
    if not target.startswith(base + os.sep) and target != base:
        raise HTTPException(400, "Path traversal denied")
    return target


@app.get("/api/projects/{project_id}/files")
def list_project_files(project_id: str, path: str = Query("", alias="path")):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    target = resolve_path(project_id, path)
    if not os.path.exists(target):
        raise HTTPException(404, "Path not found")
    if not os.path.isdir(target):
        raise HTTPException(400, "Not a directory")
    entries = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        stat = os.stat(full)
        entries.append({
            "name": name,
            "is_dir": os.path.isdir(full),
            "size": stat.st_size if not os.path.isdir(full) else 0,
            "modified": stat.st_mtime,
        })
    current_rel = path.lstrip("/")
    parent_rel = "/".join(current_rel.split("/")[:-1]) if current_rel else ""
    return {
        "path": current_rel,
        "parent_path": parent_rel,
        "entries": entries,
        "count": len(entries),
    }


@app.post("/api/projects/{project_id}/files/upload")
async def upload_project_files(project_id: str, files: List[UploadFile] = FastAPIFile(...)):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"Too many files. Max {MAX_UPLOAD_FILES} at once.")
    base_dir = _get_project_dir(project_id)
    uploaded = []
    errors = []
    total_size = 0
    for f in files:
        safe_name = sanitize_filename(f.filename or "unnamed")
        if not safe_name:
            errors.append({"file": f.filename, "error": "Invalid filename"})
            continue
        content = await f.read()
        total_size += len(content)
        if total_size > MAX_FILE_SIZE:
            errors.append({"file": f.filename, "error": "Total upload exceeds 50 MB limit"})
            break
        dest = os.path.join(base_dir, safe_name)
        try:
            with open(dest, "wb") as out:
                out.write(content)
            uploaded.append(safe_name)
        except OSError as e:
            errors.append({"file": safe_name, "error": str(e)})
    return {
        "status": "ok" if not errors else "partial" if uploaded else "error",
        "uploaded": uploaded,
        "uploaded_count": len(uploaded),
        "errors": errors,
        "error_count": len(errors),
    }


@app.post("/api/projects/{project_id}/files/upload/archive")
async def upload_project_archive(project_id: str, file: UploadFile = FastAPIFile(...)):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    safe_name = sanitize_filename(file.filename or "archive.zip")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2"):
        raise HTTPException(400, f"Unsupported archive format: {ext}. Use .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "Archive exceeds 50 MB limit")
    base_dir = _get_project_dir(project_id)
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    extracted = []
    errors = []
    try:
        if ext == ".zip":
            with zipfile.ZipFile(tmp_path) as zf:
                for info in zf.infolist():
                    safe = sanitize_filename(info.filename)
                    if not safe:
                        continue
                    target = os.path.realpath(os.path.join(base_dir, safe))
                    if not target.startswith(os.path.realpath(base_dir) + os.sep):
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    extracted.append(safe)
        elif ext in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2"):
            mode = "r:gz" if ext in (".tar.gz", ".tgz") else "r:bz2" if ext in (".tar.bz2", ".tbz2") else "r"
            with tarfile.open(tmp_path, mode) as tf:
                for member in tf.getmembers():
                    safe = sanitize_filename(member.name)
                    if not safe:
                        continue
                    target = os.path.realpath(os.path.join(base_dir, safe))
                    if not target.startswith(os.path.realpath(base_dir) + os.sep):
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    src = tf.extractfile(member)
                    if src is not None:
                        with open(target, "wb") as dst:
                            dst.write(src.read())
                    extracted.append(safe)
    except Exception as e:
        errors.append(str(e))
    finally:
        os.unlink(tmp_path)
    return {
        "status": "ok" if not errors else "partial" if extracted else "error",
        "extracted": extracted,
        "extracted_count": len(extracted),
        "errors": errors,
    }


@app.get("/api/projects/{project_id}/files/download/{path:path}")
def download_project_file(project_id: str, path: str):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    target = resolve_path(project_id, path)
    if not os.path.exists(target):
        raise HTTPException(404, "File not found")
    if os.path.isdir(target):
        raise HTTPException(400, "Cannot download a directory. Archive it first.")
    mime, _ = mimetypes.guess_type(target)
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        filename=os.path.basename(target),
                        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(target)}"'})


@app.delete("/api/projects/{project_id}/files/{path:path}")
def delete_project_item(project_id: str, path: str):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    target = resolve_path(project_id, path)
    if not os.path.exists(target):
        raise HTTPException(404, "Path not found")
    if target == _get_project_dir(project_id):
        raise HTTPException(400, "Cannot delete project root")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return {"status": "deleted", "path": path}
    except OSError as e:
        raise HTTPException(500, f"Delete failed: {e}")


@app.post("/api/projects/{project_id}/files/mkdir")
def create_project_directory(project_id: str, name: str = Query(...)):
    proj = active_projects.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    safe = sanitize_filename(name)
    if not safe:
        raise HTTPException(400, "Invalid directory name")
    base_dir = _get_project_dir(project_id)
    target = os.path.join(base_dir, safe)
    if os.path.exists(target):
        raise HTTPException(400, "Path already exists")
    try:
        os.makedirs(target)
        return {"status": "created", "path": safe}
    except OSError as e:
        raise HTTPException(500, f"Create failed: {e}")


# ── Serve built frontend (SPA) ───────────────────────────────────────────
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")
print(f"[Backend Debug]: FRONTEND_DIST={FRONTEND_DIST} isdir={os.path.isdir(FRONTEND_DIST)}")

@app.get("/api/debug/frontend", include_in_schema=False)
async def debug_frontend():
    return {
        "BASE_DIR": BASE_DIR,
        "FRONTEND_DIST": FRONTEND_DIST,
        "isdir": os.path.isdir(FRONTEND_DIST),
        "index_exists": os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")),
        "assets_exists": os.path.isdir(os.path.join(FRONTEND_DIST, "assets")),
        "routes": [r.path for r in app.routes],
    }

if os.path.isdir(FRONTEND_DIST):
    print(f"[Backend]: Frontend dist found at {FRONTEND_DIST}")

    @app.get("/", include_in_schema=False)
    async def serve_index(request: Request):
        idx = os.path.join(FRONTEND_DIST, "index.html")
        if not os.path.isfile(idx):
            raise HTTPException(404)
        with open(idx, "r", encoding="utf-8") as f:
            html = f.read()
        port = request.url.port
        script = f'<script>window.BACKEND_PORT={port};</script>'
        html = html.replace("</head>", f"{script}</head>")
        return HTMLResponse(html, media_type="text/html")

    MEDIA_TYPES = {".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2", ".html": "text/html", ".map": "application/json"}

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        if path.startswith("api/") or path in ("openapi.json", "docs", "redoc"):
            raise HTTPException(404)
        file_path = os.path.join(FRONTEND_DIST, path)
        if os.path.isfile(file_path):
            ext = os.path.splitext(path)[1].lower()
            return FileResponse(file_path, media_type=MEDIA_TYPES.get(ext))
        idx = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.isfile(idx):
            return FileResponse(idx, media_type="text/html")
        raise HTTPException(404)
else:
    print(f"[Backend Warning]: Frontend dist not found at {FRONTEND_DIST}")


if __name__ == "__main__":
    import uvicorn
    import socket

    def find_free_port(start_port: int = 8080, max_attempts: int = 20) -> int:
        for port in range(start_port, start_port + max_attempts):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", port))
                    return port
                except OSError:
                    continue
        raise IOError("Could not find any available network ports in the system registry.")

    start_port = int(os.environ.get("BACKEND_START_PORT", "8080"))
    free_port = find_free_port(start_port=start_port)
    print(f"[Backend Boot]: Network cluster allocated securely on target port: {free_port}")

    port_file = os.path.join(BASE_DIR, "studio_port.txt")
    try:
        with open(port_file, "w", encoding="utf-8") as f:
            f.write(str(free_port))
    except Exception as e:
        print(f"[Backend Warning]: Failed to write port file: {e}")

    uvicorn.run(app, host="0.0.0.0", port=free_port)
