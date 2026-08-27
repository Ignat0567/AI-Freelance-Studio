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

## Criteria 1 and 2, in one pass (2026-08-27, 23:12)

**3 of 3 again, and this time every folder is complete in the same sequence.**

| order | outcome | duration | repairs |
|---|---|---|---|
| `b02-pricing-page` | succeeded | 144 s | 0 |
| `b06-reading-journal` | succeeded | 1583 s | 1 |
| `b03-focus-timer` | succeeded | 1585 s | 0 |

One repair across three orders, $16.58, 56 minutes, unattended. All three folders carry the
delivery report, the README agreeing with it, the checks' own output and a screenshot -- the
static page included, which is what the previous 3-of-3 could not show.

The reading journal, which failed six of its first eight runs, needed a single repair and its
`core_feature` phase passed every gate first time. The focus timer needed none at all.

Two of today's fixes were confirmed live in this run rather than in a test:

* The cleanup stopped two processes a build had left running inside the workspace -- a node
  process and the CLI's own shell -- and said so in the event stream (`5d99671`). Before
  today those outlived the run; three of them squatted the benchmark's own port last night.
* Findings named their element (`<div.ambient-layer> inside "Reading Journal Workspac"`), and
  the repair closed all three in one attempt.

Still true, and unchanged by any of this: **criterion 3 has not been started.**

## Criterion 1 is met (2026-08-27, 17:29)

**3 of 3, unattended, first time.**

| order | outcome | duration | repairs | cost |
|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 707 s | 0 | $0.65 |
| `b06-reading-journal` | succeeded | 2095 s | 2 | $11.64 |
| `b03-focus-timer` | **succeeded** | 1577 s | **0** | $8.06 |

Completion yield 100%, clean yield 67%, $20.35, 74 minutes wall clock, nothing touched while
it ran. The focus timer -- which had never once passed `ui_shell` inside a full sequence,
losing four acceptances to the provider's session limit and one to the stale bundle --
completed with no repairs at all.

What made the difference, all measured rather than guessed, all from the two days before it:
gates that rebuild before they judge (`e924850`), a repair budget set from the calls that
were being killed (`b8994d1`), findings that name the element they are about (`c011fe7`), a
third repair attempt taken from the convergence curves (`2581cb4`), and a state gate that no
longer demands a half-typed form survive navigation (`b2b1f41`).

**Criterion 2 was met for two of the three, and is now met for all three product kinds.** The
two web apps hand back a folder with the delivery report, the README agreeing with it, the
checks' own output, a screenshot and an HTTP 200 from the production image. The static page
had neither screenshot nor HTTP 200 -- its path builds no container, and the visual gate that
writes `delivery_screenshot.png` does not run for a single file -- so its report answered the
question with "container packaging was not part of this run": a statement where evidence
belongs.

Nothing new had to be built. The static-page check already served the page over HTTP from its
own local server and already had it open in a browser; it now records the status it receives
and photographs the page before the viewport is reshaped for the tablet measurement
(`9c53767`), and the report leads with what happened rather than what did not (`17d8155`).
Verified on a real delivery at 18:26:

```
## Proof it runs

The page was served over HTTP and answered 200 during its checks.
`delivery_screenshot.png` is the page as the check saw it, captured during the run.
```

That verification is a single-order run, not a sequence: the next full three-order run is what
shows all three folders complete in one pass.

**Criterion 3 is untouched.** Nobody but the author has ordered or paid for a delivery.

So: **the MVP criterion is not met.** Two of its three parts are -- criterion 1 in one
unattended sequence, criterion 2 across every product kind the pipeline builds. The third,
somebody other than the author ordering or paying for a delivery, has not been started.

## Where it stands (2026-08-27, second run of the day)

**1 of 3**, $9.19. `b02` succeeded in 336 s with no repairs; `b06` failed after two repairs;
`b03` died four seconds in on the provider's session limit, the fourth acceptance in a row
lost to it.

The b06 row is the first failure recorded since `4894780`, so for the first time the run says
what it failed on rather than that it failed:

