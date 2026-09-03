from __future__ import annotations

from dataclasses import dataclass
import re

from .execution_plan import build_prompt
from .models import AgentHandoff, ProjectBrief
from .phase_context import PhaseContext
from .website_generation import _CINEMATIC_KEYWORDS, detect_cinematic_website_intent

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
        "widths wider than the screen, and side-by-side columns must stack. Controls that sit "
        "next to each other -- icon buttons, toolbars, rows of links -- must be at least 24x24px "
        "at that width or keep 24px between their centres; a link inside a sentence is sized by "
        "its text and is fine as it is.",
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
            # A theme control is a reasonable thing to build and the model builds one often.
            # When it does, its state has to drive the same tokens the media query drives, or
            # the two disagree and half the page switches: the reading journal of 2026-08-27
            # was reported for near-white text on a white surface for exactly that reason.
            "- If you add a light/dark control, set `data-theme` to \"light\" or \"dark\" on the "
            "`<html>` element and let the approved tokens do the rest -- they already define every "
            "colour for that attribute as well as for the system preference. Do not write a second "
            "set of colours for the toggle, and do not toggle a class the tokens know nothing about.",
            # And only when a person chooses. Measured 2026-08-28: the delivered journal read
            # matchMedia once in useState and wrote data-theme at mount, which pinned the page
            # to the preference of that instant -- the gate switched the browser to dark
            # afterwards and the ground never repainted.
            "- Write `data-theme` only when the person picks a theme. Do not set it at startup "
            "from `prefers-color-scheme`: while the attribute is absent the tokens follow the "
            "system on their own, and writing it at mount freezes the page against any later "
            "change. A control that offers \"system\" removes the attribute again.",
        ]
    return rules


def build_ui_shell_prompt(brief: ProjectBrief, handoff: AgentHandoff, *, additions: str = "", design_tokens_file: str | None = None) -> str:
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
        "- Scaffold MUST be Vite 5 + React 18, not Vite 2/3 and not React 17: package.json "
        "dependencies `react` and `react-dom` at ^18.3.1, devDependencies `vite` at ^5.3.1 and "
        "`@vitejs/plugin-react` at ^4.3.1, and `\"type\": \"module\"`.",
        "- scripts.build must be `vite build`. scripts.preview must be exactly "
        "`vite preview --host 127.0.0.1 --port 4173` so the smoke check can open "
        "http://localhost:4173. Do not leave preview on Vite 2's default port 5000.",
        "- src/main.jsx must import App from './App.jsx' and mount it with createRoot. Never "
        "leave a Hello-World stub in the entry file while the real UI lives in App.jsx.",
        "- Visible controls the smoke check can find must be real <button>, <input>, <select>, "
        "or <a href> elements -- not clickable divs.",
        *_visual_gate_rules(brief),
        *([
            f"- The approved palette is already in ./{design_tokens_file} at the project root. Move or "
            "copy it into your stylesheet directory, import it from the entry stylesheet so it applies "
            "to every screen, and use its variables (--color-background, --color-surface, --color-text, "
            "--color-accent) instead of writing colour literals. It already carries the dark-theme "
            "media query and paints the page ground, so do not re-declare either.",
            "- Any text or icon sitting on an accent-coloured background (buttons, badges, active tabs) "
            "must use var(--color-on-accent), never white or the body text colour. That variable is "
            "already computed per theme to clear AA contrast against the accent; white on an accent "
            "is the single most common way this build fails its contrast check.",
            # The mirror of the rule above, and a measured deadlock rather than a nicety: every
            # style pack tells the build to use the accent for links, and five of the nine ship
            # a light accent that fails AA as text on their own background (#0071e3 on #f5f5f7
            # is 4.31:1). The gate then asks for a darker colour while this file asks for no new
            # literals, so the same finding came back generation after generation.
            "- Where the accent is the colour of text rather than a fill -- links, eyebrow labels, "
            "figures, active nav items -- use var(--color-accent-text), not var(--color-accent). "
            "It is the same accent moved just far enough to clear AA on the background and on a "
            "surface; the accent itself stays for fills, borders and glows.",
        ] if design_tokens_file else []),
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
        # Prevention for the state-continuity gate, which failed on the third gate of this
        # phase in every measured CRUD run and was then fixed by repair every time -- the
        # same avoidable round trip the visual criteria used to cost the ui_shell phase.
        "Required: every value the user can enter or change must survive navigation. The gate that "
        "follows this phase types into a control, navigates to another screen, comes back, and asserts "
        "the value is still there. A `useState` inside a screen that unmounts fails this. Hold such "
        "values in state that outlives the screen -- context, a store, or a parent component that stays "
        "mounted -- or persist them to localStorage, so the rest of the app reads the same value.",
        "",
        "Do not replace src/main.jsx with a Hello-World stub, do not downgrade Vite or React, "
        "and do not remove or rename the `preview` script (`vite preview --host 127.0.0.1 --port 4173`).",
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


