from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_root_runtime_artifacts_are_ignored_without_broad_patterns():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/studio_port.txt" in ignore
    assert "/sandbox-test-lab-jobs.json" in ignore
    assert "/backups/" in ignore
    assert "*.json" not in ignore
    assert "\n*.txt" not in ignore
    assert "data/" not in ignore
    assert "tests/" not in ignore


def test_backend_port_state_uses_runtime_dir_not_repository_root():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    port_block = source.split('port_file = os.path.join(RUNTIME_DIR, "studio_port.txt")', 1)[1].split("uvicorn.run", 1)[0]

    assert 'port_file = os.path.join(RUNTIME_DIR, "studio_port.txt")' in source
    assert "BASE_DIR" not in port_block
    assert "os.makedirs(RUNTIME_DIR, exist_ok=True)" in source


def test_electron_and_backend_share_runtime_port_contract():
    source = (ROOT / "frontend" / "main.js").read_text(encoding="utf-8")

    assert "const runtimeDir = runtimeDirectory();" in source
    assert "path.join(runtimeDir, 'studio_port.txt')" in source
    assert "FREELANCERSTUDIO_RUNTIME_DIR: runtimeDir" in source
    assert "readPortFile" not in source


def test_sandbox_job_store_uses_injected_runtime_dir_and_single_owner():
    bridge = (ROOT / "sandbox_test_lab" / "production_bridge.py").read_text(encoding="utf-8")
    store = (ROOT / "sandbox_test_lab" / "job_service.py").read_text(encoding="utf-8")

    assert 'JsonSandboxJobStateStore(Path(runtime_dir) / "sandbox-test-lab-jobs.json")' in bridge
    assert "class JsonSandboxJobStateStore" in store
    assert "self._path.parent.mkdir(parents=True, exist_ok=True)" in store
    assert "temporary.replace(self._path)" in store
