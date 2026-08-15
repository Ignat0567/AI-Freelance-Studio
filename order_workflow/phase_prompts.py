from __future__ import annotations

from dataclasses import dataclass
import re

from .execution_plan import build_prompt
from .models import AgentHandoff, ProjectBrief
from .phase_context import PhaseContext

_FAIL_CLOSED_REASONING = "The brief names an audience this rule set does not recognise; defaulting to requiring a backend for safety."


def build_ui_shell_prompt(brief: ProjectBrief, handoff: AgentHandoff) -> str:
    lines = [
        "Implement ONLY the UI shell for this project: screens and navigation between them.",
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "Screens and navigation to build (from the approved requirements):",
        *[f"- {item}" for item in handoff.requirements],
        "",
        *(["Visual design direction (from Elena's approved design preview -- follow it precisely, it is not optional flavor):",
           *[f"- {item}" for item in handoff.design_preview_summary],
           ""] if handoff.design_preview_summary else []),
        "Strict rules for this phase:",
        "- Do NOT connect a database or any persistence layer.",
        "- Do NOT implement authentication or user accounts.",
        "- Do NOT call any external API or AI service.",
        "- Every place where asynchronous data or a future feature will later appear must show a "
        "loading or skeleton state now, so the UI does not visibly change shape once real logic is added.",
        "- Build navigation between all listed screens so it can be clicked through end to end.",
        "- package.json must include a `preview` script that serves the production build "
        "(the output of `npm run build`) on its tool's default local port, so the app can be "
        "opened and smoke-tested automatically after this phase. A plain Vite project already "
        "gets this for free (`vite preview`, default port 4173) -- do not remove or rename it "
        "if it is already there; add it if it is missing.",
        "",
        "Do not expose secrets in logs, reports, or generated files.",
        "After creating the requested screens and navigation, stop and exit. Do not keep rewriting files.",
    ]
    return "\n".join(lines)


def build_revision_prompt(brief: ProjectBrief, handoff: AgentHandoff, revision_note: str) -> str:
    """For ReviseProjectExecutionAdapter only: this project already exists, fully built, in
    this exact workspace -- unlike every other phase prompt, which either builds from
    nothing or from a PhaseContext summary of what a *previous phase in the same run* did.
    The instruction must be scoped tightly to the one requested change; nothing here asks
    for the whole project to be reconsidered."""
    lines = [
        "This project already exists, fully built, in this exact workspace. Do not rebuild it "
        "from scratch and do not restructure parts that are not mentioned below.",
        "",
        f"Original goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "The client has asked for exactly this change to the existing, already-delivered project:",
        f"{revision_note}",
        "",
        "Rules for this revision:",
        "- Read the existing code first. Make the smallest change that satisfies the request above.",
        "- Do not rewrite files or features unrelated to this request.",
        "- Do not remove the `preview` npm script or otherwise break how the project builds and runs.",
        "",
        "Do not expose secrets in logs, reports, or generated files.",
        "After making the requested change, stop and exit. Do not keep rewriting files.",
    ]
    return "\n".join(lines)


def build_core_feature_prompt(brief: ProjectBrief, handoff: AgentHandoff, ui_shell_context: PhaseContext) -> str:
    core_feature = brief.core_features[0]
    lines = [
        "The UI shell for this project is already implemented in this workspace. Do not rewrite or "
        "restructure the existing screens or navigation.",
        "",
        f"Already built: {ui_shell_context.summary}",
        "",
        f"Now wire in exactly ONE central feature: {core_feature}",
        "Integrate it into the existing UI shell's loading/skeleton slot for this feature — replace the "
        "placeholder state with the real working feature, do not add new screens.",
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "Required: write exactly one automated test that exercises this feature and asserts it returns "
        "the expected result on a test input. The test must be runnable by the project's normal test command.",
        "",
        "Do not expose secrets in logs, reports, or generated files.",
        "After wiring the feature and its test, stop and exit. Do not keep rewriting files.",
    ]
    return "\n".join(lines)


def build_backend_bridge_prompt(
    brief: ProjectBrief,
    handoff: AgentHandoff,
    ui_shell_context: PhaseContext,
    core_feature_context: PhaseContext,
    decision_reasoning: str,
    qa_commands: tuple[str, ...],
) -> str:
    preamble = (
        "The UI shell and one core feature are already implemented in this workspace and must not be "
        f"rewritten. Already built (UI shell): {ui_shell_context.summary} "
        f"Already built (core feature): {core_feature_context.summary}\n"
        f"A backend/database was determined to be needed: {decision_reasoning}\n"
        "Add the backend, database, authentication (if required by the requirements below), and any "
        "remaining requirements or acceptance criteria not yet covered by the existing UI shell and core "
        "feature. Integrate with the existing frontend rather than replacing it."
    )
    return build_prompt(brief, handoff, qa_commands, extra_preamble=preamble)