STATIC_PAGE_FILENAME = "index.html"
# Hosts a single-file page may still reach: the module CDNs a no-build page needs to import a
# library at all, and Google Fonts. Anything else has to be inline or a data: URI. The gate
# reads every script/link/img in the delivered page and fails on any other host, so this list
# is the contract, not advice -- see static_page_check.ALLOWED_ASSET_HOSTS, which must match.
STATIC_PAGE_ALLOWED_HOSTS: tuple[str, ...] = (
    "unpkg.com",
    "cdn.jsdelivr.net",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)
STATIC_PAGE_MAX_BYTES = 1_000_000


def static_page_requires_webgl(brief: ProjectBrief, handoff: AgentHandoff | None = None) -> bool:
    """WebGL is the gate for cinematic/3D pages, not for a one-file card or landing copy.

    detect_cinematic_website_intent only reads goal/frontend/visual_direction. A static-page
    brief often puts the 3D signal in core_features ('orbiting 3D scene'), so the prompt and
    the check have to look there too or a nature scene would be asked for WebGL by tests and
    then not by the live gate -- or the other way around.
    """
    if detect_cinematic_website_intent(brief):
        return True
    parts = [brief.goal, brief.recommended_stack.frontend, *brief.core_features, *brief.acceptance_criteria]
    if brief.elena_design_concept is not None:
        parts.append(brief.elena_design_concept.visual_direction)
    if handoff is not None:
        parts.extend([handoff.goal, handoff.context_summary, *handoff.requirements, *handoff.acceptance_criteria])
    haystack = " ".join(part for part in parts if part).lower()
    return any(keyword in haystack for keyword in _CINEMATIC_KEYWORDS)


