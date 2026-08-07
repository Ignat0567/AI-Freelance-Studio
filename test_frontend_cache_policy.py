import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _run_cache_policy_probe(tmp_path: Path) -> dict:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/index-AbC123.js"></script>', encoding="utf-8")
    (assets / "index-AbC123.js").write_text("console.log('fresh');", encoding="utf-8")
    (dist / "manifest.json").write_text('{"name":"AI Freelance Studio"}', encoding="utf-8")

    code = r'''
import json

import main
from test_security_support import authorized_test_client

client = authorized_test_client(main.app)
responses = {
    "index": client.get("/"),
    "hashed_asset": client.get("/assets/index-AbC123.js"),
    "unhashed_asset": client.get("/manifest.json"),
    "api": client.get("/api/debug/frontend"),
    "spa_fallback": client.get("/settings/ai"),
}
print(json.dumps({name: {"status": response.status_code, "headers": dict(response.headers)} for name, response in responses.items()}))
'''
    env = os.environ.copy()
    env["FREELANCERSTUDIO_FRONTEND_DIR"] = str(dist)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_frontend_shell_and_assets_use_route_scoped_cache_policy(tmp_path):
    result = _run_cache_policy_probe(tmp_path)

    assert result["index"]["status"] == 200
    assert result["index"]["headers"]["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert result["index"]["headers"]["pragma"] == "no-cache"
    assert result["index"]["headers"]["expires"] == "0"
    assert result["spa_fallback"]["headers"]["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"

    assert result["hashed_asset"]["status"] == 200
    assert result["hashed_asset"]["headers"]["cache-control"] == "public, max-age=31536000, immutable"

    assert result["unhashed_asset"]["status"] == 200
    assert result["unhashed_asset"]["headers"]["cache-control"] == "no-cache, max-age=0"


def test_frontend_cache_policy_does_not_leak_to_api_or_downloads(tmp_path):
    result = _run_cache_policy_probe(tmp_path)

    assert result["api"]["status"] == 200
    assert "cache-control" not in result["api"]["headers"]


def test_electron_cache_invalidation_preserves_storage_and_loads_after_failure():
    source = Path("frontend/main.js").read_text(encoding="utf-8")
    create_window = source.split("async function createWindow", 1)[1].split("if (!app.requestSingleInstanceLock())", 1)[0]

    assert ".clearCache()" in create_window
    assert "mainWindow.loadURL(`${expectedRendererOrigin}/`)" in create_window
    assert create_window.index(".clearCache()") < create_window.index("mainWindow.loadURL")
    assert "catch (error)" in create_window
    assert "Failed to clear renderer HTTP cache before load" in create_window
    assert "clearStorageData" not in create_window
    assert "cookies" not in create_window.lower()
    assert "localstorage" not in create_window.lower()
    assert "indexeddb" not in create_window.lower()
    assert "rmSync" not in create_window
    assert "rmdir" not in create_window.lower()
    assert "shell:" not in create_window
