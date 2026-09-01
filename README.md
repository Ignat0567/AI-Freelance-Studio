# AI Freelance Studio

Local desktop studio that takes a freelance order in plain language and delivers a verified project folder.

```
order → brief → Grok spec → local Ollama coding → QA gates → delivery folder
```

## Run it (Windows)

1. Python venv with `pip install -r requirements.txt`
2. Node 20+ and `cd frontend && npm install`
3. Docker Desktop running (QA gates)
4. Ollama running with `qwen2.5-coder:14b`
5. Grok CLI logged in (`grok login` — the same `grok` as PowerShell, not an xAI API key)
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