def build_static_page_prompt(brief: ProjectBrief, handoff: AgentHandoff, *, additions: str = "") -> str:
    """One self-contained .html file -- a creative/interactive page, not a smaller web app.

    Every structural rule here is load-bearing for static_page_check.py, the same way the
    web-app prompt's rules are for the build/smoke/visual gates: the gate serves this
    directory and opens `index.html`, counts WebGL draw calls to prove the scene actually
    renders, counts animation frames to prove the loop is alive, and rejects any asset host
    outside STATIC_PAGE_ALLOWED_HOSTS.

    One deliberate inversion from build_ui_shell_prompt: the *client's* art direction is
    authoritative here, and Elena's palette is not enforced. A page whose whole point is a
    moody cinematic scene cannot also be required to paint a light palette some style pack
    picked from the word "nature" -- so this pipeline does not run the palette gate at all,
    and says so rather than quietly hoping the two agree.
    """
    addition_text = " ".join(additions.split()) if additions else ""
    requirements = handoff.requirements or brief.core_features
    cinematic = static_page_requires_webgl(brief, handoff)
    shared_rules = [
        f"- Exactly one HTML file, named {STATIC_PAGE_FILENAME}. Do not create .js or .css files "
        "next to it, and do not add a package.json or any build config. Overwrite that one file "
        "if you need to change it; do not add src/ or a second HTML file.",
        "- Libraries may be imported only from these hosts: "
        + ", ".join(STATIC_PAGE_ALLOWED_HOSTS)
        + ". Everything else must be inline or a data: URI. No other remote hosts, and no "
        "external image files -- generate textures procedurally, draw them on a canvas, or "
        "inline a tiny data: URI placeholder.",
        f"- Keep the finished file under {STATIC_PAGE_MAX_BYTES // 1000} KB.",
    ]
    if cinematic:
        shape_rules = [
            "- The page must render into a <canvas> with a working WebGL context, and must actually "
            "issue draw calls: a canvas that stays empty fails the gate that follows this phase.",
            "- The animation loop must keep running after load (requestAnimationFrame), so the scene "
            "is measurably still moving a second later, not a single static frame.",
            "- No uncaught errors and no console errors, at any point during load or the first "
            "seconds of interaction.",
            "- No horizontal scrolling at 1280px or at 768px wide, and every interactive element "
            "must be at least 24x24px at 768px.",
            "- Any text sitting over the 3D scene must have its own backing surface -- a frosted or "
            "solid panel, or at minimum a text shadow. Text painted directly onto a moving scene "
            "has no measurable contrast, and the gate rejects it.",
            "- Comment the code: what each section of the scene setup does, and why non-obvious "
            "numbers were chosen.",
            "- Do not emit a rotating cube or a stock particle network unless the brief is about "
            "networks or data in space. The scene must read as this brief's world.",
        ]
    else:
        shape_rules = [
            "- Do not add WebGL or Three.js unless the client asked for a 3D or cinematic scene.",
            "- This is a website a client would open, not a raw unstyled form. Required structure: "
            "a header or nav, a hero with an h1, one supporting sentence and a primary button or "
            "link, at least two <section> blocks, and a footer.",
            "- Author real CSS in a <style> tag: body margin 0, a max-width content column, "
            "padding, and designed buttons (padding, not the browser default). Do not ship "
            "Times-on-white unstyled controls.",
            "- Put the client's copy and controls in real HTML elements (headings, paragraphs, buttons), "
            "not as textures on a canvas.",
            "- JavaScript must be valid: no top-level return, no broken syntax, no references to files "
            "that do not exist.",
            "- No uncaught errors and no console errors, at any point during load or the first "
            "seconds of interaction.",
            "- No horizontal scrolling at 1280px or at 768px wide, and every interactive element "
            "must be at least 24x24px at 768px.",
        ]
        if brief.elena_design_concept is not None:
            shape_rules.insert(
                0,
                "- Elena animates the client plate as the full-viewport atmosphere: if "
                "elena_background.webp is in the workspace, use <img src=\"elena_background.webp\"> as "
                "the background. Slow Ken Burns (~30s), breathing sunlight, optional 2d-canvas water "
                "glints, overlay chrome (name, nav, CTA, headline) unless the brief is a single card, "
                "frosted/solid panel for all text, and freeze motion when prefers-reduced-motion is set.",
            )
    lines = [
        f"Build ONE self-contained file, {STATIC_PAGE_FILENAME}, at the project root. This is NOT "
        "a React/Vite project and NOT an npm project: no build step, no bundler, no framework "
        "scaffolding, no package.json, and no source files beside it. All HTML, CSS and "
        "JavaScript live inline in that one file.",
        "",
        f"Goal: {brief.goal}",
        f"Context: {handoff.context_summary}",
        "",
        "What the page must contain and do (from the approved requirements):",
        *[f"- {item}" for item in requirements],
        "",
        *(["Additional notes from the client, who reviewed this instruction before it was sent:",
           addition_text,
           ""] if addition_text else []),
        "Visual direction: follow the client's own description above -- the colours, mood and "
        "typography they asked for are authoritative. Do not substitute a different palette.",
        "- Paint this brief's subject, not a leftover scene from another order: a bakery is a "
        "bakery, a climate brand is that world, a card is a card.",
        "- Unless the brief is a single card, use overlay editorial chrome on a full-viewport "
        "atmosphere: mark and name, text nav, one CTA, headline. Put copy on a backing surface. "
        "Do not reduce the page to one centered business card.",
        "",
        *(_elena_static_page_lines(brief, handoff)),
        "Strict rules for this phase (these take precedence over the client's additional notes above):"
        if addition_text
        else "Strict rules for this phase:",
        *shared_rules,
        *shape_rules,
        "",
        "Do not expose secrets in logs, reports, or generated files.",
        f"After {STATIC_PAGE_FILENAME} is complete, stop and exit. Do not keep rewriting it.",
    ]
    return "\n".join(lines)


def _elena_static_page_lines(brief: ProjectBrief, handoff: AgentHandoff) -> list[str]:
    lines: list[str] = []
    concept = brief.elena_design_concept
    if concept is not None:
        lines.extend(
            [
                "Elena's approved visual concept (motion and backing; do not replace the client's copy):",
                f"- Direction: {concept.visual_direction}",
                f"- Layout: {concept.layout}",
                *[f"- {note}" for note in concept.accessibility_notes],
                "",
            ]
        )
    preview_lines = tuple(handoff.design_preview_summary or ())
    if preview_lines:
        lines.append("Elena design-preview notes:")
        lines.extend(f"- {item}" for item in preview_lines[:12])
        lines.append("")
    return lines


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
