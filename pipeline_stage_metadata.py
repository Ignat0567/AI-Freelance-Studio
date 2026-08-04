"""Static pipeline/agent stage metadata shared by main.py and api/discovery.py.

These are plain constant dicts, never reassigned at runtime, so both
modules import the same objects from here rather than main.py owning
them and a router importing them back (which would be circular).
"""

from __future__ import annotations

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
    "failed_final_audit": "Failed final audit",
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
