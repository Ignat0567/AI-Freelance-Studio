# Two mechanisms from an AI code-generation pipeline

*Role-based model routing and a self-healing QA loop — and the rule that decides when a
model should be involved at all.*

---

## What the system does

AI Freelance Studio takes a freelance order in plain language and produces a working
project. The chain is:

```
order → planner → coder → reviewer → self-healing → container
```

- **planner** turns the order into an approved brief through a bounded clarification
  dialogue (`order_workflow/clarification.py`, `brief_service.py`)
- **coder** builds the project in phases, each its own scoped prompt to a coding CLI
  (`order_workflow/phased_adapter.py`)
- **reviewer** is a compiler, a test runner and a real headless browser — not a model
- **self-healing** re-prompts the coder with the reviewer's exact failure output, bounded
  (`order_workflow/phase_repair.py`)
- **container** builds a production image and probes it over HTTP (`order_workflow/deployment.py`)

Two parts of that are worth writing down, because they generalise past this project.

---

## 1. Role-based model routing

### The problem

Running the strongest available model for every phase is expensive and slow. Running the
cheapest is fine for scaffolding a page and not fine for wiring up WebSocket reconnection
logic. A single global model choice is wrong in one direction or the other, always.

### The mechanism

Each phase is classified, and the classification picks the model:

```python
# order_workflow/complexity.py
MODEL_FOR_COMPLEXITY: dict[PhaseComplexity, str] = {
    "routine": "sonnet",
    "complex": "opus",
}

def classify_phase_complexity(brief: ProjectBrief, *, focus_text: str) -> PhaseComplexity:
    text = focus_text.casefold()
    if any(keyword in text for keyword in _COMPLEXITY_KEYWORDS):
        return "complex"
    if len(brief.technical_constraints) >= 3 or len(brief.core_features) >= 5:
        return "complex"
    return "routine"
```

The model alias is then passed straight through to the coding CLI's `--model` flag.

### The two decisions that matter

**The classifier is not a model call.** This is the whole point. A model call to decide
which model to call costs a full round trip and gets the decision wrong in exactly the
same ways the downstream call would — same weights, same blind spots. The heuristic reads
signal the approved brief already contains. It is cheap, instant, and inspectable, and
when it is wrong it is wrong *legibly*: you can read the keyword list.

**`focus_text` is per-phase, not the whole brief.** Each phase passes only what that phase
is actually about:

```python
# the UI shell cares about the goal and the screens
focus_text=f"{brief.goal} {' '.join(brief.ui_requirements)}"

# the core-feature phase cares about the one feature being wired in
focus_text=f"{brief.core_features[0]} {brief.goal}"
```

Without this, a simple project containing one hard feature would route every phase to the
expensive model, or an otherwise-hard project would route its trivial scaffolding phase
there too. Complexity is a property of the *work item*, not of the project.

### The bug that only showed up when it was measured

The mechanism above shipped and looked fine. Then a live run printed its routing decisions
and both phases came back `opus`. So did the next one. A three-line probe answered why:

```
static page     substantive=0/3  ->  ui_shell=complex  core_feature=complex
book list       substantive=0/3  ->  ui_shell=complex  core_feature=complex
```

*A single page showing a name and a photo was being routed to the most expensive model.*

The cause was the second rule, `len(brief.technical_constraints) >= 3`. The brief generator
injects the recommended stack as technical constraints on **every** generic web app:

```
["React + Vite frontend", "FastAPI backend where required", "SQLite local storage"]
```

Exactly three, every time. The threshold fired on all of them, so every phase of every web
app classified as complex, and the keyword list — the part that carries the actual signal —
was dead code in the default path. The router had one branch.

The fix separates constraints that describe *the project* from constraints that describe
*the pipeline's own scaffolding choice*:

```python
def substantive_technical_constraints(brief: ProjectBrief) -> tuple[str, ...]:
    stack = brief.recommended_stack
    stack_terms = [t.casefold() for t in (stack.frontend, stack.backend, stack.storage) if t]
    return tuple(c for c in brief.technical_constraints
                 if not any(term in c.casefold() for term in stack_terms))
```

