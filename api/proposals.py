"""Job proposal generation/refinement routes.

Extracted from main.py. agent_configs is rebound (not just mutated) by
main.py in a couple of places (`global agent_configs; agent_configs =
...`), so a plain injected dict reference would go stale after such a
rebind -- get_agent_config must be a callable that looks the name up
fresh in main.py's module namespace on every call. proposal_generator is
force-loaded specially in main.py (works both in normal and
packaged/frozen mode), so its functions are passed in explicitly rather
than re-imported here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException

from api.request_models import ProposalGeneratePayload, ProposalRefinePayload


def build_proposals_router(
    get_agent_config: Callable[[str], Dict[str, Any]],
    get_agent_provider_model: Callable[[str], tuple],
    generate_proposal: Callable[..., Any],
    refine_spec: Callable[..., Any],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/proposals/generate")
    def create_proposal(payload: ProposalGeneratePayload):
        job_desc = payload.job_description.strip()
        if not job_desc:
            raise HTTPException(400, "job_description is required")
        agent_data = get_agent_config("goldie")
        provider, model = get_agent_provider_model("goldie")
        result = generate_proposal(
            job_description=job_desc,
            provider=provider,
            model=model,
            temperature=agent_data.get("temperature", 0.3),
        )
        return result

    @router.post("/api/proposals/refine")
    def refine_project_spec(payload: ProposalRefinePayload):
        original = payload.original_job.strip()
        answers = payload.client_answers.strip()
        if not original or not answers:
            raise HTTPException(400, "original_job and client_answers are required")
        agent_data = get_agent_config("maya")
        provider, model = get_agent_provider_model("maya")
        result = refine_spec(
            original_job=original,
            client_answers=answers,
            provider=provider,
            model=model,
            temperature=agent_data.get("temperature", 0.2),
        )
        return result

    return router