@dataclass(frozen=True, slots=True)
class BackendDecision:
    needs_backend: bool
    reasoning: str
    # False means the rules below could not classify the audience and fell back to the
    # safe answer, rather than reaching the conclusion on evidence.
    confident: bool


# The strings _target_users() in brief_service.py maps the "target-users" clarification
# answer onto. Anything outside these two sets is free text the user typed themselves.
_SINGLE_USER_TARGETS = frozenset({"single local user"})
_MULTI_USER_TARGETS = frozenset({
    "a small internal team",
    "customers using the application",
    "public users",
})

# Kept deliberately narrow. A false positive here is not free: it triggers a whole extra
# backend-bridge phase (another coding-CLI invocation), so vague matches like "shared" or
# "real-time" -- both of which a client-only app can satisfy on its own -- are left out.
_BACKEND_REQUIRING_PATTERNS: tuple[str, ...] = (
    r"\bsynchroni[sz]",
    r"\bsync\b",
    r"\bmulti[- ]?user\b",
    r"\bmultiple users\b",
    r"\bcollaborat",
    r"\bacross (devices|browsers|machines)\b",
    r"\b(sign|log)[- ]?(in|up)\b",
    r"\blogin\b",
    r"\bauthenticat",
    r"\buser accounts?\b",
)


def decide_backend_need(brief: ProjectBrief, handoff: AgentHandoff) -> BackendDecision:
    """Decide whether this project needs a backend, from the approved brief alone.

    This used to be an extra coding-CLI subprocess per execution (via
    ask_studio_ai_with_history), asking a model to re-derive "is this multi-user?" from
    prose that clarification.py had *already* resolved into brief.target_users. The model
    was not adding information -- it was re-reading a question the pipeline had answered
    upstream, at the cost of a full CLI round trip on every single order.

    Fails closed (needs_backend=True) on anything the rules cannot classify, matching the
    behaviour of the JSON parser this replaces.
    """
    text = " ".join((
        brief.goal,
        *brief.core_features,
        *brief.acceptance_criteria,
        *brief.technical_constraints,
        *handoff.requirements,
    )).casefold()

    # Checked before target_users on purpose: "only me, but synced across my laptop and
    # phone" is a single-user audience that still needs somewhere to sync through.
    for pattern in _BACKEND_REQUIRING_PATTERNS:
        if re.search(pattern, text):
            return BackendDecision(
                needs_backend=True,
                reasoning="The approved requirements ask for sync, accounts, or multi-user access, which needs server-side state.",
                confident=True,
            )

    targets = {item.casefold().strip() for item in brief.target_users}
    shared_audience = sorted(targets & _MULTI_USER_TARGETS)
    if shared_audience:
        return BackendDecision(
            needs_backend=True,
            reasoning=f"The brief targets {', '.join(shared_audience)}, so state has to outlive one local browser.",
            confident=True,
        )
    if targets and targets <= _SINGLE_USER_TARGETS:
        return BackendDecision(
            needs_backend=False,
            reasoning="Single local user, with no sync, accounts, or sharing anywhere in the approved requirements.",
            confident=True,
        )
    return BackendDecision(needs_backend=True, reasoning=_FAIL_CLOSED_REASONING, confident=False)


def build_bot_prompt(brief: ProjectBrief, handoff: AgentHandoff) -> str:
    """Telegram bot instead of a browser web app: no screens, no UI shell/core-feature
    split -- most bots are small enough to build in one pass. The QA that follows this
    (see bot_adapter.py) is `pip install` + `python -c "import bot"`, so the structural
    rules below exist specifically to make that a real, meaningful check rather than a
    vacuous one (a bot module that tries to connect to Telegram at import time would
    "fail" QA for having no real token, not for being broken)."""
    lines = [
        "Build a Telegram bot in Python using the python-telegram-bot library. This is NOT "
        "a browser web app -- there is no HTML/CSS/JS, no frontend, no screens.",
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "Commands and behavior to implement (from the approved requirements):",
        *[f"- {item}" for item in handoff.requirements],
        "",
        "Structure requirements:",
        "- Entry point: bot.py at the project root.",
        "- requirements.txt listing python-telegram-bot and anything else actually used.",
        "- .env.example with BOT_TOKEN=your-token-here (a placeholder, never a real token).",
        "- Read BOT_TOKEN with os.environ, and only inside `if __name__ == \"__main__\":` -- "
        "importing bot.py (e.g. `python -c \"import bot\"`) must succeed with NO token set "
        "and must NOT attempt to contact Telegram's API, start polling, or block. All handler "
        "registration and bot construction must be safe to import.",
        "- README.md with setup steps: create requirements.txt install, set BOT_TOKEN, run bot.py.",
        "",
        "Do not expose secrets in logs, reports, or generated files. Never hardcode a real "
        "bot token anywhere.",
        "After implementing the requested commands, stop and exit. Do not keep rewriting files.",
    ]
    return "\n".join(lines)
