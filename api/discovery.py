"""Stateless discovery/metadata routes: pipeline stage metadata, freelance
job search, and platform suggestions.

Extracted from main.py. search_freelance_jobs / AVAILABLE_PLATFORMS come
from search_utils, which main.py force-loads specially to work both in
normal and packaged/frozen mode, so they are passed in explicitly rather
than re-imported here.
"""

from __future__ import annotations

from typing import Any, Callable, List

from fastapi import APIRouter, Query

from pipeline_stage_metadata import (
    AGENT_STAGE_METADATA,
    PIPELINE_STAGE_METADATA,
    PIPELINE_UI_STAGE_ORDER,
    _STATE_DISPLAY,
)


def build_discovery_router(
    search_freelance_jobs: Callable[[str], Any],
    available_platforms: List[dict],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/pipeline/metadata")
    def get_pipeline_metadata():
        return {
            "stage_order": PIPELINE_UI_STAGE_ORDER,
            "stages": {
                stage: {"id": stage, **PIPELINE_STAGE_METADATA.get(stage, {"label": _STATE_DISPLAY.get(stage, stage.replace("_", " ").title())})}
                for stage in PIPELINE_UI_STAGE_ORDER
            },
            "agent_stages": AGENT_STAGE_METADATA,
        }

    @router.get("/api/jobs/search")
    def search_jobs(q: str = Query(default=""), search_query: str = Query(default=None)):
        query = q or search_query or ""
        results = search_freelance_jobs(query)
        return {"results": results}

    @router.get("/api/platforms/suggest")
    def suggest_platforms(q: str = Query(default=""), search_query: str = Query(default=None)):
        query = (q or search_query or "").lower()
        if query:
            filtered = [p for p in available_platforms if query in p["code"].lower() or query in p["name"].lower()]
        else:
            filtered = available_platforms
        return filtered

    return router
