from __future__ import annotations

import re
from pathlib import Path

from repair_scope import walk_repairable_files

SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql"}

# Order matters: the first area whose keyword matches a path segment takes the file, and it
# takes it exactly once. Measured on a delivered reading journal before that rule existed:
# 29 source files, 18 in the map, 11 missing and 2 counted twice -- every page, the app
# entry, the state context and the theme hook absent from the client's architecture diagram,
# while `AddBookForm.jsx` was filed under "repositories/data" because "ad(db)ookform"
# contains the substring "db".
_AREA_KEYWORDS = {
    "tests": ("test", "tests", "spec", "specs", "__tests__"),
    "configuration": ("config", "configs", "settings", "env"),
    "routes/controllers": ("route", "routes", "router", "controller", "controllers", "api"),
    "models": ("model", "models", "entity", "entities"),
    "schemas": ("schema", "schemas", "dto"),
    "services/domain": ("service", "services", "domain"),
    "repositories/data": ("repository", "repositories", "dao", "data", "db", "database"),
    "authentication": ("auth", "login", "logout", "session", "sessions"),
    "screens/pages": ("page", "pages", "screen", "screens", "view", "views"),
    "state/store": ("store", "state", "slice", "slices", "context", "contexts", "hook", "hooks", "provider", "providers"),
    "components": ("component", "components", "ui", "widgets"),
    "styles": ("style", "styles", "css", "theme", "themes", "tokens"),
}
# Matched against the file's stem alone, and only when nothing above claimed the file: an
# `app/` directory is a whole area in some frameworks, while `App.jsx` is one file.
_ENTRY_STEMS = {"app", "main", "index", "entry", "server", "bot"}
_ENTRY_AREA = "entry points"
# Where the rest goes. A file the rules cannot name is still a file the client received, and
# leaving it out of the diagram is how the app's own pages went missing from one.
_OTHER_AREA = "other files"

_AREA_ORDER = (*_AREA_KEYWORDS, _ENTRY_AREA, _OTHER_AREA)


def _tokens(segment: str) -> set[str]:
    """Whole words in one path segment. Whole words on purpose: substring matching over the
    entire path is what put a form component under "repositories/data"."""
    return {token for token in re.split(r"[^a-z0-9]+", segment.lower()) if token}


def _area_for(rel: str) -> str:
    segments = rel.replace("\\", "/").lower().split("/")
    directories, name = segments[:-1], segments[-1]
    # Nearest directory first, then the file's own name: a file's neighbours describe it
    # better than its name does (`components/DataTable.jsx` is a component, not data), and
    # a file with no telling directory is still named something (`vite.config.js`).
    for segment in [*reversed(directories), name]:
        tokens = _tokens(segment)
        for area, keywords in _AREA_KEYWORDS.items():
            if tokens & set(keywords):
                return area
    if name.rsplit(".", 1)[0] in _ENTRY_STEMS:
        return _ENTRY_AREA
    return _OTHER_AREA


def build_module_map(project_root: Path) -> dict[str, tuple[str, ...]]:
    """Bucket a real generated project's files into named architectural areas.

    Walks the real file tree via `repair_scope.walk_repairable_files` (the same
    safe, noise-excluding walker `architecture_policy.py` already uses), so the
    result always reflects what was actually generated -- never an AI guess.

    Every walked file appears exactly once, so the counts in README.md and
    ARCHITECTURE.md add up to the project a client is holding.
    """
    areas: dict[str, list[str]] = {area: [] for area in _AREA_ORDER}
    for rel, _path in walk_repairable_files(project_root, SOURCE_EXTS):
        areas[_area_for(rel)].append(rel)
    return {area: tuple(paths) for area, paths in areas.items() if paths}
