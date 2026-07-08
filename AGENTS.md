# AI FreelancerStudio Agent Rules

- Use Windows and PowerShell conventions for commands and paths.
- Inspect relevant files and current state before editing.
- Make the smallest compatible change that preserves existing behavior.
- Do not require git commits.
- Before each phase, use Git for checkpoints only if Git is already configured.
- If Git is not configured, do not initialize Git automatically and do not block work because no commit can be created.
- Before editing, create a timestamped backup copy of every file that will be modified.
- Store backups in a dedicated local backup folder outside `generated_projects`.
- Do not start the next phase automatically.
- Preserve the state machine, completion gates, OpenCode direct repair, full QA reruns, final audit, agents, and working features.
- Never weaken, delete, skip, or fake tests.
- Treat JSON repair as a legacy fallback only.
- Do not expose secrets in code, logs, reports, commits, or responses.
- Root Studio pytest must not collect `generated_projects/**/tests`.
- Run focused tests after each phase.
- Final reports must include files changed, backup location, tests run, exact results, and any remaining limitation.

## Key Files

- `main.py`
- `project_spec.py`
- `qa_engine.py`
- `opencode_bridge.py`
- `delivery_audit.py`
