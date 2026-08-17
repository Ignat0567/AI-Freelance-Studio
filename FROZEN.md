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

## Unfreezing

After MVP acceptance, one front at a time, and only against demand somebody has paid for.
Not "it would be nice", not "it is nearly done already".
