from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import logging
from threading import RLock
from typing import Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from backend_security import StrictRequestModel, require_local_only_request
from sandbox_test_lab.adapter import (
    SandboxAvailability,
    SandboxDiagnosticCode,
    SandboxProfile,
    SandboxProgress,
    SandboxReadiness,
    SandboxTestLabAdapter,
)
from sandbox_test_lab.job_service import (
    SandboxJobDisabledError,
    SandboxJobError,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxTestLabJobService,
)


LOGGER = logging.getLogger(__name__)
IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_IDEMPOTENCY_ENTRIES = 256


class TestLabAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


class TestLabRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def guarded_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                code = (
                    "invalid_cancel_request"
                    if request.url.path.endswith("/cancel")
                    else "invalid_launch_request"
                )
                return _error_response(422, code, "The JSON request body is invalid.")
            except TestLabAPIError as exc:
                return _error_response(exc.status_code, exc.code, exc.message)

        return guarded_handler


router = APIRouter(route_class=TestLabRoute)


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TestLabOperation(str, Enum):
    PRODUCTION_SELF_TEST = "production_self_test"
    PRODUCTION_SCREENSHOT = "production_screenshot"


class TestLabPublicStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    LAUNCHING = "launching"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class TestLabLaunchParameters(StrictRequestModel):
    pass


class TestLabLaunchRequest(StrictRequestModel):
    operation: TestLabOperation
    parameters: TestLabLaunchParameters


class TestLabCancelRequest(StrictRequestModel):
    reason: Literal["user_requested"]


class TestLabCapabilityReason(StrictResponseModel):
    code: str
    message: str


class TestLabOperations(StrictResponseModel):
    launch: bool
    cancel: bool
    status: bool
    evidence: bool


class TestLabCapabilitiesResponse(StrictResponseModel):
    available: bool
    reasons: tuple[TestLabCapabilityReason, ...]
    backend_mode: Literal["loopback"]
    operations: TestLabOperations


class TestLabLaunchResponse(StrictResponseModel):
    run_id: str
    status: TestLabPublicStatus


class TestLabProgressResponse(StrictResponseModel):
    phase: str
    message: str


class TestLabResultResponse(StrictResponseModel):
    outcome: Literal["succeeded", "failed", "cancelled", "infrastructure_error"]
    manual_close_required: bool


class TestLabRunResponse(StrictResponseModel):
    run_id: str
    operation: TestLabOperation
    status: TestLabPublicStatus
    terminal: bool
    progress: TestLabProgressResponse
    result: TestLabResultResponse | None
    errors: tuple[str, ...]


class TestLabCancelResponse(StrictResponseModel):
    run_id: str
    status: TestLabPublicStatus
    accepted: bool


class TestLabErrorDetail(StrictResponseModel):
    code: str
    message: str


class TestLabErrorResponse(StrictResponseModel):
    error: TestLabErrorDetail


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    fingerprint: str
    response: TestLabLaunchResponse


class LaunchIdempotencyRegistry:
    def __init__(self, max_entries: int = MAX_IDEMPOTENCY_ENTRIES) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _IdempotencyEntry] = OrderedDict()
        self._lock = RLock()

    def lookup(self, key: str, fingerprint: str) -> TestLabLaunchResponse | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.fingerprint != fingerprint:
                raise TestLabAPIError(
                    409,
                    "idempotency_conflict",
                    "The idempotency key was already used for a different launch request.",
                )
            self._entries.move_to_end(key)
            return entry.response.model_copy(deep=True)

    def execute(
        self,
        key: str,
        fingerprint: str,
        launch: Callable[[], TestLabLaunchResponse],
    ) -> TestLabLaunchResponse:
        with self._lock:
            existing = self.lookup(key, fingerprint)
            if existing is not None:
                return existing
            response = launch()
            self._entries[key] = _IdempotencyEntry(
                fingerprint,
                response.model_copy(deep=True),
            )
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return response


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _default_availability_provider() -> SandboxAvailability:
    return SandboxTestLabAdapter(enabled=True).availability()


def install_sandbox_test_lab_api(
    app: FastAPI,
    *,
    service: SandboxTestLabJobService | None = None,
    availability_provider: Callable[[], SandboxAvailability] | None = None,
    shutdown_timeout: float = 2.0,
) -> None:
    owned_service = service or SandboxTestLabJobService(enabled=False)
    app.state.sandbox_test_lab_job_service = owned_service
    app.state.sandbox_test_lab_availability_provider = (
        availability_provider or _default_availability_provider
    )
    app.state.sandbox_test_lab_idempotency = LaunchIdempotencyRegistry()

    async def shutdown_service() -> None:
        owned_service.shutdown(shutdown_timeout)

    app.add_event_handler("shutdown", shutdown_service)


