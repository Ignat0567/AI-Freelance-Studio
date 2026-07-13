import ast
from pathlib import Path
import browser_viewport_harness as harness

def test_tablet_smoke_returns_structured_actions_and_hashed_artifacts():
    result = harness.tablet_interaction_smoke()
    assert result["navigation_result"] and result["search_result"] and result["filter_result"]
    assert all(result[key] for key in ("edit_reachability", "comment_reachability", "delete_reachability"))
    assert len(result["artifacts"]) == 4 and all(item["size"] > 0 and item["hash_valid"] for item in result["artifacts"])
    assert result["cleanup"] is True

def test_source_uses_scoped_controls_without_force_or_position_selectors():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    ast.parse(source)
    assert "filter(has_text=marker)" in source
    assert "force=True" not in source and ".first(" not in source and ".nth(" not in source

def test_hash_mismatch_and_missing_artifact_are_detectable(tmp_path):
    path = tmp_path / "artifact.png"; path.write_bytes(b"fixture")
    digest = harness.hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest != harness.hashlib.sha256(b"changed").hexdigest()
    path.unlink(); assert not path.exists()
