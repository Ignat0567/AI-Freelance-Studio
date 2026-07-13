from pathlib import Path
import viewport_evidence_validator as validator

def test_constants_are_deterministic_and_disjoint():
    assert validator.COMPLETED == ["1366x768", "1920x1080"]
    assert validator.SUPPLEMENTAL == ["2560x1440"]
    assert validator.EXCLUDED == ["3440x1440"]
    assert not set(validator.COMPLETED) & set(validator.REMAINING)

def test_validator_is_multiline_disk_based_source():
    source = Path(validator.__file__).read_text(encoding="utf-8")
    assert "python -c" not in source
    assert "load_project_state" in source and "load_evidence_ledger" in source
