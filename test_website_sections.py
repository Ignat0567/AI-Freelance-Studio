from __future__ import annotations

import json

import pytest

from website_sections import SECTION_LIBRARY, get_section, materialize_site
from website_sections.materialize import _escape_jsx_text

pytestmark = pytest.mark.unit


def test_library_has_unique_slugs_and_nonempty_files():
    assert len(SECTION_LIBRARY) >= 3
    slugs = [section.slug for section in SECTION_LIBRARY]
    assert len(slugs) == len(set(slugs))
    for section in SECTION_LIBRARY:
        assert section.files, f"{section.slug} has no files"
        for relative_path, content in section.files.items():
            assert content.strip(), f"{section.slug}:{relative_path} is empty"
        fields = section.content_fields()
        assert len(fields) == len(set(fields)), f"{section.slug} has duplicate content field names"
        assert fields, f"{section.slug} declares no content fields"


def test_every_content_field_has_a_placeholder_in_the_section_files():
    for section in SECTION_LIBRARY:
        combined = "\n".join(section.files.values())
        for field_name, _description in section.content_schema:
            assert f"%%CONTENT:{field_name}%%" in combined, f"{section.slug} is missing a placeholder for {field_name}"


def test_get_section_returns_none_for_unknown_slug():
    assert get_section("does_not_exist") is None
    assert get_section(SECTION_LIBRARY[0].slug) is SECTION_LIBRARY[0]


def test_escape_jsx_text_neutralizes_structural_characters():
    escaped = _escape_jsx_text("<script>{alert('x')}</script> & co")
    assert "<" not in escaped
    assert ">" not in escaped
    assert "{" not in escaped
    assert "}" not in escaped
    assert "&amp;" in escaped


def _fill_all_fields(section, marker="Copy"):
    return {field_name: f"{marker} for {field_name}" for field_name, _description in section.content_schema}


def test_materialize_site_writes_scaffold_and_selected_sections(tmp_path):
    selected = [(section.slug, _fill_all_fields(section)) for section in SECTION_LIBRARY]

    written = materialize_site(selected, tmp_path, project_name="demo-site", project_title="Demo Site")

    assert "frontend/package.json" in written
    assert "frontend/index.html" in written
    assert "frontend/src/App.jsx" in written
    for section in SECTION_LIBRARY:
        for relative_path in section.files:
            assert f"frontend/src/{relative_path}" in written
            assert (tmp_path / "frontend/src" / relative_path).is_file()

    package_data = json.loads((tmp_path / "frontend/package.json").read_text(encoding="utf-8"))
    assert package_data["name"] == "demo-site"
    for section in SECTION_LIBRARY:
        for dependency in section.npm_dependencies:
            assert dependency in package_data["dependencies"]

    app_jsx = (tmp_path / "frontend/src/App.jsx").read_text(encoding="utf-8")
    for section in SECTION_LIBRARY:
        assert f"import {section.component_name} from './{section.entry_relative_path}.jsx';" in app_jsx
        assert f"<{section.component_name} />" in app_jsx

    hero_file = next(iter(get_section("hero_webgl").files))
    hero_text = (tmp_path / "frontend/src" / hero_file).read_text(encoding="utf-8")
    assert "%%CONTENT:" not in hero_text
    assert "Copy for headline" in hero_text


def test_materialize_site_rejects_unknown_slug(tmp_path):
    with pytest.raises(ValueError, match="unknown_section_slug"):
        materialize_site([("not_a_real_section", {})], tmp_path)


def test_materialize_site_requires_every_declared_field(tmp_path):
    section = SECTION_LIBRARY[0]
    content = _fill_all_fields(section)
    content.pop(next(iter(content)))

    with pytest.raises(ValueError, match="missing_content_fields"):
        materialize_site([(section.slug, content)], tmp_path)


def test_materialize_site_drops_undeclared_content_keys(tmp_path):
    section = SECTION_LIBRARY[0]
    content = _fill_all_fields(section)
    content["not_a_declared_field"] = "should be dropped"

    materialize_site([(section.slug, content)], tmp_path, project_name="demo", project_title="Demo")

    for relative_path in section.files:
        text = (tmp_path / "frontend/src" / relative_path).read_text(encoding="utf-8")
        assert "should be dropped" not in text


def test_materialize_site_escapes_content_that_could_break_jsx(tmp_path):
    section = SECTION_LIBRARY[0]
    content = _fill_all_fields(section)
    first_field = next(iter(content))
    content[first_field] = "<img src=x onerror=alert(1)>"

    materialize_site([(section.slug, content)], tmp_path, project_name="demo", project_title="Demo")

    combined = "\n".join((tmp_path / "frontend/src" / relative_path).read_text(encoding="utf-8") for relative_path in section.files)
    assert "<img" not in combined