def get_test_lab_job_service(request: Request) -> SandboxTestLabJobService:
    service = getattr(request.app.state, "sandbox_test_lab_job_service", None)
    if service is None:
        raise TestLabAPIError(503, "job_service_unavailable", "The Test Lab job service is unavailable.")
    return service


def get_test_lab_availability_provider(
    request: Request,
) -> Callable[[], SandboxAvailability]:
    provider = getattr(request.app.state, "sandbox_test_lab_availability_provider", None)
    if provider is None:
        raise TestLabAPIError(503, "job_service_unavailable", "The Test Lab job service is unavailable.")
    return provider


def get_test_lab_idempotency(request: Request) -> LaunchIdempotencyRegistry:
    registry = getattr(request.app.state, "sandbox_test_lab_idempotency", None)
    if registry is None:
        raise TestLabAPIError(503, "job_service_unavailable", "The Test Lab job service is unavailable.")
    return registry


def _capability_response(provider: Callable[[], SandboxAvailability]) -> TestLabCapabilitiesResponse:
    try:
        availability = provider()
    except Exception:
        LOGGER.error("Test Lab capability provider failed")
        availability = None
    if type(availability) is SandboxAvailability and availability.readiness is SandboxReadiness.READY:
        return TestLabCapabilitiesResponse(
            available=True,
            reasons=(),
            backend_mode="loopback",
            operations=TestLabOperations(launch=True, cancel=True, status=True, evidence=False),
        )

    reason = TestLabCapabilityReason(
        code="sandbox_capability_unavailable",
        message="Windows Sandbox capability is unavailable.",
    )
    if type(availability) is SandboxAvailability:
        if availability.readiness is SandboxReadiness.DISABLED:
            reason = TestLabCapabilityReason(
                code="test_lab_disabled",
                message="Sandbox Test Lab is disabled.",
            )
        elif (
            availability.readiness is SandboxReadiness.NOT_READY
            or SandboxDiagnosticCode.SANDBOX_RUNNER_NOT_CONFIGURED in availability.warnings
        ):
            reason = TestLabCapabilityReason(
                code="job_service_unavailable",
                message="Sandbox Test Lab execution is not configured.",
            )
    return TestLabCapabilitiesResponse(
        available=False,
        reasons=(reason,),
        backend_mode="loopback",
        operations=TestLabOperations(launch=False, cancel=False, status=False, evidence=False),
    )


def _require_available(provider: Callable[[], SandboxAvailability]) -> None:
    capability = _capability_response(provider)
    if not capability.available:
        raise TestLabAPIError(
            503,
            "test_lab_unavailable",
            "Sandbox Test Lab is unavailable.",
        )


def _idempotency_key(request: Request) -> str:
    values = [
        value.decode("latin-1")
        for name, value in request.scope.get("headers", [])
        if name.lower() == IDEMPOTENCY_HEADER.lower().encode("ascii")
    ]
    if len(values) != 1:
        raise TestLabAPIError(400, "invalid_launch_request", "A single Idempotency-Key header is required.")
    try:
        canonical = str(UUID(values[0]))
    except (AttributeError, TypeError, ValueError):
        raise TestLabAPIError(400, "invalid_launch_request", "Idempotency-Key must be a canonical UUID.") from None
    if values[0].lower() != canonical:
        raise TestLabAPIError(400, "invalid_launch_request", "Idempotency-Key must be a canonical UUID.")
    return canonical