```
before repair 2                          final verdict
- <div> cut off by 185px            ->   - <div> cut off by 28px
- <div> cut off by 333px            ->   - <div> cut off by 27px
- two texts overlapping by 100%     ->   (fixed)
  dark mode does not repaint        ->   dark mode repaints (#f5f5f7 -> #08080b)
                                    ->   - contrast 1.09:1 in dark mode (new, exposed by the fix above)
```

Repair 2 ran 865 s of its 900 and was **converging**: 185 px of clipping down to 28, 333 down
to 27, the overlap gone, dark mode working. What ran out was not the budget of a call but the
number of calls -- and that is now measured rather than asserted. Across the seven repair
sequences on record, counted as total pixels of damage per gate run:

| sequence | |
|---|---|
| `b03` 538 -> 85 | 84% down, closed |
| `b03` 451 -> 378 | 16% down, attempts ran out |
| `b06` 469 -> 3 | 99% down, closed |
| `b06` 618 -> 618 -> 55 | 91% down, attempts ran out |
| `b06` 968 -> 968, 469 -> 469, 510 -> 510 | 0% -- all three predate `e924850` |

The three flat sequences are exactly the runs where the gate was re-reading the pre-repair
bundle. Every sequence since converges, and two of four ran out of attempts mid-descent. So
`MAX_PHASE_REPAIR_ATTEMPTS` goes 2 -> 3 (`2581cb4`), the same decision the ceiling got
yesterday and made the same way.

Two other fixes came out of this run, both mine from earlier the same day:

* A gate finding that named no element (`<div> is cut off by 185px`, one of several hundred
  divs) sent a repair to edit an unrelated theme toggle for 239 s and $0.89. Findings now
  carry the selector that would find them (`c011fe7`).
* Printing a gate's findings killed a run outright: `UnicodeEncodeError` on a page's own
  glyph, with stdout at cp1251 because the run was redirected to a file. Eleven minutes and
  about $3 of build lost, no row written (`b10d0bf`).

## Where it stands (2026-08-27, on the 900s ceiling)

**1 of 3.** The first run made with repairs allowed to finish:

| order | outcome | duration | repairs | cost | cause |
|---|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 136 s | 0 | $0.39 | -- |
| `b06-reading-journal` | qa_failed | 1746 s | 2 | $7.44 | `generated_code` |
| `b03-focus-timer` | failed | 174 s | 0 | $0.68 | `provider` |

**The ceiling change did what it was raised to do, and it is the first claim this week that
its own numbers support rather than merely allow.** No call was stopped by a ceiling in this
run at all -- worst budget ratio 68%. b06's first repair took 616 s, which the old 450 would
have killed at 74% of the way through, and in those 616 s it went from three findings to one:
the 1280px clipping fixed, the dark-mode contrast fixed (the page now really repaints,
`#f5f5f7 -> #08080b`), and the 375px overflow down from 329 px to 3. The second repair took
281 s of its 900 and finished.

And then the gate failed the phase anyway, and **nothing recorded what it found**. Measuring
the delivered bundle afterwards with the gate's own rules -- clipping, viewport overflow, tap
targets, contrast in both colour schemes, text overlap, at 375 px and 1280 px -- every one of
them passes. So the run failed on something the artifact does not show and the record does
not name. That gap is closed in `4894780`: the event that ends a repair loop now carries the
gate's final findings, the way every repair *request* already did. The next run says what
this one could not.

The third order died on the provider's session limit again (2:40 am reset), the third
acceptance in a row lost to it and the third that says nothing about the pipeline.

## Where it stands (2026-08-26, third run -- on the gate fix)

**2 of 3.** The first run made on `04a2b93`, i.e. the first whose gates could see what its
repairs did:

| order | outcome | duration | repairs | cost | cause |
|---|---|---|---|---|---|
| `b02-pricing-page` | succeeded | 131 s | 0 | $0.45 | -- |
| `b06-reading-journal` | qa_failed | 1612 s | 2 | $3.06 | `generated_code` |
| `b03-focus-timer` | **succeeded** | 1875 s | 2 | $8.57 | -- |

