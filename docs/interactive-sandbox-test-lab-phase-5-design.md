# Interactive Sandbox Test Lab — Phase 5 Design (Proposed)

## Status

This is a **design/roadmap document for proposed, not-yet-built work**. Unlike the other documents in this directory (Phases 1 through 4E), which describe completed, shipped functionality, nothing described below exists in code yet. It exists to break a large product-roadmap goal into implementable, independently reviewable slices before any of them is scheduled.

## Why

The product roadmap's first major post-current-work item is "Interactive Sandbox Test Lab": live visibility into what's happening inside the Windows Sandbox VM from the Studio UI, an AI agent driving actions inside it automatically, the ability for the human to take over manual control at any moment, video/log/screenshot evidence collection, and using Sandbox as a full QA environment — not just a fixed self-test.

What exists today (Phases 1 through 4E) is a closed, one-shot, batch self-test harness: it installs this app's own pinned installer inside an isolated Sandbox VM, verifies install/launch/backend-health, captures one screenshot, and shuts down. It proves the harness is reliable (Phase 4E added durable diagnostics and validated a real end-to-end pass), but it is not interactive in any of the roadmap's senses. This document defines the gap as five sub-phases.

## What already exists that's reusable

- **Windows Sandbox already renders as a real, visible, interactive window on the host** — not headless. `sandbox_test_lab/sandbox_process_model.py` already tracks two process models: the legacy `WindowsSandboxClient.exe` and the newer `WindowsSandboxRemoteSession.exe`/`WindowsSandboxServer.exe` RDP-style pair (`sandbox_session.py`'s `capture_owned_sandbox_session` probes for both). Today's code only ever uses this window for ownership/cleanup (`CloseMainWindow`/`Kill`) — never reads or writes its content, and never expects a human to click into it.
- **Screenshot capture (Phase 3B) is the one proven capture primitive.** It runs entirely guest-side: `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)` with a `CopyFromScreen` fallback, writing a PNG into the Sandbox-mapped evidence folder that the host polls on the filesystem. This is the direct basis for any live-view work — but today it captures exactly one frame, then the guest shuts itself down immediately.
- **Nothing else exists yet**: no window embedding/reparenting, no input injection (`SendInput` or equivalent) anywhere in the guest scripts, no video capture, and no channel for the host to send new commands to an already-running guest session.
- **The guest execution model is deliberately one-shot, zero-argument, and hash-pinned.** The entry script accepts no command, path, or arguments; it runs one fixed, SHA-256-verified payload once and calls `shutdown.exe` immediately after. `docs/interactive-sandbox-test-lab-phase-3b.md` documents this as an explicit security boundary ("no dynamic source"), not an oversight. Any interactive/agent-driven mode has to work *with* this invariant, not against it.

## Phase 5a — Manual Takeover (validate the cheapest win first)

**Goal:** confirm whether a user can already click directly into the visible Sandbox window and control the guest, with zero new capture or streaming engineering.

**Scope:**
- Empirical verification (manual, one-time, outside this codebase): launch Sandbox through the existing harness, don't let it auto-close, and test whether clicking into the `WindowsSandboxClient`/`WindowsSandboxRemoteSession` window gives normal keyboard/mouse control of the guest.
- If confirmed: add a new "interactive session" launch mode, distinct from today's fixed self-test profiles, that starts Sandbox and keeps the window open and focused instead of running the current automatic capture-and-close flow. The UI's only new control is an explicit "End Session" action.

**Out of scope:** any capture, streaming, or embedding — this phase is purely "get out of the user's way."

**Risk:** this is genuinely unverified by anything in this repo — it's a property of Windows Sandbox itself, not something the codebase proves. Must be tested empirically before the rest of the plan leans on it.

**Open question:** does `ProtectedClient=Enable` or any other current `.wsb` flag (`wsb_config.py`) interfere with direct interaction? Needs a real test, not a guess.

## Phase 5b — Live View In The Studio UI

**Goal:** show sandbox activity live inside the Electron app, without the user needing to alt-tab to a separate window.

**Scope:**
- Guest-side: turn the existing one-shot `PrintWindow`/`CopyFromScreen` capture into a repeating loop, writing timestamped frames to the mapped evidence folder at a fixed interval instead of once before shutdown.
- Host-side: a new poller reads the latest frame and exposes it through a new IPC channel — today's `sandbox-test-lab-*` channels in `frontend/main.js` are strictly request/response (capabilities/launch/status/cancel), nothing streams.
- Frontend: new rendering surface in `frontend/src/features/sandbox-test-lab/` (today's `SandboxTestLabPage.jsx` renders zero sandbox pixels — this is greenfield UI work, not an extension of existing rendering).

**Risk / cost:** polling a mapped folder is not a real video protocol — expect a rough frame rate and visible latency, adequate for "watch progress" but not for smooth manual interaction (that's what 5a covers, if confirmed).

**Open question:** sustained capture cost inside a VM with `VGpu=Disable` — needs a performance check before committing to a target interval.

## Phase 5c — Agent-Driven Actions (the real architectural fork)

**Goal:** let an AI agent click, type, and navigate inside the guest as part of an automated QA flow.

**Scope:**
- Guest-side input injection: new `SendInput`-based (or equivalent) primitives, following the existing P/Invoke pattern already used for `PrintWindow`/`PostMessage`.
- Command channel — the hard part: today's guest is one-shot and argument-free by deliberate design, and that invariant must not simply be relaxed. Proposed approach: a **separate**, still hash-pinned, still argument-free guest entry script — a fixed "QA agent" payload — that, once started, polls the mapped folder for new command *files* matching a constrained, schema-validated vocabulary (e.g. `{"action": "click", "x": ..., "y": ...}` / `{"action": "type", "text": "..."}`) — never arbitrary code or shell commands. This keeps "no dynamic source" intact (the executable logic stays 100% pinned) while allowing dynamic *parameters* inside a closed schema, generalizing the same request.json pattern Phase 4E already uses from a single read into a loop.

**Risk:** the single biggest security-review item in this whole plan. Any looseness in the command schema becomes an execution primitive inside the VM — still sandboxed, but deserves the same rigor as the rest of this codebase's evidence validation (allow-listed fields, bounded values, no path/string injection). This needs its own dedicated security design pass before implementation, not a review after the fact.

**Depends on:** 5b, so the agent (and a human watching) can see the effect of its actions.

## Phase 5d — Video Evidence

**Goal:** retain session video as QA evidence, not just a single screenshot.

**Scope:** extend 5b's frame loop to persist a bounded, retained sequence — reusing the diagnostics-archival pattern shipped in Phase 4E (bounded retention count, safe-only content, best-effort and never fatal to the run). Simplest version: keep the frame sequence plus timestamps as-is. Full version: proper video encoding.

**Depends on:** 5b.

**Open question:** encode guest-side (adds a guest dependency) or host-side (simpler, more standard tooling, consistent with the existing "guest stays minimal, host does the heavy lifting" pattern from Phases 1-4E)? Leans host-side.

## Phase 5e — General-Purpose QA Environment

**Goal:** use the interactive sandbox to QA arbitrary AI-generated projects — the delivery pipeline's actual output — not just this app's own pinned installer.

**Scope:** the largest generalization in this plan. Everything today (profiles, trusted-artifact pinning, hardcoded window titles and executable names) is hardcoded to this app's own build. Supporting arbitrary generated projects needs: per-project artifact staging in place of the pinned-hash model, a way to define "success" for a project with no fixed window title or backend endpoint to check, and reconciling the current hard `Networking=Disable` against projects that plausibly need network access to run at all.

**Depends on:** 5a-5c functionally complete. This phase is intentionally last and most open-ended — it likely needs its own separate design pass once the earlier phases exist and their real constraints are known.

## Recommended sequencing

5a (cheapest, resolves the biggest unknown) → 5b (foundational for everything visual) → 5d (extends 5b, low incremental risk) → 5c (hardest, most security-sensitive) → 5e (largest scope, do last).

## Not covered by this document

Concrete API contracts, exact `.wsb` config changes, the exact command-vocabulary schema for 5c, and effort/timeline estimates. Those belong in per-sub-phase design docs once a sub-phase is actually scheduled, matching how Phases 2a/2b/2c and 3a/3b were each documented separately as they were built.