def _launch_fingerprint(payload: TestLabLaunchRequest) -> str:
    serialized = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def require_unambiguous_json(request: Request) -> None:
    def reject_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_field")
            value[key] = item
        return value

    try:
        json.loads((await request.body()).decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    except ValueError as exc:
        if str(exc) == "duplicate_json_field":
            code = (
                "invalid_cancel_request"
                if request.url.path.endswith("/cancel")
                else "invalid_launch_request"
            )
            raise TestLabAPIError(422, code, "Duplicate JSON fields are not allowed.") from None
        raise


def _operation_profile(operation: TestLabOperation) -> SandboxProfile:
    return {
        TestLabOperation.PRODUCTION_SELF_TEST: SandboxProfile.PRODUCTION_SELF_TEST,
        TestLabOperation.PRODUCTION_SCREENSHOT: SandboxProfile.PRODUCTION_SCREENSHOT,
    }[operation]


_TERMINAL_PUBLIC_STATUSES = frozenset(
    {
        TestLabPublicStatus.SUCCEEDED,
        TestLabPublicStatus.FAILED,
        TestLabPublicStatus.CANCELLED,
        TestLabPublicStatus.INFRASTRUCTURE_ERROR,
    }
)


def _public_status(snapshot: SandboxJobSnapshot) -> TestLabPublicStatus:
    if snapshot.status is SandboxJobStatus.RUNNING and snapshot.progress is SandboxProgress.LAUNCHING:
        return TestLabPublicStatus.LAUNCHING
    return {
        SandboxJobStatus.QUEUED: TestLabPublicStatus.QUEUED,
        SandboxJobStatus.PREPARING: TestLabPublicStatus.PREPARING,
        SandboxJobStatus.RUNNING: TestLabPublicStatus.RUNNING,
        SandboxJobStatus.CANCELLING: TestLabPublicStatus.CANCELLING,
        SandboxJobStatus.PASSED: TestLabPublicStatus.SUCCEEDED,
        SandboxJobStatus.FAILED: TestLabPublicStatus.FAILED,
        SandboxJobStatus.TIMED_OUT: TestLabPublicStatus.FAILED,
        SandboxJobStatus.CANCELLED: TestLabPublicStatus.CANCELLED,
        SandboxJobStatus.INTERRUPTED: TestLabPublicStatus.INFRASTRUCTURE_ERROR,
    }[snapshot.status]


def _run_response(snapshot: SandboxJobSnapshot) -> TestLabRunResponse:
    if type(snapshot) is not SandboxJobSnapshot:
        raise TestLabAPIError(500, "internal_error", "The Test Lab service returned an invalid response.")
    status = _public_status(snapshot)
    progress_messages = {
        SandboxProgress.NOT_STARTED: "The run is queued.",
        SandboxProgress.PREPARING: "The Sandbox run is being prepared.",
        SandboxProgress.LAUNCHING: "Windows Sandbox is launching.",
        SandboxProgress.RUNNING_CHECKS: "Sandbox checks are in progress.",
        SandboxProgress.COLLECTING_EVIDENCE: "Sandbox evidence is being validated.",
        SandboxProgress.CANCELLING: "Cancellation is in progress.",
        SandboxProgress.COMPLETE: "The run is complete.",
    }
    error_map = {
        SandboxJobStatus.FAILED: ("run_failed",),
        SandboxJobStatus.TIMED_OUT: ("run_timed_out",),
        SandboxJobStatus.INTERRUPTED: ("run_interrupted",),
    }
    outcome_map = {
        TestLabPublicStatus.SUCCEEDED: "succeeded",
        TestLabPublicStatus.FAILED: "failed",
        TestLabPublicStatus.CANCELLED: "cancelled",
        TestLabPublicStatus.INFRASTRUCTURE_ERROR: "infrastructure_error",
    }
    result = (
        TestLabResultResponse(
            outcome=outcome_map[status],
            manual_close_required=snapshot.manual_close_required,
        )
        if status in _TERMINAL_PUBLIC_STATUSES
        else None
    )
    return TestLabRunResponse(
        run_id=snapshot.run_id,
        operation=TestLabOperation(snapshot.profile.value),
        status=status,
        terminal=status in _TERMINAL_PUBLIC_STATUSES,
        progress=TestLabProgressResponse(
            phase=snapshot.progress.value,
            message=progress_messages[snapshot.progress],
        ),
        result=result,
        errors=error_map.get(snapshot.status, ()),
    )


def _snapshot(service: SandboxTestLabJobService, run_id: str) -> SandboxJobSnapshot:
    try:
        return service.snapshot(run_id)
    except ValueError:
        raise TestLabAPIError(404, "run_not_found", "The requested Test Lab run was not found.") from None
    except SandboxJobError as exc:
        if str(exc) == "sandbox_job_not_found":
            raise TestLabAPIError(404, "run_not_found", "The requested Test Lab run was not found.") from None
        LOGGER.error("Test Lab snapshot failed: %s", type(exc).__name__)
        raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None
    except Exception as exc:
        LOGGER.error("Test Lab snapshot failed: %s", type(exc).__name__)
        raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None


@router.get(
    "/api/sandbox-test-lab/capabilities",
    response_model=TestLabCapabilitiesResponse,
    responses={403: {"model": TestLabErrorResponse}},
)
def test_lab_capabilities(
    _context=Depends(require_local_only_request),
    provider: Callable[[], SandboxAvailability] = Depends(get_test_lab_availability_provider),
) -> TestLabCapabilitiesResponse:
    return _capability_response(provider)


@router.post(
    "/api/sandbox-test-lab/runs",
    status_code=202,
    response_model=TestLabLaunchResponse,
    responses={400: {"model": TestLabErrorResponse}, 409: {"model": TestLabErrorResponse}},
)
def launch_test_lab_run(
    payload: TestLabLaunchRequest,
    request: Request,
    _context=Depends(require_local_only_request),
    _unambiguous=Depends(require_unambiguous_json),
    service: SandboxTestLabJobService = Depends(get_test_lab_job_service),
    provider: Callable[[], SandboxAvailability] = Depends(get_test_lab_availability_provider),
    idempotency: LaunchIdempotencyRegistry = Depends(get_test_lab_idempotency),
) -> TestLabLaunchResponse:
    key = _idempotency_key(request)
    fingerprint = _launch_fingerprint(payload)
    existing = idempotency.lookup(key, fingerprint)
    if existing is not None:
        return existing
    _require_available(provider)

    def start() -> TestLabLaunchResponse:
        try:
            snapshot = service.start(_operation_profile(payload.operation))
        except ValueError:
            raise TestLabAPIError(422, "invalid_launch_request", "The launch request is invalid.") from None
        except SandboxJobDisabledError:
            raise TestLabAPIError(503, "test_lab_unavailable", "Sandbox Test Lab is unavailable.") from None
        except SandboxJobError as exc:
            code = str(exc)
            if code == "sandbox_job_already_active":
                raise TestLabAPIError(409, "launch_rejected", "Another Test Lab run is active.") from None
            if code in {"sandbox_job_backend_not_configured", "sandbox_job_service_stopping"}:
                raise TestLabAPIError(503, "job_service_unavailable", "The Test Lab job service is unavailable.") from None
            LOGGER.error("Test Lab launch failed: %s", type(exc).__name__)
            raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None
        except Exception as exc:
            LOGGER.error("Test Lab launch failed: %s", type(exc).__name__)
            raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None
        if type(snapshot) is not SandboxJobSnapshot:
            raise TestLabAPIError(500, "internal_error", "The Test Lab service returned an invalid response.")
        return TestLabLaunchResponse(run_id=snapshot.run_id, status=_public_status(snapshot))

    return idempotency.execute(key, fingerprint, start)


@router.get(
    "/api/sandbox-test-lab/runs/{run_id}",
    response_model=TestLabRunResponse,
    responses={404: {"model": TestLabErrorResponse}},
)
def get_test_lab_run(
    run_id: str,
    _context=Depends(require_local_only_request),
    service: SandboxTestLabJobService = Depends(get_test_lab_job_service),
) -> TestLabRunResponse:
    return _run_response(_snapshot(service, run_id))


@router.post(
    "/api/sandbox-test-lab/runs/{run_id}/cancel",
    response_model=TestLabCancelResponse,
    responses={404: {"model": TestLabErrorResponse}, 409: {"model": TestLabErrorResponse}},
)
def cancel_test_lab_run(
    run_id: str,
    _payload: TestLabCancelRequest,
    _context=Depends(require_local_only_request),
    _unambiguous=Depends(require_unambiguous_json),
    service: SandboxTestLabJobService = Depends(get_test_lab_job_service),
) -> TestLabCancelResponse:
    current = _snapshot(service, run_id)
    current_status = _public_status(current)
    if current_status is TestLabPublicStatus.CANCELLED:
        return TestLabCancelResponse(run_id=current.run_id, status=current_status, accepted=False)
    if current_status in _TERMINAL_PUBLIC_STATUSES:
        raise TestLabAPIError(409, "run_already_terminal", "The Test Lab run is already terminal.")
    if current_status is TestLabPublicStatus.CANCELLING:
        return TestLabCancelResponse(run_id=current.run_id, status=current_status, accepted=True)
    try:
        cancelled = service.cancel(run_id)
    except SandboxJobError as exc:
        if str(exc) == "sandbox_job_not_found":
            raise TestLabAPIError(404, "run_not_found", "The requested Test Lab run was not found.") from None
        if str(exc) == "sandbox_job_terminal":
            raced = _snapshot(service, run_id)
            raced_status = _public_status(raced)
            if raced_status is TestLabPublicStatus.CANCELLED:
                return TestLabCancelResponse(run_id=raced.run_id, status=raced_status, accepted=False)
            raise TestLabAPIError(409, "run_already_terminal", "The Test Lab run is already terminal.") from None
        LOGGER.error("Test Lab cancellation failed: %s", type(exc).__name__)
        raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None
    except Exception as exc:
        LOGGER.error("Test Lab cancellation failed: %s", type(exc).__name__)
        raise TestLabAPIError(500, "internal_error", "The Test Lab service failed.") from None
    response = _run_response(cancelled)
    return TestLabCancelResponse(run_id=response.run_id, status=response.status, accepted=True)
