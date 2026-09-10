"""A delivered project must not execute code when the client opens it.

`.claude/` declares hooks (shell commands) and skills (model-invocable bundled scripts) that
run when a directory is opened in agent tooling, so it never ships. A CLAUDE.md beside it is
text and does ship: it is not executable, and if the build wrote one it is documentation the
client paid for.

This is about what Studio ships, not about what Studio runs. Studio's own isolation from a
workspace is the `--setting-sources` flag in claude_code_client, which is tested there and
does not depend on any of this.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.workspace import remove_executable_agent_config

pytestmark = pytest.mark.unit


def _populate(root: Path) -> None:
    (root / ".claude" / "skills" / "vendored").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (root / ".claude" / "skills" / "vendored" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("project notes", encoding="utf-8")
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")


def test_the_executable_config_goes_and_the_documentation_stays(tmp_path):
    _populate(tmp_path)

    assert remove_executable_agent_config(tmp_path) is True
    assert not (tmp_path / ".claude").exists()
    assert (tmp_path / "CLAUDE.md").is_file()


def test_the_rest_of_the_project_is_untouched(tmp_path):
    _populate(tmp_path)

    remove_executable_agent_config(tmp_path)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<!doctype html>"


def test_a_nested_config_dir_is_removed_whole(tmp_path):
    """A skill is a directory of files, not a single one; removing only what is at the top of
    `.claude/` would leave the payload behind."""
    _populate(tmp_path)

    remove_executable_agent_config(tmp_path)

    assert not (tmp_path / ".claude" / "skills" / "vendored" / "SKILL.md").exists()


def test_a_clean_project_removes_nothing_and_says_so(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")

    assert remove_executable_agent_config(tmp_path) is False


def test_a_path_that_cannot_be_removed_is_reported_as_not_removed(tmp_path, monkeypatch):
    """Never fatal: a project that still holds one has failed to be tidied, which the caller
    logs, but an order that has already been built is not lost over it. A Windows junction
    left by npm has raised exactly this here before, on the way out of a finished build."""
    _populate(tmp_path)

    def _refuse(*_args, **_kwargs):
        raise OSError("cannot stat")

    monkeypatch.setattr("order_workflow.workspace.shutil.rmtree", _refuse)

    assert remove_executable_agent_config(tmp_path) is False
    assert (tmp_path / ".claude").exists(), "a directory that survived must not be reported as removed"
