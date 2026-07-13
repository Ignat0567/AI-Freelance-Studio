from pathlib import Path

import delivery_audit


def _scan(tmp_path: Path, text: str):
    test_file = tmp_path / "tests" / "test_credentials.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(text, encoding="utf-8")
    return delivery_audit._check_secrets(str(tmp_path))


def test_production_hardcoded_secret_and_realistic_test_fixture_are_blocking(tmp_path):
    production_ok, production_hits = _scan(tmp_path / "production", 'ADMIN_PASSWORD = "reusable-production-password"\n')
    fixture_ok, fixture_hits = _scan(tmp_path / "fixture", 'TEST_ADMIN_PASSWORD = "reusable-test-password"\n')
    assert not production_ok and production_hits == ["tests/test_credentials.py"]
    assert not fixture_ok and fixture_hits == ["tests/test_credentials.py"]


def test_synthetic_fixture_environment_expression_and_documentation_placeholder_are_nonblocking(tmp_path):
    synthetic_ok, synthetic_hits = _scan(tmp_path / "synthetic", 'TEST_ADMIN_PASSWORD = f"test-fixture-{value}"\n')
    environment_ok, environment_hits = _scan(tmp_path / "environment", 'ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")\n')
    docs_ok, docs_hits = _scan(tmp_path / "docs", 'ADMIN_PASSWORD=<set-a-local-admin-password>\n')
    assert synthetic_ok and not synthetic_hits
    assert environment_ok and not environment_hits
    assert docs_ok and not docs_hits


def test_secret_scan_evidence_passes_only_with_zero_blocking_findings(tmp_path):
    criterion = {"id": "AC-023", "verification_method": "secret_scan"}
    passed = delivery_audit.verify_acceptance_criterion(criterion, {}, str(tmp_path), {}, [{"name": "secret_scan", "status": "passed", "evidence": {"files_with_secret_like_values": []}}])
    failed = delivery_audit.verify_acceptance_criterion(criterion, {}, str(tmp_path), {}, [{"name": "secret_scan", "status": "failed", "evidence": {"files_with_secret_like_values": ["tests/test_credentials.py"]}}])
    assert passed["status"] == "passed"
    assert failed["status"] == "failed"
