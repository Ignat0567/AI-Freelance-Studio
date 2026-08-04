"""OpenCode bridge status/web-login/authenticate routes.

Extracted from main.py. main.py imports these opencode_bridge functions
inside a try/except (OpenCode support is optional), so the resolved
functions and the availability flag are passed in explicitly rather than
re-importing opencode_bridge here (which would duplicate the optional
-import handling in two places).
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException


def build_opencode_status_router(
    has_opencode: bool,
    get_opencode_status: Callable[[], Any],
    start_opencode_web: Callable[..., Any],
    start_opencode_auth_terminal: Callable[..., Any],
    base_dir: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/opencode/status")
    def opencode_status():
        if not has_opencode:
            return {"installed": False, "error": "OpenCode bridge is not available"}
        return get_opencode_status()

    @router.post("/api/opencode/web")
    def opencode_web_login():
        if not has_opencode:
            raise HTTPException(500, "OpenCode bridge is not available")
        result = start_opencode_web(workdir=base_dir)
        if result.get("status") == "error":
            raise HTTPException(500, result.get("message", "Failed to start OpenCode web"))
        return result

    @router.post("/api/opencode/authenticate")
    def opencode_authenticate():
        if not has_opencode:
            raise HTTPException(500, "OpenCode bridge is not available")
        result = start_opencode_auth_terminal(workdir=base_dir)
        if result.get("status") == "error":
            raise HTTPException(500, result.get("message", "Failed to start OpenCode authentication"))
        return result

    return router
