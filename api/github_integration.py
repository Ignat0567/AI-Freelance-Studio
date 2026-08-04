"""GitHub configuration and project push/import routes.

Extracted from main.py. Project lookup is owned by main.py's
active_projects registry, so this module exposes a factory that main.py
calls once at startup, passing that in explicitly instead of importing
it back (which would create a circular import).
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

import config_storage
import secret_store
from api.request_models import GitHubConfigPayload, GitHubImportPayload

load_studio_keys = config_storage.load_studio_keys
save_studio_keys = config_storage.save_studio_keys


def build_github_router(active_projects: Dict[str, Any]) -> APIRouter:
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

    @router.post("/api/projects/{project_id}/github/push")
    def push_project_to_github(project_id: str):
        data = load_studio_keys()
        github = data.get("_github", {})
        token = secret_store.get_secret("github_token", {"github_token": github.get("token", "")})
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

    @router.post("/api/projects/import/github")
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

    return router
