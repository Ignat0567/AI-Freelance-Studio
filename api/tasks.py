"""Task management routes for /api/projects/{project_id}/tasks.

Extracted from main.py. Project existence and persistence are owned by
main.py's active_projects registry and _save_projects_state(), so this
module exposes a factory that main.py calls once at startup, passing
those in explicitly instead of importing them back (which would create
a circular import between main.py and this router).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException

from api.request_models import TaskCommentPayload, TaskModel, TaskUpdateModel
from project_task_store import PROJECT_TASKS, get_next_task_id


def build_tasks_router(active_projects: Dict[str, Any], save_projects_state: Callable[[], None]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/tasks")
    def get_project_tasks(project_id: str):
        """Get all tasks for a project."""
        tasks = PROJECT_TASKS.get(project_id, [])
        return {"tasks": tasks}

    @router.post("/api/projects/{project_id}/tasks")
    def create_task(project_id: str, task: TaskModel):
        """Create a new task in a project."""
        if project_id not in active_projects:
            raise HTTPException(status_code=404, detail="Project not found")
        task_id = get_next_task_id(project_id)
        new_task = {
            "id": task_id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "assignee": task.assignee,
            "priority": task.priority,
            "due_date": task.due_date,
            "created_at": datetime.now().isoformat(),
            "comments": [],
        }
        if project_id not in PROJECT_TASKS:
            PROJECT_TASKS[project_id] = []
        PROJECT_TASKS[project_id].append(new_task)
        save_projects_state()
        return new_task

    @router.put("/api/projects/{project_id}/tasks/{task_id}")
    def update_task(project_id: str, task_id: str, updates: TaskUpdateModel):
        """Update a task."""
        tasks = PROJECT_TASKS.get(project_id, [])
        for task in tasks:
            if task["id"] == task_id:
                if updates.title is not None:
                    task["title"] = updates.title
                if updates.description is not None:
                    task["description"] = updates.description
                if updates.status is not None:
                    task["status"] = updates.status
                if updates.assignee is not None:
                    task["assignee"] = updates.assignee
                if updates.priority is not None:
                    task["priority"] = updates.priority
                if updates.due_date is not None:
                    task["due_date"] = updates.due_date
                task["updated_at"] = datetime.now().isoformat()
                save_projects_state()
                return task
        raise HTTPException(status_code=404, detail="Task not found")

    @router.delete("/api/projects/{project_id}/tasks/{task_id}")
    def delete_task(project_id: str, task_id: str):
        """Delete a task."""
        tasks = PROJECT_TASKS.get(project_id, [])
        for i, task in enumerate(tasks):
            if task["id"] == task_id:
                tasks.pop(i)
                save_projects_state()
                return {"status": "deleted", "task_id": task_id}
        raise HTTPException(status_code=404, detail="Task not found")

    @router.post("/api/projects/{project_id}/tasks/{task_id}/comments")
    def add_task_comment(project_id: str, task_id: str, payload: TaskCommentPayload):
        """Add a comment to a task."""
        tasks = PROJECT_TASKS.get(project_id, [])
        for task in tasks:
            if task["id"] == task_id:
                comment = {
                    "id": f"comment_{len(task['comments']) + 1}",
                    "text": payload.text,
                    "author": payload.author,
                    "created_at": datetime.now().isoformat(),
                }
                task["comments"].append(comment)
                return comment
        raise HTTPException(status_code=404, detail="Task not found")

    return router
