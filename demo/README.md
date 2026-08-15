# End-to-end demo

One order, taken all the way from plain text to a running container, over the real HTTP
API against a real backend. No fakes, no in-process shortcuts.

```
order → planner → coder → reviewer → self-healing → container
```

## Run it

```bash
python demo/run_end_to_end_demo.py
```

Takes roughly 15–20 minutes, almost all of it the coding CLI actually writing the project.

**Prerequisites**

| Requirement | Check |
|---|---|
| Docker running | `docker info` |
| `claude` CLI authenticated, with session quota left | `claude --version` |
| Python venv | `.venv/Scripts/python.exe` (falls back to `sys.executable`) |

The CLI's session limit is the usual reason a run dies early. It fails fast and says so —
`rate_limit_message` in the transcript carries the reset time.

## What it prints

Every stage transition, live, with timestamps. The two mechanisms worth watching are
called out explicitly at the end:

- **role-based model routing** — which model each phase went to *and why*
  (`opus -- complex: matched 'offline', 'notification'`)
- **the self-healing loop** — every QA gate, and any repair attempts that followed

A full JSON transcript lands in `demo/transcripts/`. One successful run is committed as a
worked example.

## What it proves

| Link | Evidence in the output |
|---|---|
| order | 3 clarification questions, answered from recommended defaults |
| planner | approved brief rev1, Elena design concept, handoff ready |
| coder | two scoped phases, each its own CLI call with its own model |
| reviewer | `npm run build`, `npm test`, Playwright render check — all in Docker |
| self-healing | repair attempts per phase, bounded at 2 |
| container | `docker build` + HTTP probe from outside the container → 200 |

The reviewer is deliberately not a model. It is a compiler, a test runner and a real
headless browser. See [`docs/model-routing-and-self-healing.md`](../docs/model-routing-and-self-healing.md).

## Recording it

The script is written to be filmed: single command, no interaction, readable output.

1. **Reset to a clean state** so the run starts from nothing:
   ```bash
   echo '{"orders": {}}' > orders_state.json && echo '{}' > order_executions_state.json
   ```
2. **Widen the terminal** to at least 110 columns — the stage lines are aligned to that.
3. **Start recording, run the command.** The long silences are the CLI working; they are
   worth cutting. Natural cut points are the two `Sending … prompt to the coding CLI` lines.
4. **Land on the summary block** (`4/5 RESULT`) — routing, QA gates, deploy, all on one screen.
5. **Then show the artifact.** The delivery report prints the exact command:
   ```bash
   docker run --rm -p 3000:3000 freelancerstudio/<execution-id>:latest
   ```
   Open `http://localhost:3000`, press Start, let the countdown tick. That closes the loop
   on camera: a container running an app nobody wrote by hand.

`screenshots/focus-timer-deployed.png` is that final screen from a real run, captured with
Playwright against the deployed container (`screenshots/_shot.mjs`).

## The demo order

Fixed in `run_end_to_end_demo.py` so every run has the same shape — a pomodoro focus timer
that works offline with a desktop notification. It is chosen, not arbitrary:

- `offline` and `notification` are complexity keywords, so the build phases route to the
  stronger model and the routing line has something to show
- none of them are backend-need signals, so the run stays a clean two-phase frontend build
  instead of triggering a third bridging phase
- the result is visual and interactive, which matters when the last shot is a browser
