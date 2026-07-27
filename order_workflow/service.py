from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from .api_models import CreateOrderRequest, RevisionOperationRequest
from .brief_service import (
    BriefApprovalBinding,
    BriefRevisionKind,
    BriefRevisionOperation,
    BriefRevisionRequest,
    BriefServiceError,
    ProjectBriefService,
)
from .clarification import AlexClarificationService, ClarificationError, ClarificationSession
from .execution import ExecutionServiceError, ProjectExecutionService
from .handoffs import AgentHandoffService
from .models import (
    AgentHandoff,
    ClarificationAnswer,
    ClarificationQuestion,
    ElenaDesignChoice,
    ExecutionArtifact,
    ExecutionBlocker,
    ExecutionEvent,
    ExecutionMode,
    ProjectBrief,
    ProjectExecution,
    UserOrder,
    UserOrderStatus,
    new_public_id,
    utc_now,
)


class OrderWorkflowError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message or code


class OrderWorkflowService:
    def __init__(
        self,
        *,
        id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
        execution_service: ProjectExecutionService | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._clarification = AlexClarificationService(clock=clock)
        self._briefs = ProjectBriefService(id_factory=id_factory, clock=clock)
        self._handoffs = AgentHandoffService(id_factory=id_factory, clock=clock)
        self._executions = execution_service or ProjectExecutionService(id_factory=id_factory, clock=clock)
        self._orders: dict[str, UserOrder] = {}
        self._sessions: dict[str, ClarificationSession] = {}
        self._brief_versions: dict[str, list[ProjectBrief]] = {}
        self._approval_bindings: dict[str, BriefApprovalBinding] = {}
        self._handoff_by_order: dict[str, AgentHandoff] = {}
        self._execution_by_order: dict[str, str] = {}
        self._lock = RLock()

    def create_order(self, request: CreateOrderRequest) -> dict[str, Any]:
        if request.product_type != "web_app":
            raise OrderWorkflowError("unsupported_product_type", "Only web_app orders are supported.")
        now = utc_now(self._clock)
        order = UserOrder(
            id=new_public_id("order", self._id_factory),
            title=request.title,
            description=request.description,
            product_type=request.product_type,
            preferred_language=request.preferred_language,
            constraints=request.constraints,
            attachments=request.attachments,
            created_at=now,
            updated_at=now,
        )
        try:
            result = self._clarification.begin(order)
        except ClarificationError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._orders[result.order.id] = result.order
            self._sessions[result.order.id] = result.session
        return self.snapshot(result.order.id)

    def snapshot(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self._order(order_id)
            session = self._sessions.get(order_id)
            brief = self._latest_brief(order_id)
            handoff = self._handoff_by_order.get(order_id)
            execution = self._current_execution(order_id)
        blockers = list(execution.blockers if execution else ())
        next_action = self._next_action(order, session, brief, handoff, execution, blockers)
        return {
            "order": order.to_dict(),
            "questions": [item.to_dict() for item in order.questions],
            "answers": [item.to_dict() for item in order.answers],
            "assumptions": list(session.assumptions if session else ()),
            "recommended_defaults_available": bool(order.questions and session and session.remaining_dimensions),
            "brief": brief.to_dict() if brief else None,
            "approval": self._approval_payload(brief),
            "handoff_ready": handoff is not None,
            "handoff": handoff.to_dict() if handoff else None,
            "execution": execution.to_dict() if execution else None,
            "next_action": next_action,
            "blockers": [item.to_dict() for item in blockers],
        }

    def questions(self, order_id: str) -> dict[str, Any]:
        return self.snapshot(order_id)

    def answer(self, order_id: str, answers: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
        with self._lock:
            order = self._order(order_id)
            session = self._session(order_id)
        converted = tuple(ClarificationAnswer(question_id=question_id, value=self._normalize_answer(value)) for question_id, value in answers)
        try:
            result = self._clarification.apply_answers(order, session, converted)
        except ClarificationError as exc:
            raise OrderWorkflowError(self._clarification_error_code(exc.code)) from None
        with self._lock:
            self._orders[order_id] = result.order
            self._sessions[order_id] = result.session
        return self.snapshot(order_id)

    def defaults(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self._order(order_id)
            session = self._session(order_id)
        try:
            result = self._clarification.use_recommended_defaults(order, session)
        except ClarificationError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._orders[order_id] = result.order
            self._sessions[order_id] = result.session
        return self.snapshot(order_id)

    def get_brief(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            self._order(order_id)
            if self._latest_brief(order_id) is None:
                raise OrderWorkflowError("brief_not_ready", "Generate the brief after clarification is complete.")
        return self.snapshot(order_id)

    def generate_brief(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            existing = self._latest_brief(order_id)
            order = self._order(order_id)
            session = self._session(order_id)
        if existing is None:
            if order.status is not UserOrderStatus.BRIEF_READY:
                raise OrderWorkflowError("brief_not_ready", "Clarification must be completed before generating a brief.")
            try:
                brief = self._briefs.generate(order, session)
            except BriefServiceError as exc:
                raise OrderWorkflowError(exc.code) from None
            with self._lock:
                self._brief_versions.setdefault(order_id, []).append(brief)
                self._orders[order_id] = order.model_copy(update={"brief_id": brief.id, "status": UserOrderStatus.AWAITING_APPROVAL})
        return self.snapshot(order_id)

    def revise_brief(self, order_id: str, operations: tuple[RevisionOperationRequest, ...]) -> dict[str, Any]:
        with self._lock:
            brief = self._require_brief(order_id)
        request = BriefRevisionRequest(
            operations=tuple(
                BriefRevisionOperation(
                    kind=BriefRevisionKind(item.kind),
                    value=item.value,
                    previous_value=item.previous_value,
                    elena_choice=ElenaDesignChoice(item.elena_choice) if item.elena_choice else None,
                )
                for item in operations
            )
        )
        try:
            revised, _ = self._briefs.revise(brief, request)
        except (BriefServiceError, ValueError) as exc:
            code = getattr(exc, "code", "brief_revision_invalid")
            raise OrderWorkflowError(code) from None
        with self._lock:
            self._brief_versions.setdefault(order_id, []).append(revised)
            self._approval_bindings.pop(order_id, None)
            self._handoff_by_order.pop(order_id, None)
            self._orders[order_id] = self._orders[order_id].model_copy(update={"brief_id": revised.id, "status": UserOrderStatus.AWAITING_APPROVAL})
        return self.snapshot(order_id)

    def approve_brief(self, order_id: str, *, revision: int, fingerprint: str | None) -> dict[str, Any]:
        with self._lock:
            brief = self._require_brief(order_id)
        if revision != brief.revision:
            raise OrderWorkflowError("brief_approval_stale", "Approval applies to an older brief revision.")
        try:
            binding = self._briefs.prepare_approval(brief)
            if fingerprint is not None and fingerprint != binding.fingerprint:
                raise OrderWorkflowError("brief_approval_stale", "Approval fingerprint is stale.")
            approved = self._briefs.approve(brief, binding)
            handoff = self._handoffs.create_implementation_handoff(approved)
        except OrderWorkflowError:
            raise
        except BriefServiceError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._brief_versions[order_id][-1] = approved
            self._approval_bindings[order_id] = binding
            self._handoff_by_order[order_id] = handoff
            self._orders[order_id] = self._orders[order_id].model_copy(update={"status": UserOrderStatus.APPROVED})
        return self.snapshot(order_id)

    def handoff(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            if order_id not in self._handoff_by_order:
                raise OrderWorkflowError("handoff_blocked", "Approve the current brief before requesting a handoff.")
        return self.snapshot(order_id)

    def start_execution(self, order_id: str, mode: ExecutionMode) -> dict[str, Any]:
        with self._lock:
            order = self._order(order_id)
            brief = self._require_brief(order_id)
            handoff = self._handoff_by_order.get(order_id)
        if handoff is None:
            raise OrderWorkflowError("brief_not_approved", "Approve the current brief before execution.")
        try:
            execution = self._executions.start(brief, handoff, mode=mode)
        except ExecutionServiceError as exc:
            raise OrderWorkflowError(self._execution_error_code(exc.code)) from None
        with self._lock:
            self._execution_by_order[order_id] = execution.id
            next_status = UserOrderStatus.AWAITING_USER if execution.blockers else UserOrderStatus.QUEUED
            self._orders[order_id] = order.model_copy(update={"execution_id": execution.id, "status": next_status})
        return self.snapshot(order_id)

    def execution(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            self._order(order_id)
            if order_id not in self._execution_by_order:
                raise OrderWorkflowError("execution_not_found")
        return self.snapshot(order_id)

    def cancel_execution(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            self._order(order_id)
            execution_id = self._execution_by_order.get(order_id)
        if execution_id is None:
            raise OrderWorkflowError("execution_not_found")
        try:
            execution = self._executions.cancel(execution_id)
        except ExecutionServiceError as exc:
            raise OrderWorkflowError(self._execution_error_code(exc.code)) from None
        with self._lock:
            if execution.status.value == "cancelled":
                self._orders[order_id] = self._orders[order_id].model_copy(update={"status": UserOrderStatus.CANCELLED})
        return self.snapshot(order_id)

    def events(self, order_id: str) -> tuple[ExecutionEvent, ...]:
        return tuple((self._execution_snapshot(order_id).events))

    def artifacts(self, order_id: str) -> tuple[ExecutionArtifact, ...]:
        return tuple((self._execution_snapshot(order_id).artifacts))

    def result(self, order_id: str) -> dict[str, Any] | None:
        return self._execution_snapshot(order_id).result.to_dict() if self._execution_snapshot(order_id).result else None

    def _execution_snapshot(self, order_id: str) -> ProjectExecution:
        with self._lock:
            self._order(order_id)
            execution_id = self._execution_by_order.get(order_id)
        if execution_id is None:
            raise OrderWorkflowError("execution_not_found")
        try:
            return self._executions.snapshot(execution_id)
        except ExecutionServiceError as exc:
            raise OrderWorkflowError(self._execution_error_code(exc.code)) from None

    def _current_execution(self, order_id: str) -> ProjectExecution | None:
        execution_id = self._execution_by_order.get(order_id)
        if not execution_id:
            return None
        try:
            return self._executions.snapshot(execution_id)
        except ExecutionServiceError:
            return None

    def _order(self, order_id: str) -> UserOrder:
        order = self._orders.get(order_id)
        if order is None:
            raise OrderWorkflowError("order_not_found", "Order was not found.")
        return order

    def _session(self, order_id: str) -> ClarificationSession:
        session = self._sessions.get(order_id)
        if session is None:
            raise OrderWorkflowError("clarification_not_ready")
        return session

    def _latest_brief(self, order_id: str) -> ProjectBrief | None:
        versions = self._brief_versions.get(order_id, [])
        return versions[-1] if versions else None

    def _require_brief(self, order_id: str) -> ProjectBrief:
        brief = self._latest_brief(order_id)
        if brief is None:
            raise OrderWorkflowError("brief_not_ready")
        return brief

    @staticmethod
    def _normalize_answer(value: Any) -> str | tuple[str, ...] | bool:
        if type(value) is bool:
            return value
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, tuple):
            return tuple(str(item) for item in value)
        return str(value)

    @staticmethod
    def _approval_payload(brief: ProjectBrief | None) -> dict[str, Any] | None:
        if brief is None:
            return None
        return {
            "brief_id": brief.id,
            "revision": brief.revision,
            "approved": brief.approved_at is not None,
            "approved_at": brief.approved_at.isoformat() if brief.approved_at else None,
            "approval_fingerprint": brief.approval_fingerprint,
        }

    @staticmethod
    def _next_action(order: UserOrder, session: ClarificationSession | None, brief: ProjectBrief | None, handoff: AgentHandoff | None, execution: ProjectExecution | None, blockers: list[ExecutionBlocker]) -> dict[str, str]:
        if blockers:
            return {"code": blockers[0].code, "message": blockers[0].message}
        if order.status is UserOrderStatus.CLARIFICATION_REQUIRED:
            return {"code": "answer_clarification", "message": "Answer Alex's questions to prepare the project brief."}
        if brief is None:
            return {"code": "generate_brief", "message": "Generate the structured project brief."}
        if brief.approved_at is None:
            return {"code": "approve_brief", "message": "Review and approve the current project brief."}
        if handoff is None:
            return {"code": "prepare_handoff", "message": "Prepare the Alex to Codex implementation handoff."}
        if execution is None:
            return {"code": "start_execution", "message": "Start fake project execution."}
        if execution.result is not None:
            return {"code": "view_result", "message": "Review generated artifacts and verification summary."}
        return {"code": "watch_execution", "message": "Track execution progress."}

    @staticmethod
    def _clarification_error_code(code: str) -> str:
        return {
            "unknown_question_id": "question_not_found",
            "invalid_answer": "invalid_answer",
            "duplicate_answer_conflict": "invalid_answer",
        }.get(code, code)

    @staticmethod
    def _execution_error_code(code: str) -> str:
        return {
            "execution_already_completed": "execution_already_completed",
            "execution_not_found": "execution_not_found",
            "brief_not_approved": "brief_not_approved",
        }.get(code, code)
