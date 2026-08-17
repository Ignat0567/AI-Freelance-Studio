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

Five live runs are archived in `demo/transcripts/` (2026-08-16 and 2026-08-17):

| Metric | Value |
|---|---|
| Runs reaching `completed` | 3 / 5 |
| Runs completing with **zero** repair attempts | 0 / 5 |
| Duration of the successful runs | 1382 s, 1335 s, 1166 s (19–23 min) |
| Failure causes | 1 provider (expired `claude` OAuth token, died after 18.7 s), 1 generated-code (visual gate never closed after 22 min) |
| Repair attempts per successful run | 1, 2, 2 |

Two things to note about this table. First, the failure causes are different in kind --
a dead token says nothing about code quality -- which is why every failure must now carry a
cause class. Second, `TestSummary.repair_attempts` was `0` in all five transcripts while the
event streams recorded 1 and 2, so none of the above could be read out of the structured
record; it had to be counted by hand out of prose. That is what Tuesday fixes.

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

## Changes to this file

The criterion may not be relaxed to fit what happened to get built. Any edit gets a dated
line here saying what changed and why -- so that moving the goalposts, if it happens, is at
least visible.

- 2026-08-17 — created.
