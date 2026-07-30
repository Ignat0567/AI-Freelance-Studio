"""Claude Code CLI status/authenticate routes -- the Claude counterpart to
api/opencode_routes.py. Claude Code support has no optional-import guard
(claude_bridge.py has no special dependencies), so the module is imported
as a whole and called through module-qualified attribute access -- that
keeps `monkeypatch.setattr(claude_bridge, "...")` effective in tests, the
same way a plain `from claude_bridge import x` would not be.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import claude_bridge


def build_claude_status_router(base_dir: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/claude/status")
    def claude_status():
        return claude_bridge.get_claude_status()

    @router.post("/api/claude/authenticate")
    def claude_authenticate():
        result = claude_bridge.start_claude_auth_terminal(workdir=base_dir)
        if result.get("status") == "error":
            raise HTTPException(500, result.get("message", "Failed to start Claude Code authentication"))
        return result

    return router
