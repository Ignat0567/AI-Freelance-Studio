from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from design_system import DEFAULT_TOKENS, render_tokens_css

from . import scaffold
from .library import get_section

_JSX_TEXT_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ("{", "&#123;"),
    ("}", "&#125;"),
)


def _escape_jsx_text(value: str) -> str:
    escaped = value
    for raw, safe in _JSX_TEXT_ESCAPES:
        escaped = escaped.replace(raw, safe)
    return escaped


def materialize_site(
    selected: Sequence[tuple[str, dict[str, str]]],
    destination: Path,
    *,
    project_name: str = "generated-cinematic-site",
    project_title: str = "AI Freelance Studio",
    tokens_css: str | None = None,
) -> tuple[str, ...]:
    """Write the base scaffold plus each selected section's files into `destination`.

    Every `(slug, content)` pair must reference a real library entry; unknown slugs
    fail closed. Content dict keys not declared by that section's `content_schema`
    are silently dropped; any declared field left unfilled raises, since an unfilled
    `%%CONTENT:...%%` placeholder would ship as broken, unbuildable source.
    `tokens_css` defaults to the built-in design tokens when omitted, so every
    existing call site keeps working unmodified.
    Returns the relative paths (posix-style, rooted at `destination`) that were written.
    """
    sections = []
    for slug, content in selected:
        section = get_section(slug)
        if section is None:
            raise ValueError(f"unknown_section_slug:{slug}")
        declared_fields = set(section.content_fields())
        filtered_content = {key: value for key, value in content.items() if key in declared_fields}
        missing = declared_fields - filtered_content.keys()
        if missing:
            raise ValueError(f"missing_content_fields:{slug}:{','.join(sorted(missing))}")
        sections.append((section, filtered_content))

    extra_dependencies = tuple(dict.fromkeys(dep for section, _content in sections for dep in section.npm_dependencies))
    written: dict[str, str] = dict(scaffold.base_files(project_name, project_title, extra_dependencies))
    written["frontend/src/tokens.css"] = tokens_css if tokens_css is not None else render_tokens_css(DEFAULT_TOKENS)

    for section, content in sections:
        for relative_path, raw_text in section.files.items():
            text = raw_text
            for field_name, value in content.items():
                text = text.replace(f"%%CONTENT:{field_name}%%", _escape_jsx_text(value))
            written[f"frontend/src/{relative_path}"] = text

    imports = tuple(f"import {section.component_name} from './{section.entry_relative_path}.jsx';" for section, _content in sections)
    renders = tuple(f"<{section.component_name} />" for section, _content in sections)
    written["frontend/src/App.jsx"] = scaffold.app_jsx(("import React from 'react';", *imports), renders)

    for relative_path, text in written.items():
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    return tuple(written.keys())
