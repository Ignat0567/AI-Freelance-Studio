# Frozen fronts

Frozen as of **2026-08-17**, until MVP acceptance passes (see [MVP_ACCEPTANCE.md](MVP_ACCEPTANCE.md)).

One operator cannot advance ten fronts. Everything below stays alive and stays where it is,
so that all remaining attention goes to the one path that can be sold:

> **order → clarified brief → phased build → oracle gates → delivered, verified project**

An undeclared freeze still consumes attention, which is why this file exists. If a change is
not on the decisive path, it does not happen this week -- and it does not need to be
re-litigated either, because it is written down here.

## What "frozen" means

- It keeps working. Its tests keep passing; a change elsewhere must not break it.
- It gets **no new features**, no refactors, no polish.
- It is **not shown in demos**. A demo covers the decisive path only.
- A bug in a frozen front is fixed only if it blocks the decisive path.

## Frozen subsystems

| Front | Code |
|---|---|
| AI video generation | `video_generation/`, `api/video.py` |
| AI presentations | `presentation_generator/`, `api/presentation.py` |
| Marketplace | `api/marketplace.py`, its frontend feature |
| Knowledge base / embeddings | `embeddings/`, `api/embeddings.py` |
| Interactive Sandbox Test Lab | `sandbox_test_lab/`, `api/sandbox_test_lab.py`, `sandbox-test-lab-diagnostics/` |
| Team collaboration (chat, timeline, presence) | `collaboration/`, `api/collaboration.py` |
| Android device control | `android_device_control.py`, `api/android.py` |
| Website sections generator | `website_sections/` |
| GitHub integration | `api/github_integration.py` |
| Windows packaging, installer, code signing | `frontend/build/`, `INTERNAL_BETA_RELEASE.md` |
| "Liquid Glass" visual polish sprint | `design_system/`, `design_reference/` |

Deliberately **not** frozen, because they are the decisive path: `order_workflow/`,
`api/orders.py`, `api/system.py`, the order-workflow frontend, `provider_*.py`, and the
`demo/` runners.

## Also frozen: the set of QA gates

The pipeline currently gates on: `npm run build`, `npm test`, the Playwright render check,
the visual check, the state-continuity check, the functional smoke check, the static-page
check, dependency install plus import, and `docker build` plus an HTTP probe.

**No new gate is added before MVP acceptance.** Every gate costs wall-clock time on every
run and adds one more way for a run to fail. Adding gates feels like raising quality while
it lowers yield and lengthens the cycle -- and yield is the number this week is about.

Improving an *existing* gate's precision or its repair prompt is allowed. Adding a tenth
oracle is not.

## Measured and rejected

Not frozen because they are out of scope -- frozen because they were measured and the
numbers said no. Recorded here so they do not get re-proposed as obvious wins.

**Caching npm/node_modules between QA container runs, pre-pulling the QA image, merging the
browser gates into one container start** (2026-08-18). The premise was that container
cold-start dominates a run. Measured against the b06-reading-journal live run and a real
generated manifest:

| Where the wall clock goes | |
|---|---|
| coding CLI thinking | 1153 s — **91%** |
| all QA gates, containers included | 68 s — 5% |
| everything else | 51 s — 4% |

And a shared npm cache volume, timed on that project's real `package.json` in the actual
Playwright image: 7.8 s cold versus 7.1 s warm. **0.7 s saved per gate invocation**, roughly
4 s across a whole run — 0.3% of it, in exchange for a shared mutable volume and a refactor
across four gate modules.

The gates were never the bottleneck. Anything that matters for run time has to come out of
the 91%: fewer coding-CLI calls (i.e. fewer repairs), or shorter ones.

## Unfreezing

After MVP acceptance, one front at a time, and only against demand somebody has paid for.
Not "it would be nice", not "it is nearly done already".
