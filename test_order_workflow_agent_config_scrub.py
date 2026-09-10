"""Agent configuration must not survive in a generated project -- neither into the next
call the pipeline makes in that directory, nor into what the client receives.

`.claude/` is executable: it declares hooks (shell commands) and skills (model-invocable
bundled scripts). CLAUDE.md is not, but it is read as project instructions by an agent
working in the directory, and an unattended order must take instructions only from its own
brief. The two are therefore removed at different moments, which is what these tests pin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.workspace import scrub_agent_config

pytestmark = pytest.mark.unit


def _populate(root: Path) -> None:
    (root / ".claude" / "skills" / "vendored").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (root / ".claude" / "skills" / "vendored" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("project notes", encoding="utf-8")
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")


def test_before_a_run_both_the_config_dir_and_the_instructions_go(tmp_path):
    _populate(tmp_path)

    removed = scrub_agent_config(tmp_path, instructions_too=True)

    assert set(removed) == {".claude", "CLAUDE.md"}
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_at_delivery_only_the_executable_half_goes(tmp_path):
    """The client's own CLAUDE.md is documentation and is theirs to keep. `.claude/` would
    run on their machine the moment they opened the project in agent tooling."""
    _populate(tmp_path)

    removed = scrub_agent_config(tmp_path, instructions_too=False)

    assert removed == (".claude",)
    assert not (tmp_path / ".claude").exists()
    assert (tmp_path / "CLAUDE.md").is_file()


def test_the_rest_of_the_project_is_untouched(tmp_path):
    _populate(tmp_path)

    scrub_agent_config(tmp_path, instructions_too=True)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<!doctype html>"


def test_a_nested_config_dir_is_removed_whole(tmp_path):
    """A skill is a directory of files, not a single one; removing only what is at the top
    of `.claude/` would leave the payload behind."""
    _populate(tmp_path)

    scrub_agent_config(tmp_path, instructions_too=True)

    assert not (tmp_path / ".claude" / "skills" / "vendored" / "SKILL.md").exists()


def test_a_clean_workspace_removes_nothing_and_says_so(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")

    assert scrub_agent_config(tmp_path, instructions_too=True) == ()


def test_a_path_that_cannot_be_removed_is_reported_as_not_removed(tmp_path, monkeypatch):
    """Never fatal: a workspace that still holds one of these has failed to be tidied, which
    the callers report, but an order that has already been built is not lost over it. A
    Windows junction left by npm has raised exactly this here before, on the way out of a
    finished build."""
    _populate(tmp_path)

    def _refuse(*_args, **_kwargs):
        raise OSError("cannot stat")

    monkeypatch.setattr("order_workflow.workspace.shutil.rmtree", _refuse)

    removed = scrub_agent_config(tmp_path, instructions_too=True)

    assert ".claude" not in removed, "a directory that survived must not be reported as removed"
    assert "CLAUDE.md" in removed, "one unremovable path must not stop the other being cleared"
