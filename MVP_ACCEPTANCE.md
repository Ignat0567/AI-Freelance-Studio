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

## Where it stands (2026-08-26, second run)

**1 of 3 on the fourth attempt** -- and the reason is the most important thing found this
week, because it was mis-attributed in every run before it.

| order | outcome | duration | repairs | cost | cause |
|---|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 379 s | 0 | $1.31 | -- |
| `b06-reading-journal` | qa_failed | 2047 s | 2 | $6.38 | `generated_code` |
| `b03-focus-timer` | qa_failed | 1744 s | 2 | $8.90 | `generated_code` |

All four repairs were `ui_shell/visual`, and not one of them closed its gate. Reading the
workspace rather than the summary:

```
17:05  dist/assets/index-CPAfjQJF.css   built, before the repair loop began
17:20  src/styles/global.css            edited by repair attempt 2
17:21  the visual gate                  three findings, word for word, off the 17:05 bundle
```

Every browser gate previews with `npm run preview`, which serves `dist/`. A repair edits
`src/`. So **a repair could only ever pass if the model happened to run a build of its own**
-- which is what separates the repairs that "worked" from the ones that changed nothing, and
what has been read as model variance since the first live run. Both failures above were
recorded as `generated_code`: the pipeline blamed the generated app for fixes its own gate
could not see. Fixed in `e924850`; the delivered Dockerfile always rebuilt from source, so
only the gates were judging a stale artifact.

This makes the two earlier results below **not comparable to what comes next**, and none of
them are evidence about repair quality. What they are evidence about is the cost of the
defect: today's two runs spent $26 and about two hours, and 4 of the 8 repair calls in them
were judged against a bundle they had not touched.

Open, in order:

1. **Re-run acceptance on `e924850` or later.** Nothing about repair yield, the ceilings, or
   the gates' precision can be read from a run made before this fix.
2. **Criterion 1** still needs three orders completing in one unattended sequence.
3. **Criterion 3** -- somebody other than the author paying for a delivery -- is untouched.

## Where it stood (2026-08-26, first run)

**2 of 3. Not met.** A third attempt, again from a fresh clone, at `2e995fd`:

| order | outcome | duration | repairs | cost | cause |
|---|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 335 s | 0 | $1.17 | -- |
| `b06-reading-journal` | succeeded | 2155 s | 4 | $6.64 | -- |
| `b03-focus-timer` | failed | 373 s | 0 | $1.44 | `provider` |

The reading journal completed for the first time since the ceiling work: its visual gate
closed after two repairs and its state gate after two more, and the delivered folder answers
all four of criterion 2's questions -- HTTP 200 from the production image, a screenshot, the
gates' own output, and a `docker run` line. The third order died on the provider's session
limit for the second acceptance in a row, this time 371 seconds into an opus build. The
pipeline said nothing wrong; the account ran out of week.

**The time ceilings are not what is short, and that question is now closed by data rather
than impression.** Seven repair calls are on record against the 450s limit: one was stopped
by it, and the six that finished have a median of 173 s and a p90 of 396 s. The ten build
calls against 1500 s peak at 55%. The single strike is the interesting one -- the call spent
its whole 454 seconds writing four Playwright scripts to re-find elements the gate had
already named, pixel offsets included, and edited no source file at all. Raising the ceiling
would have bought more of that. `ca8778a` tells the repair its findings are already
measured; `bench/budget.py` is how the next sample gets read.

Five defects were found by reading this run rather than by any test, all now fixed: the
delivered README told the client of a single HTML file to run `npm test` while the delivery
report in the same folder said to open it in a browser (`9dc2036`); a benchmark row outlived
the transcript it cites, because the transcript went to the clone and the row to the main
repository (fixed in this run's own commit, `2e995fd`); the live log printed that a gate failed but not what it found, while
a *replay* of the same run printed both (`9b09eac`); a core feature was sliced mid-word into
the client's delivery report (`52b1e40`); and the state gate demanded that a half-typed "Add
book" form and a search box survive navigation, which the repair satisfied by persisting
both to localStorage -- a product nobody ordered (`b2b1f41`).

Open, in order:

1. **Criterion 1 still needs three in one unattended sequence.** Two of the three are now
   proven on today's code; the third has never been given a working provider session.
2. **Criterion 3** -- somebody other than the author paying for a delivery -- is untouched.

## Where it stood (2026-08-24)

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
- 2026-08-26 (evening) — criterion unchanged; a fourth attempt recorded, at 1 of 3, along
  with the reason: the browser gates were judging the build from before the repair, so no
  earlier run says what it appeared to say about repair quality. Nothing was softened --
  the worse number is written down as the worse number.
- 2026-08-26 — criterion unchanged; the third attempt recorded above, and the ceiling
  question closed against the honest sample it was waiting for. 2 of 3 is written down as
  2 of 3: the run that failed did so on the provider's session limit, and that is recorded
  as an external cause rather than counted as a pass.
