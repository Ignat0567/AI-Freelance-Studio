"""GitHub credential configuration routes.

Extracted from main.py. Previously also owned project push/import routes
tied to main.py's classic-pipeline active_projects registry; those were
removed along with that pipeline (order_workflow has no equivalent
project-JSON-push concept yet). Only the credential get/save routes
remain, since Settings' GitHub panel reads/writes them independent of
any pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter

import config_storage
import secret_store
from api.request_models import GitHubConfigPayload

load_studio_keys = config_storage.load_studio_keys
save_studio_keys = config_storage.save_studio_keys


def build_github_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/config/github")
    def get_github_config():
        data = load_studio_keys()
        cfg = data.get("_github", {})
        token = secret_store.get_secret("github_token", {"github_token": cfg.get("token", "")})
        warnings = []
        if cfg.get("token") and not secret_store.has_env_secret("github_token"):
            warnings.append(secret_store.legacy_secret_warning("github_token"))
        return {
            "token": bool(token),
            "username": cfg.get("username", ""),
            "repo": cfg.get("repo", ""),
            "connected": bool(token and cfg.get("username") and cfg.get("repo")),
            "legacy_secret_warnings": warnings,
        }

    @router.post("/api/config/github")
    def save_github_config(payload: GitHubConfigPayload):
        data = load_studio_keys()
        current = data.get("_github", {}) if isinstance(data.get("_github"), dict) else {}
        data["_github"] = {
            "token": current.get("token", ""),
            "username": payload.username,
            "repo": payload.repo,
        }
        save_studio_keys(data)
        token = payload.token or secret_store.get_secret("github_token", {"github_token": current.get("token", "")})
        response = {"status": "saved", "connected": bool(token and payload.username and payload.repo)}
        if payload.token:
            response["secret_store_warning"] = "GitHub token was not written to studio_config.json. Set GITHUB_TOKEN to persist it."
        return response

    return router
