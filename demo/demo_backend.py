"""Backend launcher for the end-to-end demo.

A real uvicorn server with live execution enabled, bound to a fixed demo port with a
fixed local token so the demo script can drive it over real HTTP. Separate file rather
than an inline `python -c` so the demo is readable, and so sys.path can be bootstrapped
explicitly -- Python puts *this file's* directory on sys.path, not the working directory,
so the repo root has to be added by hand for `main` and `backend_security` to import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ["FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION"] = "1"
# The demo exists to show the whole chain, including the last link: build a production
# container from the generated project and prove it serves over HTTP. Off by default in
# normal operation -- an image build per order costs minutes.
os.environ["FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY"] = "1"

import uvicorn  # noqa: E402

from backend_security import LocalSecurityContext, set_app_security_context  # noqa: E402
from main import app  # noqa: E402
from test_security_support import TEST_LOCAL_TOKEN  # noqa: E402

PORT = int(os.environ.get("FREELANCERSTUDIO_DEMO_PORT", "8099"))

set_app_security_context(
    app,
    LocalSecurityContext.create(
        token=TEST_LOCAL_TOKEN,
        bind_host="127.0.0.1",
        port=PORT,
        launch_id="e2e-demo",
    ),
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
