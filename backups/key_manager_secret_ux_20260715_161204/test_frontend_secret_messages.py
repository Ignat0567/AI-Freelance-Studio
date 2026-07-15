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
    assert "does not store OAuth tokens" in source
