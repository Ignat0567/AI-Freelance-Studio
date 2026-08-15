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
| `pip install` + `python -c "import bot"` | interpreter | dependencies resolve, the module imports cleanly |
| `docker build` + HTTP probe | Docker + the network | the artifact runs as a deployable container |

Every one of those is a program, not a judgement. When the loop terminates, something
outside the model has confirmed the result. If the gate were instead "ask a model whether
this looks right", the loop would terminate when the model felt satisfied — which
correlates with the same model's failure modes, and tends toward agreeableness.

Empirically, across this project's live runs, every bug the pipeline caught was caught by
a compiler, a container, a browser, or an interpreter. None was caught by a model
reviewing another model's output.

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

- **No visual-fidelity check.** The pipeline verifies that a page renders and is
  interactive. It does not verify that it looks like the design specification it was given.
  `functional_smoke_check.py` says so in its own docstring. This is the largest open gap.
- **Clarification is front-loaded.** Questions are asked before the brief exists, capped at
  three rounds. There is no mechanism to ask the client something mid-build.
- **The complexity classifier is keyword-based** and will misjudge projects whose difficulty
  is not signalled by vocabulary.
- **The repair loop fixes local errors, not architectural ones.** It closes the gap between
  "compiles" and "passes"; it cannot notice that the whole approach is wrong.
- **Container deploy is opt-in** (`FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY=1`). A Docker
  image build per order costs minutes, and generating projects is the product — hosting
  them is not.

---

## 6. Reproducing it

```bash
python demo/run_end_to_end_demo.py
```

Runs the whole chain against a real backend over real HTTP with live execution enabled,
prints every stage transition, and saves a JSON transcript to `demo/transcripts/`. Requires
Docker running and an authenticated `claude` CLI.
