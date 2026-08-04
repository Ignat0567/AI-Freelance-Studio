from __future__ import annotations

import asyncio
import json
import threading
import time
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_adapters import OpenAICompatibleLocalAdapter
from provider_contracts import AuthMethod, ConnectionType, ProviderConnection
from workflow_artifacts import RunArtifactStore
from workflow_contracts import ExecutionBrief


class Handler(BaseHTTPRequestHandler):
    mode = "stream"

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {"data": [{"id": "local-test-model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", "0") or "0")
        self.rfile.read(length)
        if Handler.mode == "500":
            self._json(500, {"error": "server error"})
        elif Handler.mode == "model_unavailable":
            self._json(404, {"error": "model missing"})
        elif Handler.mode == "delayed":
            time.sleep(2)
            self._json(200, {"choices": [{"message": {"content": "delayed ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
        elif Handler.mode == "malformed_sse":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: {not-json\n\n")
        elif Handler.mode == "close_mid_stream":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        elif Handler.mode == "non_stream":
            self._json(200, {"choices": [{"message": {"content": "non stream ok"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}})
        else:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"stream "}}]}\n\n')
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":2,"total_tokens":4}}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _brief(run_id: str) -> ExecutionBrief:
    return ExecutionBrief(run_id, "local-live", "OpenAI-compatible live", "Return ok", str(Path.cwd()), ["reply"], ["no edits"], ["text event"], ["."], [".."], ["run"], ["python -m pytest -q test_provider_layer.py"], [], False, False, "never", "read-only", {"artifact_run_id": run_id})


async def _collect(conn: ProviderConnection, run_id: str):
    return [event.to_dict() async for event in OpenAICompatibleLocalAdapter(conn).execute(_brief(run_id))]


async def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"provider-openai-compatible-live-{stamp}"
    store = RunArtifactStore(run_id)
    store.initialize({"validation": "openai_compatible_loopback"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    results = {}
    try:
        base = {"connection_id": "local-live", "provider_id": "local", "connection_type": ConnectionType.OPENAI_COMPATIBLE_LOCAL.value, "auth_method": AuthMethod.LOCAL.value, "display_name": "Loopback", "model_id": "local-test-model", "endpoint": endpoint}
        conn = ProviderConnection(**base)
        models = await OpenAICompatibleLocalAdapter(conn).list_models()
        results["models"] = [item.to_dict() for item in models]
        Handler.mode = "non_stream"
        results["non_stream"] = await _collect(ProviderConnection(**{**base, "metadata": {"stream": False}}), run_id)
        Handler.mode = "stream"
        results["stream"] = await _collect(conn, run_id)
        Handler.mode = "malformed_sse"
        results["malformed_sse"] = await _collect(conn, run_id)
        Handler.mode = "500"
        results["server_500"] = await _collect(ProviderConnection(**{**base, "metadata": {"max_attempts": 1}}), run_id)
        Handler.mode = "model_unavailable"
        results["model_unavailable"] = await _collect(ProviderConnection(**{**base, "metadata": {"max_attempts": 1}}), run_id)
        Handler.mode = "delayed"
        results["timeout"] = await _collect(ProviderConnection(**{**base, "metadata": {"timeout_seconds": 1, "stream": False, "max_attempts": 1}}), run_id)
    finally:
        server.shutdown()
        thread.join(timeout=5)
    store.write_json("provider/live_validation.json", results)
    ok = bool(results.get("models")) and results["stream"][-1]["type"] == "completed" and results["non_stream"][-1]["type"] == "completed" and results["malformed_sse"][-1]["type"] == "error" and results["server_500"][-1]["type"] == "error" and results["model_unavailable"][-1]["type"] == "error"
    status = "PASS" if ok else "FAIL"
    summary = {"endpoint": endpoint, "checks": {key: value[-1]["type"] if isinstance(value, list) and value and isinstance(value[-1], dict) and "type" in value[-1] else "ok" for key, value in results.items()}}
    store.finalize(status, summary)
    print(json.dumps({"run_id": run_id, "status": status, **summary}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
