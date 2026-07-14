import os
from pathlib import Path

import pytest

import main
from opencode_bridge import _resolve_cli_task_file
from qa_engine import _snapshot_project_files, _walk_project_files
from repair_scope import classify_change, is_inside, resolve_inside, walk_repairable_files


def test_node_modules_files_are_excluded_from_postprocess(tmp_path: Path):
    dep = tmp_path / "node_modules" / "react-dom" / "server.js"
    dep.parent.mkdir(parents=True)
    dep.write_text("process.env.REACT_APP_API_URL", encoding="utf-8")
    app = tmp_path / "src" / "App.jsx"
    app.parent.mkdir()
    app.write_text("process.env.REACT_APP_API_URL", encoding="utf-8")

    main._post_process_code(str(tmp_path), project_type="react")

    assert dep.read_text(encoding="utf-8") == "process.env.REACT_APP_API_URL"
    assert "'/api'" in app.read_text(encoding="utf-8")


def test_node_modules_files_are_excluded_from_opencode_repair_scope(tmp_path: Path):
    dep = tmp_path / "node_modules" / "pkg" / "index.js"
    dep.parent.mkdir(parents=True)
    dep.write_text("x", encoding="utf-8")
    source = tmp_path / "src" / "index.js"
    source.parent.mkdir()
    source.write_text("x", encoding="utf-8")

    walked = [rel for rel, _path in walk_repairable_files(tmp_path)]
    qa_files = [os.path.relpath(path, tmp_path).replace(os.sep, "/") for _root, _name, path in _walk_project_files(str(tmp_path))]

    assert "src/index.js" in walked
    assert "src/index.js" in qa_files
    assert all(not rel.startswith("node_modules/") for rel in walked)
    assert all(not rel.startswith("node_modules/") for rel in qa_files)


def test_package_lock_json_is_preserved_by_generic_cleanup(tmp_path: Path):
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":3}', encoding="utf-8")
    junk = tmp_path / "vite.config.js"
    junk.write_text("export default {}", encoding="utf-8")

    main._post_process_code(str(tmp_path), project_type="simple")

    assert lock.exists()
    assert not junk.exists()


def test_dependency_source_files_are_not_rewritten(tmp_path: Path):
    dep = tmp_path / "node_modules" / "picomatch" / "index.js"
    dep.parent.mkdir(parents=True)
    original = "process.env.BACKEND_URL"
    dep.write_text(original, encoding="utf-8")

    before = dep.read_text(encoding="utf-8")
    main._post_process_code(str(tmp_path), project_type="react")
    after = dep.read_text(encoding="utf-8")

    assert after == before


def test_generated_project_repair_cannot_write_outside_project_root(tmp_path: Path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    with pytest.raises(ValueError):
        resolve_inside(tmp_path, outside)


def test_dotdot_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_inside(tmp_path, "../escape.txt")
    _cwd, _path, error = _resolve_cli_task_file(str(tmp_path), "../escape.md")
    assert error == "task_file_escapes_project_root"


def test_symlink_escape_is_rejected_where_supported(tmp_path: Path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported in this environment")

    assert not is_inside(tmp_path, link / "escaped.txt")


def test_snapshot_file_counts_exclude_installed_dependencies(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x", encoding="utf-8")
    dep = tmp_path / "node_modules" / "pkg" / "index.js"
    dep.parent.mkdir(parents=True)
    dep.write_text("x", encoding="utf-8")

    snapshot = _snapshot_project_files(str(tmp_path))

    assert "src/app.js" in snapshot
    assert all(not path.startswith("node_modules/") for path in snapshot)


def test_existing_studio_root_modifications_are_not_falsely_attributed_to_current_project_run(tmp_path: Path):
    workspace = tmp_path / "studio"
    project = workspace / "generated_projects" / "app"
    project.mkdir(parents=True)
    studio_file = workspace / "main.py"
    studio_file.write_text("dirty before run", encoding="utf-8")
    project_file = project / "app.py"
    project_file.write_text("changed", encoding="utf-8")

    root_change = classify_change(project, workspace, studio_file, proven_current_run_paths=[project_file])
    project_change = classify_change(project, workspace, project_file, proven_current_run_paths=[project_file])

    assert root_change["classification"] == "freelancerstudio_root_files"
    assert root_change["changed_by_current_run_proven"] is False
    assert root_change["should_have_been_in_repair_scope"] is False
    assert project_change["changed_by_current_run_proven"] is True
    assert project_change["should_have_been_in_repair_scope"] is True
