from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from .models import AgentHandoff, ProjectBrief, StrictDomainModel
from .workspace import ProjectWorkspace


class ProductionExecutionPackage(StrictDomainModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        protected_namespaces=(),
    )

    execution_id: str
    order_id: str
    brief_id: str
    handoff_id: str
    provider_name: str
    model_name: str
    workspace_root: str
    project_path: str
    qa_commands: tuple[str, ...]
    prompt: str
    mode: Literal["dry_run"] = "dry_run"


def build_production_execution_package(
    *,
    execution_id: str,
    brief: ProjectBrief,
    handoff: AgentHandoff,
    workspace: ProjectWorkspace,
    provider_name: str,
    model_name: str,
    qa_commands: tuple[str, ...],
) -> ProductionExecutionPackage:
    return ProductionExecutionPackage(
        execution_id=execution_id,
        order_id=brief.order_id,
        brief_id=brief.id,
        handoff_id=handoff.id,
        provider_name=provider_name,
        model_name=model_name,
        workspace_root=workspace.root_reference,
        project_path=workspace.project_reference,
        qa_commands=qa_commands,
        prompt=_build_prompt(brief, handoff, qa_commands),
    )


def _build_prompt(brief: ProjectBrief, handoff: AgentHandoff, qa_commands: tuple[str, ...]) -> str:
    lines = [
        "Implement the approved AI Freelancer Studio project brief.",
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "Requirements:",
        *[f"- {item}" for item in handoff.requirements],
        "",
        "Acceptance criteria:",
        *[f"- {item}" for item in handoff.acceptance_criteria],
        "",
        "Constraints:",
        *[f"- {item}" for item in (*brief.technical_constraints, *handoff.constraints)],
        "",
        "QA commands:",
        *[f"- {item}" for item in qa_commands],
        "",
        "Do not expose secrets in logs, reports, or generated files.",
    ]
    return "\n".join(lines)
