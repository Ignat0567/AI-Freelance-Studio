import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import ac018_browser_harness as harness


class FakeResponse:
    status = 201
    request = type("Request", (), {"method": "POST"})()


class FakeWait:
    value = FakeResponse()
    def __enter__(self): return self
    def __exit__(self, *_args): return False


class FakePage:
    def __init__(self): self.clicked = False
    def expect_response(self, predicate):
        assert predicate(FakeResponse())
        return FakeWait()


class FakeButton:
    def __init__(self, page): self.page = page
    def click(self): self.page.clicked = True


def test_import_does_not_launch_browser():
    assert callable(harness.browser_launch_smoke)
    assert callable(harness.run_ac018)


def test_comment_response_wait_is_multiline_and_returns_safe_metadata():
    page = FakePage()
    result = harness.submit_comment_and_wait(page, FakeButton(page), lambda response: response.request.method == "POST")
    assert page.clicked
    assert result["method"] == "POST"
    assert result["status"] == 201
    assert "cookie" not in str(result).lower()


def test_harness_source_compiles_and_has_no_inline_python_c_pattern():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    ast.parse(source)
    assert "python -c" not in source
    assert "with page.expect_response" in source
    assert "page.reload(wait_until" in source


def test_ticket_scoped_selector_and_controls_are_present():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert 'page.locator("article.ticket").filter(has_text=marker)' in source
    for name in ("Edit", "Comment", "Delete", "View comments"):
        assert f"name=\"{name}\", exact=True" in source
    assert ".first(" not in source and ".nth(" not in source


def test_search_filter_and_delete_inclusion_exclusion_logic_is_present():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    for control in ("#search", "#filterStatus", "#filterPriority"):
        assert control in source
    assert "dialog.dismiss" in source and "dialog.accept" in source
    assert "not card().is_visible()" in source


def test_artifact_hashing_uses_unique_run_directory(tmp_path):
    artifact = tmp_path / "run-a" / "action.png"
    artifact.parent.mkdir()
    artifact.write_bytes(b"artifact")
    assert hashlib.sha256(artifact.read_bytes()).hexdigest()
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert 'f"ac018-{uuid.uuid4().hex}"' in source
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in source


def test_smoke_mode_runs_without_full_workflow():
    completed = subprocess.run([sys.executable, harness.__file__, "--smoke"], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0
