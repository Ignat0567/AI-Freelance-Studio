from pathlib import Path


def test_frontend_does_not_name_config_file_as_secret_storage():
    source_root = Path("frontend/src")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx")
        for path in source_root.rglob(pattern)
    )

    assert "studio_config.json" not in source
    assert "API keys are not saved in Studio settings" in source


def test_key_manager_uses_honest_secret_messages_without_browser_storage():
    source = Path("frontend/src/components/KeyManagerModal.jsx").read_text(encoding="utf-8")

    assert "Saved permanently" not in source
    assert "API credentials saved" not in source
    assert "Secrets are not saved in Studio settings." in source
    assert "secret_store_warning" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "setCustomKeys({})" in source
    assert "setNewKey('')" in source
