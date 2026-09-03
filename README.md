# AI Freelance Studio

Local desktop studio that takes a freelance order in plain language and delivers a verified project folder.

```
order → brief → coding worker (Ollama, Claude Code, Grok, OpenCode, or OpenRouter) → QA gates → delivery folder
```

Which worker writes the files is chosen per run on the execution screen, or defaults to the
first ready one in this order: local Ollama, OpenCode, Claude Code subscription, Grok
subscription, OpenRouter. As of 2026-09-03 the default pair in active use is **Ollama +
Claude Code subscription** (Grok's own subscription lapsed; its code and connection are
untouched and it stays selectable by hand).

## Run it (Windows)

1. Python venv with `pip install -r requirements.txt`
2. Node 20+ and `cd frontend && npm install`
3. Docker Desktop running (QA gates)
4. Ollama running with `qwen2.5-coder:14b`
5. At least one cloud coding worker logged in as a fallback: Claude Code CLI (`claude`) or Grok CLI (`grok login`)
6. Start the desktop app:

```bat
start.bat
```

That launches Electron, which starts the local backend.

## First-time Settings

1. **Settings → Grok Subscription** → Detect → Test Connection → Save
2. **Settings → General → Live coding execution** → on
3. Create Project → approve brief and Elena preview → **Start live build**

Simulation remains a secondary fake executor for UI checks. It does not write a client project.

## What you get back

A folder under `generated_projects/` with `README.md`, `delivery_report.md`, and (for pages/apps) `delivery_screenshot.png`.

Presentation pack: `docs/PRODUCT.md`, `docs/MVP-INSTALL.md`, `docs/AI-Freelance-Studio-MVP.pptx`. Windows installer: `frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe`.

## Operator runbook

One client order, from the Studio window, left alone until Result.

1. `start.bat`. Create Project. Product type **Website** (one HTML file) or **Small web application**. Fill title and description, or **Use bakery website example**. Create order (or Create & run automatically).
2. Answer clarification if asked, or use recommended defaults. **Approve brief**, then **Approve preview**.
3. On execution: pick **Who writes the project files** (default pair is local Ollama and the Claude Code subscription; Grok and OpenRouter stay available in the dropdown). Tick **Continue?** The checklist above the start button must be ok, not blocked. **Refresh readiness** if you just logged in or started Docker.
4. **Start live build**. Do not edit the order while it runs. The delivery folder is under `generated_projects/`. Result has **Open folder**, plus `README.md`, `delivery_report.md`, and `delivery_screenshot.png` when those files exist.

If Start live stays off, the checklist names the fix:

| Blocked on | Do this, then Refresh readiness |
|---|---|
| Claude Code CLI | `claude` login in a terminal, or switch the coding worker |
| Grok CLI (only if that worker is selected) | `grok login` in a terminal (the same `grok` as PowerShell) |
| Docker | Start Docker Desktop. QA runs inside it. |
| Ollama (only if that worker is selected) | `ollama serve`, then `ollama pull qwen2.5-coder:14b` |
| Live execution locked | Settings → General → Live coding execution → on |

If the run fails: **Retry this workspace** on Result. It reuses the same folder and does not repeat clarification, brief, or design-preview. Do not start a new order because the last one died in a few seconds with a provider/rate-limit banner — wait for the reset, then Retry. A stale Claude “session limit · 2:30pm Berlin” line is not a Grok block; change the coding worker if the selected one is actually exhausted.
