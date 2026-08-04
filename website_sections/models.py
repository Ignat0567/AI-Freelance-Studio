from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """A real, hand-authored React section an AI can select and fill with content.

    `files` maps a path relative to the project's `src/` directory to its literal
    file content. Content slots inside those files use `%%CONTENT:<field>%%`
    placeholder tokens, one per name declared in `content_schema`.
    """

    slug: str
    display_name: str
    description: str
    when_to_use: str
    component_name: str
    entry_relative_path: str
    content_schema: tuple[tuple[str, str], ...]
    npm_dependencies: tuple[str, ...]
    files: dict[str, str]

    def content_fields(self) -> tuple[str, ...]:
        return tuple(field_name for field_name, _description in self.content_schema)
