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
from .design_preview import DesignPreview, DesignPreviewError, DesignPreviewService
from .execution import ExecutionServiceError, ProjectExecutionService
from .execution_config import ExecutionConfigurationProvider
from .execution_readiness import ExecutionReadinessView, readiness_blocker_view, readiness_check
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
from .readiness import BRIEF_NOT_APPROVED, DESIGN_PREVIEW_NOT_APPROVED


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
        configuration_provider: ExecutionConfigurationProvider | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._clarification = AlexClarificationService(clock=clock)
        self._briefs = ProjectBriefService(id_factory=id_factory, clock=clock)
        self._designs = DesignPreviewService(id_factory=id_factory, clock=clock)
        self._handoffs = AgentHandoffService(id_factory=id_factory, clock=clock)
        self._executions = execution_service or ProjectExecutionService(id_factory=id_factory, clock=clock)
        self._configuration = configuration_provider or ExecutionConfigurationProvider()
        self._orders: dict[str, UserOrder] = {}
        self._sessions: dict[str, ClarificationSession] = {}
        self._brief_versions: dict[str, list[ProjectBrief]] = {}
        self._design_previews: dict[str, list[DesignPreview]] = {}
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
            design_preview = self._latest_design_preview(order_id)
            handoff = self._handoff_by_order.get(order_id)
            execution = self._current_execution(order_id)
        blockers = list(execution.blockers if execution else ())
        next_action = self._next_action(order, session, brief, design_preview, handoff, execution, blockers)
        return {
            "order": order.to_dict(),
            "questions": [item.to_dict() for item in order.questions],
            "answers": [item.to_dict() for item in order.answers],
            "assumptions": list(session.assumptions if session else ()),
            "recommended_defaults_available": bool(order.questions and session and session.remaining_dimensions),
            "brief": brief.to_dict() if brief else None,
            "approval": self._approval_payload(brief),
            "design_preview": design_preview.to_dict() if design_preview else None,
            "design_preview_required": self._design_preview_required(brief),
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
                preview = self._designs.generate(brief) if self._design_preview_required(brief) else None
                if preview is not None:
                    self._design_previews.setdefault(order_id, []).append(preview)
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
            self._design_previews.pop(order_id, None)
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
            preview = self._latest_design_preview(order_id)
            handoff = self._prepare_handoff(approved, preview)
        except OrderWorkflowError:
            raise
        except BriefServiceError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._brief_versions[order_id][-1] = approved
            self._approval_bindings[order_id] = binding
            if handoff is not None:
                self._handoff_by_order[order_id] = handoff
            self._orders[order_id] = self._orders[order_id].model_copy(update={"status": UserOrderStatus.APPROVED})
        return self.snapshot(order_id)

    def get_design_preview(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            self._order(order_id)
            if self._latest_design_preview(order_id) is None:
                raise OrderWorkflowError("design_preview_not_ready", "Generate a design preview after the brief is ready.")
        return self.snapshot(order_id)

    def generate_design_preview(self, order_id: str, *, revision_note: str | None = None) -> dict[str, Any]:
        with self._lock:
            brief = self._require_brief(order_id)
            previous = self._latest_design_preview(order_id)
        if not self._design_preview_required(brief):
            raise OrderWorkflowError("design_preview_not_required", "Elena preview was not selected for this brief.")
        try:
            preview = self._designs.generate(brief, revision_note=revision_note, previous=previous)
        except DesignPreviewError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._design_previews.setdefault(order_id, []).append(preview)
            self._handoff_by_order.pop(order_id, None)
        return self.snapshot(order_id)

    def revise_design_preview(self, order_id: str, note: str) -> dict[str, Any]:
        with self._lock:
            brief = self._require_brief(order_id)
            preview = self._require_design_preview(order_id)
        try:
            requested = self._designs.request_revision(preview, brief, note)
            regenerated = self._designs.generate(brief, previous=requested)
        except DesignPreviewError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._design_previews.setdefault(order_id, []).append(regenerated)
            self._handoff_by_order.pop(order_id, None)
        return self.snapshot(order_id)

    def approve_design_preview(self, order_id: str, *, preview_id: str, brief_version: int) -> dict[str, Any]:
        with self._lock:
            brief = self._require_brief(order_id)
            preview = self._require_design_preview(order_id)
        if preview.preview_id != preview_id or preview.brief_version != brief_version:
            raise OrderWorkflowError("design_preview_stale", "Preview approval applies to an older design preview.")
        try:
            approved_preview = self._designs.approve(preview, brief)
            handoff = self._handoffs.create_implementation_handoff(brief, approved_preview)
        except BriefServiceError as exc:
            raise OrderWorkflowError(exc.code) from None
        except DesignPreviewError as exc:
            raise OrderWorkflowError(exc.code) from None
        with self._lock:
            self._design_previews[order_id][-1] = approved_preview
            self._handoff_by_order[order_id] = handoff
        return self.snapshot(order_id)

    def handoff(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            if order_id not in self._handoff_by_order:
                raise OrderWorkflowError("handoff_blocked", "Approve the current brief before requesting a handoff.")
        return self.snapshot(order_id)

    def start_execution(self, order_id: str, mode: ExecutionMode, *, live: bool = False) -> dict[str, Any]:
        if live and mode is not ExecutionMode.PRODUCTION:
            raise OrderWorkflowError("invalid_execution_mode", "Live execution requires production mode.")
        with self._lock:
            order = self._order(order_id)
            brief = self._require_brief(order_id)
            handoff = self._handoff_by_order.get(order_id)
        if handoff is None:
            raise OrderWorkflowError("design_preview_not_approved" if self._design_preview_required(brief) else "brief_not_approved", "Approve the current brief and Elena preview before execution.")
        try:
            execution = self._executions.start(brief, handoff, mode=mode, live=live)
        except ExecutionServiceError as exc:
            raise OrderWorkflowError(self._execution_error_code(exc.code)) from None
        with self._lock:
            self._execution_by_order[order_id] = execution.id
            next_status = UserOrderStatus.AWAITING_USER if execution.blockers else UserOrderStatus.QUEUED
            self._orders[order_id] = order.model_copy(update={"execution_id": execution.id, "status": next_status})
        return self.snapshot(order_id)

    def execution_readiness(self, order_id: str, mode: ExecutionMode = ExecutionMode.PRODUCTION) -> dict[str, Any]:
        with self._lock:
            self._order(order_id)
            brief = self._latest_brief(order_id)
            design_preview = self._latest_design_preview(order_id)
            handoff = self._handoff_by_order.get(order_id)
            execution_exists = order_id in self._execution_by_order
        simulation_ready = False
        production_ready = False
        config = self._configuration.snapshot()
        blockers = []
        checks = []
        if brief is None:
            blockers.append(readiness_blocker_view(BRIEF_NOT_APPROVED))
            checks.extend(
                (
                    readiness_check("simulation", "Simulation mode", "blocked", "Generate and approve the project brief first."),
                    readiness_check("provider", "Provider", "missing", "Provider is not checked until the brief is approved."),
                    readiness_check("model", "Model", "missing", "Model is not checked until the brief is approved."),
                    readiness_check("workspace", "Workspace", "missing", "Workspace is not checked until the brief is approved."),
                    readiness_check("qa_tools", "QA tools", "missing", "QA tools are not checked until the brief is approved."),
                )
            )
        else:
            fake = self._executions.check_readiness(brief, handoff, mode=ExecutionMode.FAKE)
            production = self._executions.check_readiness(brief, handoff, mode=ExecutionMode.PRODUCTION)
            live = self._executions.check_readiness(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True)
            simulation_ready = fake.ready and handoff is not None and not execution_exists
            production_ready = production.ready and handoff is not None and not execution_exists
            live_ready = live.ready and handoff is not None and not execution_exists
            readiness_blockers = list(production.blockers)
            live_blockers = list(live.blockers)
            if self._design_preview_required(brief) and (design_preview is None or not design_preview.approved):
                readiness_blockers.append(DESIGN_PREVIEW_NOT_APPROVED)
            if handoff is None and not any(item.code == "design_preview_not_approved" for item in readiness_blockers):
                readiness_blockers.extend(fake.blockers)
            blockers.extend(self._public_blocker(item) for item in (*readiness_blockers, *live_blockers))
            checks.extend(self._readiness_checks(simulation_ready, production_ready, live_ready, tuple(blocker.code for blocker in (*readiness_blockers, *live_blockers))))
        if brief is None:
            live_ready = False
        checks = (*self._configuration_checks(config), *tuple(checks))
        blockers.extend(self._configuration_blockers(config))
        view = ExecutionReadinessView(
            ready=production_ready if mode is ExecutionMode.PRODUCTION else simulation_ready,
            mode=mode,
            simulation_ready=simulation_ready,
            production_dry_run_ready=production_ready,
            production_live_ready=live_ready,
            can_run_simulation=simulation_ready,
            can_prepare_dry_run=production_ready,
            can_run_live=live_ready,
            blockers=self._unique_readiness_blockers(blockers),
            checks=tuple(checks),
        )
        return view.to_dict()

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

    def _latest_design_preview(self, order_id: str) -> DesignPreview | None:
        versions = self._design_previews.get(order_id, [])
        return versions[-1] if versions else None

    def _require_brief(self, order_id: str) -> ProjectBrief:
        brief = self._latest_brief(order_id)
        if brief is None:
            raise OrderWorkflowError("brief_not_ready")
        return brief

    def _require_design_preview(self, order_id: str) -> DesignPreview:
        preview = self._latest_design_preview(order_id)
        if preview is None:
            raise OrderWorkflowError("design_preview_not_ready")
        return preview

    def _prepare_handoff(self, brief: ProjectBrief, preview: DesignPreview | None) -> AgentHandoff | None:
        if self._design_preview_required(brief):
            if preview is None or not preview.approved:
                return None
            return self._handoffs.create_implementation_handoff(brief, preview)
        return self._handoffs.create_implementation_handoff(brief)

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
    def _next_action(order: UserOrder, session: ClarificationSession | None, brief: ProjectBrief | None, design_preview: DesignPreview | None, handoff: AgentHandoff | None, execution: ProjectExecution | None, blockers: list[ExecutionBlocker]) -> dict[str, str]:
        if blockers:
            return {"code": blockers[0].code, "message": blockers[0].message}
        if order.status is UserOrderStatus.CLARIFICATION_REQUIRED:
            return {"code": "answer_clarification", "message": "Answer Alex's questions to prepare the project brief."}
        if brief is None:
            return {"code": "generate_brief", "message": "Generate the structured project brief."}
        if brief.approved_at is None:
            return {"code": "approve_brief", "message": "Review and approve the current project brief."}
        if OrderWorkflowService._design_preview_required(brief) and design_preview is None:
            return {"code": "generate_design_preview", "message": "Generate Elena's design preview before implementation."}
        if OrderWorkflowService._design_preview_required(brief) and not design_preview.approved:
            return {"code": "approve_design_preview", "message": "Review and approve Elena's design preview."}
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
            "live_execution_opt_in_required": "live_execution_opt_in_required",
        }.get(code, code)

    @staticmethod
    def _public_blocker(blocker: ExecutionBlocker):
        code = "provider_not_configured" if blocker.code == "execution_provider_not_configured" else blocker.code
        return readiness_blocker_view(blocker, code=code)

    @staticmethod
    def _readiness_checks(fake_ready: bool, production_ready: bool, live_ready: bool, blocker_codes: tuple[str, ...]):
        codes = set(blocker_codes)
        return (
            readiness_check("simulation", "Simulation mode", "ready" if fake_ready else "blocked", "Simulation uses the fake executor and does not require live providers." if fake_ready else "Approve the brief, preview, and handoff before simulation."),
            readiness_check("qa_tools", "QA tools", "missing" if "qa_tools_unavailable" in codes else "ready", "Configure at least one QA command." if "qa_tools_unavailable" in codes else "QA planning requirements are satisfied."),
            readiness_check("production_dry_run", "Production dry-run", "ready" if production_ready else "blocked", "Can prepare a production execution package without live OpenCode." if production_ready else "Resolve production dry-run blockers before preparing a package."),
            readiness_check("live_execution", "Live execution", "ready" if live_ready else "unavailable", "Live OpenCode execution can be started." if live_ready else "Live execution is locked. Set FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION=1 and restart Studio to enable it."),
        )

    @staticmethod
    def _configuration_checks(config):
        provider_status = "ready" if config.provider.configured else "missing" if config.provider.code == "provider_not_configured" else "blocked"
        model_status = "ready" if config.model.supported else "missing" if config.model.code == "model_not_selected" else "blocked"
        workspace_status = "ready" if config.workspace.available and config.workspace.writable else "missing" if not config.workspace.available else "blocked"
        return (
            readiness_check("opencode", "OpenCode", "ready" if config.opencode.available else "missing", config.opencode.message, config.opencode.action),
            readiness_check("provider", "AI provider", provider_status, config.provider.message, config.provider.action),
            readiness_check("model", "Model", model_status, config.model.message, config.model.action),
            readiness_check("workspace", "Workspace", workspace_status, config.workspace.message, config.workspace.action),
            readiness_check("live_opt_in", "Live execution opt-in", "ready" if config.live_opt_in.enabled else "blocked", config.live_opt_in.message, config.live_opt_in.action),
        )

    @staticmethod
    def _configuration_blockers(config):
        from .execution_readiness import ExecutionReadinessBlocker

        blockers = []
        for status in (config.provider, config.model, config.opencode, config.workspace, config.live_opt_in):
            blocked = getattr(status, "configured", True) is False or getattr(status, "supported", True) is False or getattr(status, "available", True) is False or getattr(status, "writable", True) is False or getattr(status, "enabled", True) is False
            if blocked:
                blockers.append(ExecutionReadinessBlocker(code=status.code, message=status.message, action=status.action))
        return tuple(blockers)

    @staticmethod
    def _unique_readiness_blockers(blockers):
        unique = {}
        for blocker in blockers:
            unique.setdefault(blocker.code, blocker)
        return tuple(unique.values())

    @staticmethod
    def _design_preview_required(brief: ProjectBrief | None) -> bool:
        return bool(brief and brief.elena_design_choice is ElenaDesignChoice.SHOW_ELENA_CONCEPT)
