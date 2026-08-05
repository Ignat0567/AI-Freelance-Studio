from __future__ import annotations

from pathlib import Path

from repair_scope import walk_repairable_files

SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql"}

_AREA_KEYWORDS = {
    "routes/controllers": ("route", "router", "controller", "api"),
    "models": ("model", "models", "entity"),
    "schemas": ("schema", "schemas", "dto"),
    "services/domain": ("service", "services", "domain"),
    "repositories/data": ("repository", "repositories", "dao", "data", "db"),
    "authentication": ("auth", "login", "session"),
    "configuration": ("config", "settings", ".env"),
    "tests": ("test", "tests", "spec"),
    "components": ("component", "components"),
    "state/store": ("store", "state", "slice"),
}


def build_module_map(project_root: Path) -> dict[str, tuple[str, ...]]:
    """Bucket a real generated project's files into named architectural areas.

    Walks the real file tree via `repair_scope.walk_repairable_files` (the same
    safe, noise-excluding walker `architecture_policy.py` already uses), so the
    result always reflects what was actually generated -- never an AI guess.
    """
    areas: dict[str, list[str]] = {area: [] for area in _AREA_KEYWORDS}
    for rel, _path in walk_repairable_files(project_root, SOURCE_EXTS):
        lowered = rel.replace("\\", "/").lower()
        for area, keywords in _AREA_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                areas[area].append(rel)
    return {area: tuple(paths) for area, paths in areas.items() if paths}
