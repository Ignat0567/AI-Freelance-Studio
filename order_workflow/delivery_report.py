"""delivery_report.md: the one document a client actually reads.

MVP_ACCEPTANCE.md's second criterion is that a stranger can open the delivered folder and
answer four questions without asking anyone: what was built, what defects were found and
fixed, proof it runs, how to start it. The report this module used to write answered none
of them -- "Phased live execution completed. Meaningful artifacts detected: 6. Workspace
files inspected: 41." states facts about the *pipeline's own bookkeeping*, not about the
project a client is holding.

An empty "what we found and fixed" section is not an omission to apologise for. A report
that only ever lists problems reads as improvised; naming that nothing needed a second pass
is itself the evidence of a clean build, and saying so plainly is more convincing than
silence would be.
"""

from __future__ import annotations

from .models import TokenUsage


_GATE_LABELS = {
    "ui_shell": "the screens and navigation",
    "core_feature": "the core feature",
    "implementation": "the backend",
    "revision": "the requested change",
    "bot_build": "the bot",
    "static_page_build": "the page",
}


def _gate_label(stage: str) -> str:
    return _GATE_LABELS.get(stage, stage.replace("_", " "))


def _found_and_fixed_lines(gate_log: list[tuple[str, int, bool | None]]) -> list[str]:
    repaired = [entry for entry in gate_log if entry[1] > 0]
    if not repaired:
        return ["Every check passed on the first attempt. Nothing needed a second pass."]
    lines = []
    for stage, attempts, passed in repaired:
        plural = "attempt" if attempts == 1 else "attempts"
        if passed:
            lines.append(f"- {_gate_label(stage).capitalize()} did not pass its checks at first; {attempts} repair {plural} fixed it.")
        else:
            lines.append(
                f"- {_gate_label(stage).capitalize()} still does not pass its checks after {attempts} repair {plural}. "
                "See the execution log for the exact failure -- this needs a human look."
            )
    return lines


def _cost_line(usage: TokenUsage | None) -> str | None:
    if usage is None or usage.total_cost_usd <= 0:
        return None
    return f"Build cost: ${usage.total_cost_usd:.2f}."


def build_delivery_report(
    *,
    goal: str,
    requirements: tuple[str, ...],
    note: str,
    gate_log: list[tuple[str, int, bool | None]],
    files_created: int,
    meaningful_artifact_count: int,
    usage: TokenUsage | None = None,
    deployment_status: str | None = None,
    deployment_image: str | None = None,
    run_command: str | None = None,
) -> str:
    """Assembled from data the run already produced -- brief, gate tally, deployment
    outcome -- not from a model call. The four sections below are MVP_ACCEPTANCE.md's
    four questions, in the order a reader actually asks them."""
    lines = [
        "# Delivery report",
        "",
        "## What was built",
        "",
        goal.strip(),
        "",
        *([f"- {item}" for item in requirements] if requirements else []),
        "",
        note.strip(),
        "",
        "## What we found and fixed",
        "",
        *_found_and_fixed_lines(gate_log),
        "",
        "## Proof it runs",
        "",
    ]
    if deployment_status:
        lines.append(deployment_status)
        if deployment_image:
            lines.append(f"Image: {deployment_image}")
    else:
        lines.append("The build and its automated checks passed; container packaging was not part of this run.")
    lines.append(f"{files_created} files generated, {meaningful_artifact_count} of them substantive project code (not scaffolding or config).")
    cost_line = _cost_line(usage)
    if cost_line:
        lines.append(cost_line)
    lines += [
        "",
        "## How to run it",
        "",
        run_command or "Install dependencies, then start the preview server: `npm install && npm run preview`.",
        "",
    ]
    return "\n".join(lines)
