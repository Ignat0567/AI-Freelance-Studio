# MVP acceptance criterion

Set **2026-08-17**. Target: **2026-08-23**.

This file defines "done" for the MVP so that "done" cannot drift. It is the only definition;
a passing test suite, a written document, and a clean refactor do not count toward it.

## The criterion

MVP is reached when all three hold:

1. **Three orders of different kinds, started one after another with no human intervention,
   each reach `completed`.** Not "would have completed if the token had been fresh", not
   "completed after I fixed something mid-run". Started, unattended, completed.

2. **Each order hands back a folder a stranger can open and understand**, answering four
   questions without asking anyone: what was built, which defects were found and fixed,
   proof that it runs (screenshot + HTTP 200), and how to start it.

3. **Somebody other than me has paid for one such result, or at minimum placed an order for
   one.** Symbolic money counts. A promise does not.

## Explicitly out of scope

Not part of this criterion, deliberately, and not to be worked on before it is met:

- Windows code signing, SmartScreen, notarisation
- A clean-machine install/uninstall run
- Marketplace, AI video, AI presentations, Sandbox Test Lab, knowledge base, collaboration
  (see [FROZEN.md](FROZEN.md))
- Any new QA gate
- Self-serve onboarding, billing, accounts, hosting

Reasoning: the first ten clients do not download an installer. They watch their own order get
built. Everything above matters for the *final* product and none of it is on the path to the
first paid delivery.

## Baseline measured before the work started

Eight live runs archived in `demo/transcripts/` (2026-08-16 and 2026-08-17), computed with
`python bench/report.py --transcripts demo/transcripts --per-run`:

| Metric | Value |
|---|---|
| Completion yield | **50%** (4/8) |
| Clean yield — completed with **zero** repairs | **0%** (0/8) |
| Duration, median / p90 (successful runs) | 1359 s / 1477 s (22.6 / 24.6 min) |
| Repair attempts, total | 8 |
| Repairs by gate | `ui_shell/visual` 4, `core_feature/qa` 4 |
| Failures by cause | provider 2, generated_code 1, budget 1 |

Three things this table says that the prose did not.

**Nothing has ever run clean.** Every single completed run needed one or two repairs. Clean
yield, not completion yield, is the number that says the generated code is improving rather
than the repair loop getting more patient.

**The two blame the visual gate deserves are exactly half.** `core_feature/qa` demanded as
many repairs as `ui_shell/visual` did. Wednesday's design-token work addresses the visual
half only, and this row is how that claim gets checked rather than assumed.

**The four failures are four different kinds of thing.** Two provider (a dead token), one
generated code (a gate that never closed), one budget (a timeout). Averaging them into "half
the runs failed" measures nothing, which is why every failure now carries a cause class.

The whole table is now derived by a program from the runs' own records. It could not be
before: `TestSummary.repair_attempts` read `0` in every transcript while the event streams
showed one and two, so these numbers had to be counted by hand out of prose.

## How acceptance is verified (Saturday)

1. Fresh clone of the repository into an empty directory; install as documented.
2. Preflight must pass before anything else is attempted.
3. Three orders, one per kind, launched back to back and left alone:
   - a static single page,
   - a local CRUD app with several screens,
   - one with a genuinely hard requirement (offline, notifications, or similar).
4. For each: record cause class on failure, wall-clock duration, repair attempts per gate,
   and whether the delivery folder answers the four questions in criterion 2.
5. Fix only what broke. Improve nothing.

## Where it stands (2026-08-24)

**1 of 3. Not met.** Two attempts, both from a fresh clone.

The first attempt failed 3 of 3 in 34 seconds, all with `workspace_root_unavailable`:
`generated_projects/` is gitignored and nothing created it, so a clean checkout could not run
a single order. Invisible from a working tree, where that directory has existed since the
first run, and missed by every test and every live delivery before it. Fixed in `36f5470` --
which is the clearest argument for keeping "fresh clone" in step 1 above.

The second attempt, at `36f5470`:

| order | outcome | duration | repairs | cause |
|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 117 s | 0 | — |
| `b06-reading-journal` | qa_failed | 1666 s | 2 | `generated_code` |
| `b03-focus-timer` | failed | 4 s | 0 | `provider` |

The two failures are different in kind and should not be averaged. The third order died on
the provider's session limit, costing four seconds and nothing, and says nothing whatever
about the pipeline. The second is the real result: the visual gate never closed, and its
first repair was killed by the 450s ceiling.

Open, in order:

1. **The time ceilings.** 450 s for a repair and 1500 s for a build both look low for
   multi-screen apps, but that impression rests on a sample the ceiling itself was deleting
   -- a killed call emitted no timing at all until `b55028a`. The honest sample starts now;
   decide from it, not from the impression.
2. **Re-run acceptance** when the provider's session limit is free, or the third order dies
   for nothing again.
3. **Criterion 3** -- somebody other than the author paying for a delivery -- is untouched.

## Changes to this file

The criterion may not be relaxed to fit what happened to get built. Any edit gets a dated
line here saying what changed and why -- so that moving the goalposts, if it happens, is at
least visible.

- 2026-08-17 — created.
- 2026-08-17 — baseline table replaced with the machine-computed figures over eight archived
  runs (was three of five, counted by hand). The criterion itself is unchanged.
- 2026-08-18 — criterion unchanged; the parts of it that were unmet are now built. The
  delivery folder answers all four questions (delivery_report.md), ships the checks' own
  measurements (qa_evidence.md) and the screenshot the criterion names
  (delivery_screenshot.png, captured by the visual gate and verified in a container against
  a fixture page). What still needs live runs: the three unattended orders, and someone
  paying for one.
- 2026-08-24 — criterion unchanged; a "Where it stands" section added above recording two
  acceptance attempts and what is still open. No requirement was softened: 1 of 3 is written
  down as 1 of 3.
