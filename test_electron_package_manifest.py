import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def test_electron_package_manifest_excludes_sensitive_paths():
    package_path = Path("frontend/package.json")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    build = package["build"]
    manifest_paths = [*build.get("files", [])]
    manifest_paths.extend(resource["from"] for resource in build.get("extraResources", []))
    sensitive_paths = ("studio_config.json", ".env", "backup", "secret")

    assert not any(
        sensitive_path in manifest_path.lower()
        for manifest_path in manifest_paths
        for sensitive_path in sensitive_paths
    )


def test_electron_package_manifest_bundles_only_sidecar_and_built_frontend_resources():
    package_path = Path("frontend/package.json")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    build = package["build"]

    assert not any(".venv" in path.lower() for path in build.get("files", []))
    assert build.get("extraResources", []) == [
        {
            "from": "../backend_dist",
            "to": "backend",
            "filter": ["**/*", "!**/*.{pfx,p12,pem,key,cer,crt}"],
        },
        {"from": "dist", "to": "frontend-dist", "filter": ["**/*", "!**/*.map"]},
        # Sandbox Test Lab video evidence (Phase 5d) encodes with a vendored, hash-pinned
        # LGPL ffmpeg. The filter stays narrow so the rest of the ffmpeg distribution
        # (headers, docs, ffplay/ffprobe) never reaches the installer.
        {"from": "../third_party/ffmpeg", "to": "ffmpeg", "filter": ["ffmpeg.exe", "*.dll"]},
    ]
