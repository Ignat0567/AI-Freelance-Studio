import sys, os, json, threading, time
os.chdir("E:\\Python\\OpenCode\\FreelancerStudio")

from importlib import util as ilu
spec = ilu.spec_from_file_location("ai_utils", "ai_utils.py")
ai_utils = ilu.module_from_spec(spec)
sys.modules["ai_utils"] = ai_utils
spec.loader.exec_module(ai_utils)

from fastapi import FastAPI
import uvicorn

app = FastAPI()

active = {}

@app.get("/test")
def test_ep():
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