After the fix, the static page routes `routine`, and the PDF assistant — whose brief carries
five genuinely project-specific constraints (local vector indexing, provider abstraction,
"only retrieved fragments may leave the machine") — still routes `complex`.

Two things generalise here. First: a heuristic that is never measured against real inputs
will quietly degenerate, and it degenerates *silently* because every individual decision
still looks defensible in isolation. Second: the reason a decision was reached is worth as
much as the decision. Routing now emits its own justification into the event stream —

```
Sending ui_shell prompt to the coding CLI (model: opus -- complex: matched 'offline', 'notification')
```

— so a wrong route is visible from the outside, in the log, without re-running anything.
That single string is what turned "the router works" into "the router had one branch".

### What is deliberately not built

The keyword list is crude and will still misclassify. That is an accepted tradeoff: it is a
starting default, not a configuration format, and the failure mode is bounded — a misroute
costs money or quality on one phase, never correctness. The alternative designs considered
(a `roles.yaml` per-project config, a model-driven classifier) both add a moving part before
there is evidence the simple version is the bottleneck.

---

## 2. The self-healing loop

### The problem

Generated code frequently *almost* works. It compiles but the test fails; it builds but
renders a blank page. Regenerating the whole project from scratch throws away everything
that was right in order to fix the one thing that was wrong.

### The mechanism

Run QA. If it fails, hand the failure text back to the coder and run QA again. Bounded.

```python
# order_workflow/phase_repair.py
qa_outcome = qa_runner(qa_commands, qa_cwd)
while not qa_outcome.passed and attempts < max_attempts and not cancellation.is_cancelled():
    attempts += 1
    event_sink.emit(
        stage=stage, agent=agent, progress=65,
        message=f"QA failed; asking Codex to fix (attempt {attempts} of {max_attempts})",
        level=EventLevel.WARNING,
        details=(qa_outcome.failure_summary()[:2000],),
    )
    fix_result = opencode_client.execute_project_prompt(
        fix_prompt_builder(qa_outcome), workspace_path, event_sink, cancellation, model=model
    )
    if not fix_result.success:
        break
    qa_outcome = qa_runner(qa_commands, qa_cwd)
```

### The property that makes it work

**The loop is closed by an oracle, not by an opinion.**

An oracle is an external source of truth the model cannot argue with. In this pipeline:

| Gate | Oracle | What it actually proves |
|---|---|---|
| `npm run build` | compiler | the code is syntactically valid and type-consistent |
| `npm test` | test runner | the asserted behaviour holds |
| Playwright in Docker | a real headless browser | the page renders visible content, has interactive elements, throws no console errors |
| visual check | computed styles + WCAG maths | the painted palette matches the approved one, text clears AA contrast, the layout fits 375px |
| `pip install` + `python -c "import bot"` | interpreter | dependencies resolve, the module imports cleanly |
| `docker build` + HTTP probe | Docker + the network | the artifact runs as a deployable container |

Every one of those is a program, not a judgement. When the loop terminates, something
outside the model has confirmed the result. If the gate were instead "ask a model whether
this looks right", the loop would terminate when the model felt satisfied — which
correlates with the same model's failure modes, and tends toward agreeableness.

Empirically, across this project's live runs, every bug the pipeline caught was caught by
a compiler, a container, a browser, or an interpreter. None was caught by a model
reviewing another model's output.

### The hard case: making "does it look right" an oracle

Design fidelity is the assertion that most obviously wants a model. The design system
specifies a palette; the coding CLI ships something else; a human notices instantly. The
tempting fix is to hand a screenshot to a vision model and ask whether it matches.

Most of it does not need one. The approved design is not a vibe — it is a set of hex
values in `ElenaDesignConcept.light_theme`, and the browser will report exactly what it
painted. So the gate reads computed styles out of the live page and does arithmetic:

- **Palette adherence** — every colour actually painted, weighted by the area it covers,
  matched against the approved palette by Euclidean distance in RGB
- **WCAG AA contrast** — text colour against its *effective* background (walking up the
  tree past transparent parents), by the standard luminance formula
- **Mobile layout** — `scrollWidth - clientWidth` at 375px, and tap targets under 24px
- **Dark mode** — does the ground actually repaint under `prefers-color-scheme: dark`

