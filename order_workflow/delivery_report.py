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


# The browser-backed gates arrive as "<phase>/<check>". Which phase they guarded is the
# pipeline's business; what the client wants to know is what was checked.
_CHECK_LABELS = {
    "visual": "the design and accessibility check",
    "smoke": "the browser render check",
    "state": "the check that data survives a reload",
}


def _gate_label(stage: str) -> str:
    phase, _, check = stage.partition("/")
    if check:
        return _CHECK_LABELS.get(check, check.replace("_", " "))
    return _GATE_LABELS.get(phase, phase.replace("_", " "))


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


def build_qa_evidence(evidence: list[tuple[str, str]]) -> str | None:
    """The gates' own output, verbatim, as a file that ships with the project.

    delivery_report.md says the checks passed. This is what they printed while passing --
    "Palette: 4/4 approved colours painted (100%)", "Dark mode: page repaints under
    prefers-color-scheme: dark". The difference between those two documents is the
    difference between a claim and evidence, and evidence is the thing this pipeline has
    that a freelancer's word does not.

    Verbatim on purpose: summarising it back into prose would reintroduce exactly the gap
    it exists to close.
    """
    sections = [(gate, text.strip()) for gate, text in evidence if text and text.strip()]
    if not sections:
        return None
    lines = [
        "# What the checks measured",
        "",
        "Each section is the unedited output of a check that had to pass before this project",
        "was delivered. These are programs -- a compiler, a test runner, a real headless",
        "browser, contrast arithmetic -- not opinions about the code.",
        "",
    ]
    for gate, text in sections:
        lines += [f"## {_gate_label(gate)}", "", "```", *text.splitlines(), "```", ""]
    return "\n".join(lines)


def _cost_line(usage: TokenUsage | None) -> str | None:
    if usage is None or usage.total_cost_usd <= 0:
        return None
    return f"Build cost: ${usage.total_cost_usd:.2f}."


# What a delivery says when nothing more specific is known. Kept here, next to the report
# that shows it, because the README used to answer the same question from a different source
# -- the QA gate commands -- and told the client of a single-file page to run `npm test`.
DEFAULT_RUN_INSTRUCTION = "Install dependencies, then start the preview server: `npm install && npm run preview`."


def resolve_run_instruction(run_command: str | None) -> str:
    """The one answer to "how do I start it", for every document that has to give it."""
    return run_command or DEFAULT_RUN_INSTRUCTION


def _requirements_worth_listing(goal: str, requirements: tuple[str, ...]) -> tuple[str, ...]:
    """Drop a bullet that only repeats the goal printed directly above it.

    An order written as one paragraph yields a single "core feature" that is the goal itself,
    cut to fit its field. The client then reads the same sentence twice, the second time
    ending in an ellipsis -- which reads like the document lost its nerve halfway through.
    """
    trimmed = " ".join(goal.split())
    kept = []
    for item in requirements:
        candidate = " ".join(item.split()).rstrip("…").rstrip()
        if candidate and trimmed.startswith(candidate):
            continue
        kept.append(item)
    return tuple(kept)


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
    # The status a check saw when it fetched the page itself, for products that ship no
    # container: a static page has no image to run but is still served, and still answers.
    served_http_status: int | None = None,
    run_command: str | None = None,
    evidence_file: str | None = None,
    screenshot_file: str | None = None,
    delivered_files: tuple[str, ...] = (),
    built_by: str = "",
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
        *[f"- {item}" for item in _requirements_worth_listing(goal, requirements)],
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
    elif served_http_status:
        # What actually happened, rather than what did not. A single-file page builds no
        # container, and the sentence below is all its client used to get under "proof it
        # runs" -- an absence where the criterion asks for evidence. The page *is* served
        # over HTTP during its check, and that status is a fact worth stating.
        lines.append(f"The page was served over HTTP and answered {served_http_status} during its checks.")
    else:
        lines.append("The build and its automated checks passed; container packaging was not part of this run.")
    if screenshot_file:
        lines.append(f"`{screenshot_file}` is the page as the check saw it, captured during the run.")
    if built_by:
        # Which code produced this folder. A delivery that cannot say what built it cannot be
        # reproduced, and on 2026-08-21 a run was measured against a build twelve hours older
        # than the fix it was supposed to be testing.
        lines.append(f"Built by AI Freelance Studio {built_by}.")
    if evidence_file:
        lines.append(f"The checks' own output is in `{evidence_file}` -- the measurements, not a summary of them.")
    # Deliberately not a raw file count. The first live static-page delivery reported "477
    # files generated" for a single HTML page: the QA step's node_modules, the isolated .git
    # and the pipeline's own markers all counted. A number a client can see is wrong is worse
    # than no number, and it undermines the measurements next to it.
    if delivered_files:
        listed = ", ".join(f"`{name}`" for name in delivered_files[:6])
        more = f" and {len(delivered_files) - 6} more" if len(delivered_files) > 6 else ""
        lines.append(f"Delivered: {listed}{more}.")
    cost_line = _cost_line(usage)
    if cost_line:
        lines.append(cost_line)
    lines += [
        "",
        "## How to run it",
        "",
        resolve_run_instruction(run_command),
        "",
    ]
    return "\n".join(lines)
