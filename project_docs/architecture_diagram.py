from __future__ import annotations

import re

_NODE_ID_UNSAFE = re.compile(r"[^0-9A-Za-z]+")


def _node_id(area: str) -> str:
    return "Area" + _NODE_ID_UNSAFE.sub("_", area).strip("_").title().replace("_", "")


def build_architecture_mermaid(module_map: dict[str, tuple[str, ...]], project_name: str) -> str:
    """Build a real, fully deterministic Mermaid architecture diagram (no AI).

    Reflects exactly what `module_map` says was actually generated, so it can
    never drift out of sync with reality the way an AI-imagined diagram could.
    """
    root_id = "Project"
    lines = ["```mermaid", "flowchart TD", f'    {root_id}["{project_name}"]']
    for area, files in module_map.items():
        node_id = _node_id(area)
        lines.append(f'    {root_id} --> {node_id}["{area} ({len(files)} file{"s" if len(files) != 1 else ""})"]')
    lines.append("```")

    appendix = ["", "## File listing by area", ""]
    for area, files in module_map.items():
        appendix.append(f"### {area}")
        appendix.extend(f"- `{path}`" for path in files)
        appendix.append("")

    return "\n".join(lines + appendix).rstrip() + "\n"