Pointed at a Focus Timer the pipeline had already delivered — one that passed the build,
the tests and the render check, and looks perfectly decent in a screenshot:

```
Palette: 1/4 approved colours painted (25%).
Dark mode: page does not repaint under prefers-color-scheme: dark.
VISUAL CHECK FAILED:
- Only 25% of the approved palette appears on the page. Missing: #ffffff, #172033,
  #356cf6. Largest colours actually painted: #e9eef2, #7c8794, #2b3138, #5c6672.
  Use the approved palette instead of framework defaults.
- Contrast 3.13:1 (needs 4.5:1) -- #7c8794 on #e9eef2, affecting 8 text elements
  (e.g. "Workspace", "Sessions today"). Darken this text colour or lighten its
  background until it clears 4.5:1.
```

Both findings are real, and neither is arguable. The accent the designer chose never made
it onto the page. Eight labels sit below the accessibility floor — which no screenshot
review would have caught, because 3.13:1 looks fine until you measure it.

The output shape matters as much as the finding. Failures are grouped by colour pair, not
listed per element: one muted token reused across eight labels is *one* fix, and eight
near-identical lines would bury everything else. Each line carries the measured value, the
expected value, and the action. That is what makes the repair prompt that follows one pass
instead of three rounds of "make it look better".

What still needs a human: whether the layout is *good*, whether the copy is right, whether
the thing is beautiful. The gate does not pretend to know. It answers the mechanical half
completely and leaves the rest visibly alone.

### Why bounded, and why per-phase

`MAX_PHASE_REPAIR_ATTEMPTS = 2`. An unbounded repair loop burns money and can oscillate
between two broken states. Two attempts empirically catches the "forgot an import,
mis-typed a prop" class of failure, which is most of them; failures that survive two
attempts are usually structural and want a human.

The loop runs **per phase**, not once at the end. A failure surfaces while the context is
still small and localised — a broken UI shell is diagnosed against the UI shell prompt,
not against a finished three-layer application where the cause could be anywhere. Each
phase also checkpoints on success, so a retry resumes rather than rebuilding.

---

## 3. The rule underneath both

Both mechanisms are shaped by one question:

> **Does this decision have ground truth outside the model?**

If yes, get it from the oracle. If no, and the pipeline already resolved it upstream, read
it from there. Only what remains is worth a model call.

Applying that rule to this codebase removed two model calls that had looked reasonable:

**The backend-need gate.** A phase existed purely to ask a model "does this project need a
backend?" — a full CLI subprocess per order. But the clarification stage had *already*
asked the user who the software is for, and stored the answer in `brief.target_users`. The
model was re-deriving from prose a fact the pipeline had resolved upstream. It is now a
deterministic rule over the approved brief, fail-closed on anything it cannot classify:

```python
# order_workflow/phase_prompts.py
def decide_backend_need(brief: ProjectBrief, handoff: AgentHandoff) -> BackendDecision:
    ...
```

The function takes no `ai_ask` parameter, so the call cannot silently come back.

**The README overview.** One more CLI round trip per delivery, to write two sentences of
prose — for a README whose very next sections already list the features and the tech stack
from structured data. The model was rephrasing facts the same document contained, with room
to drift from them. Now assembled from the brief.

Neither removal lost information. Both were models answering questions the system already
knew the answer to.

The inverse also holds, and matters: the client-facing value proposition in
`order_workflow/proposal.py` is still a model call, deliberately. It is persuasion, it has
no deterministic equivalent, and it runs when a human asks for it rather than on every
execution. The rule is not "fewer model calls". It is "model calls where a model is the
right instrument".

---

## 4. What one real run looks like

A live run of `demo/run_end_to_end_demo.py` against the order *"a pomodoro focus timer that
runs entirely offline, with a desktop notification when the session ends"*:

