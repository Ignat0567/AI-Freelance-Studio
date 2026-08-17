from __future__ import annotations

from dataclasses import dataclass
import re

from .execution_plan import build_prompt
from .models import AgentHandoff, ProjectBrief
from .phase_context import PhaseContext

_FAIL_CLOSED_REASONING = "The brief names an audience this rule set does not recognise; defaulting to requiring a backend for safety."


def _visual_gate_rules(brief: ProjectBrief) -> list[str]:
    """State the visual gate's own pass criteria, in its own numbers.

    visual_check.py measures palette coverage, WCAG AA contrast, phone layout, tap-target
    size and overlap on the built page -- and until this existed, the prompt that produced
    that page mentioned none of it. The gate was left to discover, at Playwright speed, that
    nobody had been told the rules: a live run spent both repair attempts and twelve minutes
    on a 4.05:1 muted label and three shelves that did not fit 375px, both of which the
    first attempt would have avoided if asked.

    Deliberately phrased as the thresholds themselves rather than "make it accessible".
    "Contrast at least 4.5:1" is checkable by the model as it writes the CSS; "make it
    accessible" is a sentiment, and the gate does not measure sentiments. This does not
    replace the gate -- a prompt cannot force obedience, which is the entire reason the
    measurement runs afterwards -- it just stops the common failures from being discovered
    the expensive way.
    """
    concept = brief.elena_design_concept
    rules = [
        "- Text must clear WCAG AA contrast against the background it actually sits on: at least "
        "4.5:1 for normal text, 3.0:1 for large text (>=24px, or >=18.66px bold). Muted and "
        "secondary label colours are where this usually fails -- check those specifically.",
        "- The layout must fit a 375x812 phone viewport with no horizontal scrolling: no fixed "
        "widths wider than the screen, and side-by-side columns must stack. Every interactive "
        "element must be at least 24x24px at that width.",
        "- No two visible elements may overlap each other, at either 1280px or 375px wide.",
    ]
    if concept is not None:
        light, dark = concept.light_theme, concept.dark_theme
        rules[:0] = [
            f"- Paint the page ground exactly {light.background} (the approved background), and use the "
            f"rest of the approved palette rather than framework defaults: surface {light.surface}, "
            f"text {light.text}, accent {light.accent}. At least half of those four colours must "
            "actually appear on the page, by painted area.",
            f"- Under `prefers-color-scheme: dark` the ground must actually repaint to the approved dark "
            f"theme (background {dark.background}, surface {dark.surface}, text {dark.text}, "
            f"accent {dark.accent}) -- not stay light.",
        ]
    return rules


def build_ui_shell_prompt(brief: ProjectBrief, handoff: AgentHandoff, *, additions: str = "") -> str:
    """`additions` is free text the client wrote after reading this prompt in the UI.

    It is placed after the requirements but *before* the strict rules, and the rules then
    declare themselves authoritative. Every one of those rules is load-bearing for a gate
    that runs later -- the preview script is what the smoke and visual checks connect to,
    "no database/auth" is what makes the backend-decision gate meaningful, and the skeleton
    states are what the core-feature phase attaches to. Letting client text land after them,
    or letting it replace them, would let someone disarm the QA chain by accident and see
    the consequence several minutes later as a confusing gate failure.

    This is ordering, not enforcement: a prompt cannot force a model to obey. The actual
    guarantee is that the gates measure the built result independently, so an addition that
    breaks the contract fails a check rather than shipping.
    """
    addition_text = " ".join(additions.split()) if additions else ""
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
        *(["Additional notes from the client, who reviewed this instruction before it was sent:",
           addition_text,
           ""] if addition_text else []),
        "Strict rules for this phase (these take precedence over the client's additional notes above):"
        if addition_text
        else "Strict rules for this phase:",
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
        *_visual_gate_rules(brief),
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


def build_core_feature_prompt(
    brief: ProjectBrief,
    handoff: AgentHandoff,
    ui_shell_context: PhaseContext,
    *,
    corrections: tuple[str, ...] = (),
) -> str:
    core_feature = brief.core_features[0]
    # Placed before the feature instruction on purpose: these came from the client looking
    # at the actual shell at the mid-build checkpoint, so they outrank what the brief
    # assumed in the abstract, and the shell may need adjusting before the feature lands
    # on top of it.
    correction_lines = [
        "The client reviewed the shell and asked for these corrections. Apply them first, then "
        "wire in the feature below:",
        *[f"- {item}" for item in corrections],
        "",
    ] if corrections else []
    lines = [
        "The UI shell for this project is already implemented in this workspace. Do not rewrite or "
        "restructure the existing screens or navigation.",
        "",
        f"Already built: {ui_shell_context.summary}",
        "",
        *correction_lines,
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
