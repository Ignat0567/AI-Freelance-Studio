from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from .brief_service import BriefApprovalBinding
from .clarification import ClarificationSession
from .design_preview import DesignPreview
from .execution import ExecutionStateStore
from .models import AgentHandoff, ProjectBrief, ProjectExecution, UserOrder


class OrderStateStore(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, state: dict[str, Any]) -> None: ...


class InMemoryOrderStore:
    """Default store: matches the pre-existing behavior of not persisting orders across
    a process restart. Used by every test and any caller that doesn't opt into a real
    file-backed store, so nothing changes for them."""

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def save(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state = dict(state)


class JsonOrderStore:
    """Whole-file JSON persistence for OrderWorkflowService's order/session/brief/preview/
    handoff state. Simple by design -- rewrites the entire file on every save -- which is
    fine at the dozens-to-hundreds-of-orders scale this MVP targets; a real database is a
    later problem if order volume ever justifies one."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.is_file():
                return {}
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

    def save(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
            tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)


class JsonExecutionStore:
    """File-backed implementation of order_workflow.execution.ExecutionStateStore --
    ProjectExecutionService defaults to an in-memory store, so without this, execution
    records (progress, artifacts, final result) vanish on restart even once order
    metadata itself survives via JsonOrderStore."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def load(self) -> tuple[ProjectExecution, ...]:
        with self._lock:
            if not self._path.is_file():
                return ()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return ()
            records = []
            for item in raw.values() if isinstance(raw, dict) else ():
                try:
                    records.append(ProjectExecution.model_validate(item))
                except Exception:
                    continue
            return tuple(records)

    def save(self, snapshot: ProjectExecution) -> None:
        with self._lock:
            data = {}
            if self._path.is_file():
                try:
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
            data[snapshot.id] = json.loads(snapshot.to_json())
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)


def dump_order_workflow_state(
    *,
    orders: dict[str, UserOrder],
    sessions: dict[str, ClarificationSession],
    brief_versions: dict[str, list[ProjectBrief]],
    design_previews: dict[str, list[DesignPreview]],
    approval_bindings: dict[str, BriefApprovalBinding],
    handoff_by_order: dict[str, AgentHandoff],
    execution_by_order: dict[str, str],
) -> dict[str, Any]:
    return {
        "orders": {order_id: json.loads(order.to_json()) for order_id, order in orders.items()},
        "sessions": {order_id: json.loads(session.to_json()) for order_id, session in sessions.items()},
        "brief_versions": {order_id: [json.loads(brief.to_json()) for brief in briefs] for order_id, briefs in brief_versions.items()},
        "design_previews": {order_id: [json.loads(preview.to_json()) for preview in previews] for order_id, previews in design_previews.items()},
        "approval_bindings": {order_id: json.loads(binding.to_json()) for order_id, binding in approval_bindings.items()},
        "handoff_by_order": {order_id: json.loads(handoff.to_json()) for order_id, handoff in handoff_by_order.items()},
        "execution_by_order": dict(execution_by_order),
    }


def load_order_workflow_state(state: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct OrderWorkflowService's dicts from a JsonOrderStore payload. Tolerates a
    missing/empty file (first run) and skips any record that fails validation (e.g. an
    older schema) rather than refusing to start Studio."""
    def _parse(model, raw):
        try:
            return model.model_validate(raw)
        except Exception:
            return None

    orders: dict[str, UserOrder] = {}
    for order_id, raw in (state.get("orders") or {}).items():
        parsed = _parse(UserOrder, raw)
        if parsed is not None:
            orders[order_id] = parsed

    sessions: dict[str, ClarificationSession] = {}
    for order_id, raw in (state.get("sessions") or {}).items():
        parsed = _parse(ClarificationSession, raw)
        if parsed is not None:
            sessions[order_id] = parsed

    brief_versions: dict[str, list[ProjectBrief]] = {}
    for order_id, raw_list in (state.get("brief_versions") or {}).items():
        parsed_list = [item for item in (_parse(ProjectBrief, raw) for raw in raw_list) if item is not None]
        if parsed_list:
            brief_versions[order_id] = parsed_list

    design_previews: dict[str, list[DesignPreview]] = {}
    for order_id, raw_list in (state.get("design_previews") or {}).items():
        parsed_list = [item for item in (_parse(DesignPreview, raw) for raw in raw_list) if item is not None]
        if parsed_list:
            design_previews[order_id] = parsed_list

    approval_bindings: dict[str, BriefApprovalBinding] = {}
    for order_id, raw in (state.get("approval_bindings") or {}).items():
        parsed = _parse(BriefApprovalBinding, raw)
        if parsed is not None:
            approval_bindings[order_id] = parsed

    handoff_by_order: dict[str, AgentHandoff] = {}
    for order_id, raw in (state.get("handoff_by_order") or {}).items():
        parsed = _parse(AgentHandoff, raw)
        if parsed is not None:
            handoff_by_order[order_id] = parsed

    execution_by_order = dict(state.get("execution_by_order") or {})

    return {
        "orders": orders,
        "sessions": sessions,
        "brief_versions": brief_versions,
        "design_previews": design_previews,
        "approval_bindings": approval_bindings,
        "handoff_by_order": handoff_by_order,
        "execution_by_order": execution_by_order,
    }
