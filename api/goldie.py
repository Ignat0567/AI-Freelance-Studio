"""Goldie (finance advisor agent) analyze/search routes.

Extracted from main.py. Project lookup, provider/model resolution, and
the goldie_agent module (force-loaded specially in main.py to work both
in normal and packaged/frozen mode) are owned by main.py, so this module
exposes a factory that main.py calls once at startup, passing those in
explicitly instead of importing them back (which would create a
circular import, and re-importing goldie_agent here directly could
resolve to a second, different module object in packaged mode).
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import APIRouter

from api.request_models import GoldieAnalyzePayload, GoldieSearchPayload


def build_goldie_router(
    active_projects: Dict[str, Any],
    get_agent_provider_model: Callable[[str], tuple],
    goldie_agent: Any,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/agents/goldie/analyze")
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

    @router.post("/api/agents/goldie/search")
    def goldie_search(payload: GoldieSearchPayload):
        result = goldie_agent.search_financial_data(query=payload.query)
        return result

    return router
