import importlib.util
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI
import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def force_import_local_module(module_name, filename):
    full_path = PROJECT_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ai_utils = force_import_local_module("ai_utils", "ai_utils.py")

app = FastAPI()
active = {}


@app.get("/test")
def test_endpoint():
    return {"status": "ok"}


@app.post("/start")
def start_pipeline():
    pid = str(time.time())
    active[pid] = {"status": "starting", "logs": []}

    def _run():
        active[pid]["logs"].append("Calling AI...")
        active[pid]["status"] = "running"
        try:
            t0 = time.time()
            resp = ai_utils.ask_studio_ai_with_history(
                provider="nvidia",
                model_name="meta/llama-3.3-70b-instruct",
                system_prompt="Reply with just OK",
                chat_history=[],
                temperature=0.2,
            )
            elapsed = time.time() - t0
            active[pid]["logs"].append(f"AI returned ({elapsed:.1f}s): {resp[:50]}")
            active[pid]["status"] = "done"
        except Exception as e:
            active[pid]["logs"].append(f"ERROR: {e}")
            active[pid]["status"] = "error"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"pid": pid}


@app.get("/status/{pid}")
def get_status(pid: str):
    return active.get(pid, {"status": "not_found"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