```
[ORDER]     3 clarification questions asked, answered from recommended defaults
[PLANNER]   brief rev1 — 1 core feature, 3 constraints, Elena concept requested   6s
[UI_SHELL]  coding CLI (opus — complex: matched 'offline', 'notification')     8m 01s
            npm run build .............................................. passed  10s
            Playwright render check .................................... passed  10s
[CORE_FEAT] coding CLI (opus — complex: matched 'offline', 'notification')     6m 30s
            npm test ................................................... passed  10s
            Playwright render check .................................... passed  10s
[DECISION]  backend not needed — single local user, no sync/accounts       instant
[PACKAGING] docker build + HTTP probe → 200                                    15s
                                                                        ─────────
                                                              total       ~15m 40s
```

Repair attempts on this run: zero — QA passed first time at every gate. That is the
uninteresting case, and worth showing precisely because a demo that only works when
something goes wrong is not a demo of a working pipeline.

The generated project is a real React + TypeScript + Vite application: a countdown ring,
start/pause/reset, per-day session streak, and an honest "notifications are blocked"
state when the browser denies permission. It is served by the container the pipeline
built, on HTTP 200, and the timer counts down.

The two decisions this document is about are both visible in that trace: the model choice
carries its own justification, and every gate between the coding calls is a program rather
than a second opinion.

## 5. Honest limitations

- **Visual fidelity is checked mechanically, not aesthetically.** Palette, contrast and
  mobile layout are measured; whether the layout is *good* is not. Nothing here catches a
  page that hits every colour and is still ugly, or one whose copy contradicts itself.
- **The visual gate runs on the UI-shell phase only.** That is where the style spec enters
  the prompt and where repair is cheapest, and it keeps the run to one Playwright container.
  A later phase that repaints the ground would not be caught.
- **Mid-build clarification asks at one boundary, not continuously.** The run stops once,
  after the UI shell, and only about assumptions the brief already recorded. It cannot
  notice mid-phase that something the client never mentioned has become important.
- **The mid-build checkpoint is opt-in** (`FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION=1`)
  and blocks until answered. An unattended run must not stop halfway waiting for a human,
  so the default is off — which means the default pipeline still ships whatever the brief
  assumed.
- **The complexity classifier is keyword-based** and will misjudge projects whose difficulty
  is not signalled by vocabulary.
- **The repair loop fixes local errors, not architectural ones.** It closes the gap between
  "compiles" and "passes"; it cannot notice that the whole approach is wrong.
- **Container deploy is opt-in** (`FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY=1`). A Docker
  image build per order costs minutes, and generating projects is the product — hosting
  them is not.

---

## 6. Asking the client mid-build

The same "does this need a model" question applies to the pipeline's own dialogue.

Clarification runs entirely before the brief exists, against a client with nothing to look
at. Anything they could not picture in the abstract gets resolved by a recommended default
and recorded as an assumption — and the first time they see the consequence is when the
finished project lands.

So the run now stops once, at the UI-shell boundary: the screens exist and build, nothing is
wired on top of them, and a correction is still cheap. It asks about the assumptions the
brief itself recorded, plus one open question for whatever nobody anticipated.

The questions are a lookup, not a model call. *Which of your own assumptions should the
client confirm* is not a judgement, and a model asked to invent questions about a project it
just built produces plausible filler. A brief with no assumptions asks nothing at all —
a checkpoint that always fires teaches people to click through it.

The pause does not park a thread. The attempt ends cleanly with `AWAITING_USER` — already a
legal transition, and already outside the terminal set — and answering resumes exactly like
a retry: same execution id, same owned workspace, every finished phase skipped via its
checkpoint. Only the answers ride along, landing in the next phase's prompt *above* the
feature instruction, because a client who has seen the real shell outranks what the brief
assumed in the abstract.

Answers that confirm the build produce no corrections at all, rather than being forwarded
as instructions the coding CLI has to interpret.

## 7. Reproducing it

```bash
python demo/run_end_to_end_demo.py
```

Runs the whole chain against a real backend over real HTTP with live execution enabled,
prints every stage transition, and saves a JSON transcript to `demo/transcripts/`. Requires
Docker running and an authenticated `claude` CLI.

```bash
python demo/run_midbuild_clarification_demo.py
```

Same, with the mid-build checkpoint switched on. The script plays the client's part
programmatically, so the pause → answer → resume loop can be watched without a human sitting
in front of it.
