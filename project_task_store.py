"""Shared in-memory task store for active projects.

A leaf module with no dependency on main.py, so both main.py and
api/tasks.py can import the same PROJECT_TASKS dict without a circular
import -- the same sharing pattern system_settings.py already uses for
SYSTEM_SETTINGS.
"""

from __future__ import annotations

import uuid
from typing import Dict, List

PROJECT_TASKS: Dict[str, List[dict]] = {}


def get_next_task_id(project_id: str) -> str:
    return f"task_{uuid.uuid4().hex[:8]}"