**The focus timer completed for the first time.** It had never once got past `ui_shell`: two
attempts died on the provider's session limit and one on the stale bundle. Here its visual
gate closed after two repairs, the container served HTTP 200, and the delivered folder
answers criterion 2's four questions with its README and its delivery report finally saying
the same thing -- both now print `docker run ...` where the README used to say `npm test`.

The gate fix is confirmed in production rather than in a test: `dist/assets` rebuilt at 22:30
against source edited at 22:22, where before tonight the gate would have re-read the 22:11
bundle and returned its findings verbatim.

The one failure is now a clean statement instead of a confused one: b06's two repair calls
were both killed at 454 s, the second having written its first file at ~444 s. That is the
ceiling, not the generated code -- and it is what the section below acts on. Expect this row
to be the thing that changes on the next run at 900 s.

## The repair ceiling, decided (2026-08-26, evening)

**Raised 450s -> 900s.** This corrects the paragraph below, written this morning, which said
"the time ceilings are not what is short". That was read off 7 repair calls with 1 strike. It
is now read off 13, and the shape is different:

|  | morning (7 calls) | tonight (13 calls) |
|---|---|---|
| stopped at the ceiling | 1 (14%) | **4 (31%)** |
| finished: median | 173 s | 216 s |
| finished: p90 | 396 s (88%) | **411 s (91%)** |

Nearly a third killed, and the survivors pressed flat against the limit, is the shape of a
ceiling that is deciding outcomes rather than catching runaways. Two direct observations
fixed where to put the new one: tonight's killed calls wrote their **first file at ~444 s**,
so 450 was cutting exactly as output began landing; and build calls, doing strictly more work
under 1500 s, peak at 74%. 900 gives the writing half of a repair the room the reading half
took, and two repairs still fit inside one build's wall time. `bench/run_bench.py`'s per-run
timeout went 3600 -> 5400 to match.

Said plainly, because it matters more than the number: **the morning conclusion was drawn
from a sample taken while repairs were fighting the stale bundle** described below -- some of
those seconds were spent on work that could never have shown up. The corrected figure is not
"the same measurement, more data"; it is the first measurement of repairs that could actually
land. Both samples are recorded above rather than one replacing the other.

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
- 2026-08-27 (night) — criterion unchanged; a second 3-of-3, with all three delivery
  folders complete in the same sequence -- the verification the previous entry said was
  still owed. Criterion 3 remains untouched.
- 2026-08-27 (evening, later) — criterion unchanged; **criterion 2 met for every product
  kind**: the static page now ships the screenshot and the HTTP status its check already
  had. Verified on a delivered folder, not in a test.
- 2026-08-27 (evening) — criterion unchanged; **criterion 1 met**: three orders, one
  sequence, no intervention, all completed. Recorded together with the two parts that are
  still open, so that meeting one third of the criterion cannot read as meeting it.
- 2026-08-27 (afternoon) — criterion unchanged; a seventh attempt recorded at 1 of 3, the
  first whose failure says what the gate found. Repair attempts per phase raised 2 -> 3
  from the recorded convergence; no requirement touched.
- 2026-08-27 — criterion unchanged; a sixth attempt recorded at 1 of 3, the first with the
  900s repair ceiling. The ceiling behaved as intended (no strikes, a 616s repair that
  fixed two findings of three); the phase still failed, on a finding nothing recorded.
- 2026-08-26 (night) — criterion unchanged; a fifth attempt recorded at 2 of 3, the first
  on gates that can see their own repairs, and the first in which the focus timer ever
  completed.
- 2026-08-26 (evening) — criterion unchanged; the repair ceiling raised 450s -> 900s from
  13 recorded calls, and this morning's "the ceilings are not what is short" corrected in
  place rather than deleted.
- 2026-08-26 (evening) — criterion unchanged; a fourth attempt recorded, at 1 of 3, along
  with the reason: the browser gates were judging the build from before the repair, so no
  earlier run says what it appeared to say about repair quality. Nothing was softened --
  the worse number is written down as the worse number.
- 2026-08-26 — criterion unchanged; the third attempt recorded above, and the ceiling
  question closed against the honest sample it was waiting for. 2 of 3 is written down as
  2 of 3: the run that failed did so on the provider's session limit, and that is recorded
  as an external cause rather than counted as a pass.
