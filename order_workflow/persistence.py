from __future__ import annotations

from threading import RLock
from typing import Protocol

from .models import ProjectExecution


class ExecutionStateStore(Protocol):
    def load(self) -> tuple[ProjectExecution, ...]: ...

    def save(self, snapshot: ProjectExecution) -> None: ...


class InMemoryExecutionStateStore:
    """Persistence-ready in-memory store for tests and the MVP service layer."""

    def __init__(self, initial: tuple[ProjectExecution, ...] = ()) -> None:
        self._records = {item.id: item for item in initial}
        self._lock = RLock()

    def load(self) -> tuple[ProjectExecution, ...]:
        with self._lock:
            return tuple(item.model_copy() for item in self._records.values())

    def save(self, snapshot: ProjectExecution) -> None:
        with self._lock:
            self._records[snapshot.id] = snapshot.model_copy()
