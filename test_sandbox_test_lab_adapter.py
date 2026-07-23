import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from qa_engine import QAEngine
from sandbox_test_lab.adapter import (
    SandboxAvailability,
    SandboxCheckCode,
    SandboxDiagnosticCode,
    SandboxProfile,
    SandboxProgress,
    SandboxReadiness,
    SandboxRepairHandoff,
    SandboxStatus,
    SandboxTestLabAdapter,
    SandboxTestLabDisabledError,
    SandboxTestLabError,
    SandboxTestLabResult,
    SandboxTestLabUnavailableError,
    ValidatedSandboxCheck,
)
from sandbox_test_lab.models import SandboxCapability


def _capability(*, available=True, executable_path=r"C:\Windows\System32\WindowsSandbox.exe"):
    return SandboxCapability(
        supported_os=True,
        windows_edition="Professional",
        windows_build=22631,
        virtualization_available=True,
        sandbox_feature_state="enabled",
        executable_found=True,
        available=available,
        blockers=() if available else ("sandbox_feature_disabled",),
        warnings=(),
        executable_path=executable_path,
        powershell_found=True,
    )


class MockSandboxRunner:
    def __init__(self):
        self.run_id = str(uuid4())
        self.profile = SandboxProfile.PRODUCTION_SELF_TEST
        self.calls = []

    def _result(self, status, **kwargs):
        progress = {
            SandboxStatus.PREPARED: SandboxProgress.PREPARING,
            SandboxStatus.LAUNCHING: SandboxProgress.LAUNCHING,
            SandboxStatus.RUNNING: SandboxProgress.RUNNING_CHECKS,
            SandboxStatus.CANCELLING: SandboxProgress.CANCELLING,
            SandboxStatus.PASSED: SandboxProgress.COMPLETE,
            SandboxStatus.FAILED: SandboxProgress.COMPLETE,
            SandboxStatus.CANCELLED: SandboxProgress.COMPLETE,
        }[status]
        return SandboxTestLabResult(
            run_id=self.run_id,
            profile=self.profile,
            status=status,
            readiness=SandboxReadiness.READY,
            progress=progress,
            **kwargs,
        )

    def prepare(self, profile):
        self.calls.append(("prepare", profile))
        self.profile = profile
        return self._result(SandboxStatus.PREPARED)

    def launch(self, run_id):
        self.calls.append(("launch", run_id))
        return self._result(SandboxStatus.LAUNCHING)

    def status(self, run_id):
        self.calls.append(("status", run_id))
        return self._result(SandboxStatus.RUNNING)

    def evidence(self, run_id):
        self.calls.append(("evidence", run_id))
        return self._result(
            SandboxStatus.PASSED,
            validated_checks=(ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, True),),
            evidence_references=(str(uuid4()),),
            warnings=(SandboxDiagnosticCode.MANUAL_CLOSE_PENDING,),
            manual_close_required=True,
            evidence_validated=True,
        )

    def cancel(self, run_id):
        self.calls.append(("cancel", run_id))
        return self._result(SandboxStatus.CANCELLED, manual_close_required=True)


def _adapter(runner=None, detector=lambda: _capability()):
    return SandboxTestLabAdapter(
        enabled=True,
        runner=runner or MockSandboxRunner(),
        capability_detector=detector,
    )


def test_disabled_adapter_does_not_probe_or_prepare():
    probed = False

    def detector():
        nonlocal probed
        probed = True
        return _capability()

    adapter = SandboxTestLabAdapter(enabled=False, capability_detector=detector)

    availability = adapter.availability()

    assert availability.readiness is SandboxReadiness.DISABLED
    assert not probed
    with pytest.raises(SandboxTestLabDisabledError):
        adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    assert not probed


def test_availability_omits_host_executable_path():
    availability = _adapter(detector=lambda: _capability()).availability()

    assert availability.available is True
    assert availability.readiness is SandboxReadiness.READY
    assert not hasattr(availability, "executable_path")
    assert "WindowsSandbox.exe" not in repr(availability)


def test_available_capability_without_runner_is_not_ready():
    adapter = SandboxTestLabAdapter(enabled=True, capability_detector=lambda: _capability())

    availability = adapter.availability()

    assert availability.available is True
    assert availability.readiness is SandboxReadiness.NOT_READY
    assert availability.warnings == (SandboxDiagnosticCode.SANDBOX_RUNNER_NOT_CONFIGURED,)
    with pytest.raises(SandboxTestLabUnavailableError):
        adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)


def test_adapter_runs_mocked_lifecycle_with_only_normalized_values():
    runner = MockSandboxRunner()
    adapter = _adapter(runner)

    prepared = adapter.prepare("production_self_test")
    launched = adapter.launch(prepared.run_id)
    running = adapter.status(prepared.run_id)
    cancelled = adapter.cancel(prepared.run_id)

    assert [prepared.status, launched.status, running.status, cancelled.status] == [
        SandboxStatus.PREPARED,
        SandboxStatus.LAUNCHING,
        SandboxStatus.RUNNING,
        SandboxStatus.CANCELLED,
    ]
    assert cancelled.manual_close_required is True
    assert [call[0] for call in runner.calls] == ["prepare", "launch", "status", "cancel"]


def test_adapter_rejects_unknown_profiles_and_noncanonical_run_ids():
    adapter = _adapter()

    with pytest.raises(ValueError):
        adapter.prepare("caller_selected_profile")
    with pytest.raises(ValueError):
        adapter.status("not-a-run-id")
    with pytest.raises(RuntimeError, match="sandbox_run_not_prepared"):
        adapter.status(str(uuid4()))


def test_cancel_uses_owner_when_capability_becomes_unavailable():
    capability_calls = 0

    def detector():
        nonlocal capability_calls
        capability_calls += 1
        return _capability(available=capability_calls == 1)

    runner = MockSandboxRunner()
    adapter = _adapter(runner, detector)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    cancelled = adapter.cancel(prepared.run_id)

    assert cancelled.status is SandboxStatus.CANCELLED
    assert capability_calls == 1
    assert runner.calls[-1] == ("cancel", prepared.run_id)


def test_cancel_rejects_unknown_and_malformed_run_ids():
    adapter = _adapter()

    with pytest.raises(SandboxTestLabError, match="sandbox_run_not_prepared"):
        adapter.cancel(str(uuid4()))
    with pytest.raises(ValueError, match="canonical UUID"):
        adapter.cancel("malformed")


def test_cancel_rejects_run_owned_by_another_adapter():
    owner_adapter = _adapter()
    foreign_adapter = _adapter()
    prepared = owner_adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    with pytest.raises(SandboxTestLabError, match="sandbox_run_not_prepared"):
        foreign_adapter.cancel(prepared.run_id)


def test_cancel_rejects_owner_result_with_wrong_profile():
    class WrongProfileRunner(MockSandboxRunner):
        def cancel(self, run_id):
            self.profile = SandboxProfile.PRODUCTION_SCREENSHOT
            return super().cancel(run_id)

    runner = WrongProfileRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    with pytest.raises(SandboxTestLabError, match="sandbox_runner_profile_mismatch"):
        adapter.cancel(prepared.run_id)


def test_cancel_stays_bound_to_original_runner_instance():
    owner = MockSandboxRunner()
    replacement = MockSandboxRunner()
    adapter = _adapter(owner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter._runner = replacement

    adapter.cancel(prepared.run_id)

    assert owner.calls[-1] == ("cancel", prepared.run_id)
    assert replacement.calls == []


def test_prepare_rejects_duplicate_run_id_from_different_runner():
    owner = MockSandboxRunner()
    replacement = MockSandboxRunner()
    replacement.run_id = owner.run_id
    adapter = _adapter(owner)
    adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter._runner = replacement

    with pytest.raises(SandboxTestLabError, match="sandbox_run_id_conflict"):
        adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    adapter.cancel(owner.run_id)
    assert owner.calls[-1] == ("cancel", owner.run_id)
    assert all(call[0] != "cancel" for call in replacement.calls)


def test_cancel_rejects_repeated_cancel_and_terminal_run():
    cancelled_runner = MockSandboxRunner()
    cancelled_adapter = _adapter(cancelled_runner)
    cancelled_run = cancelled_adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    cancelled_adapter.cancel(cancelled_run.run_id)

    with pytest.raises(SandboxTestLabError, match="sandbox_run_terminal"):
        cancelled_adapter.cancel(cancelled_run.run_id)

    passed_runner = MockSandboxRunner()
    passed_adapter = _adapter(passed_runner)
    passed_run = passed_adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    passed_adapter.status(passed_run.run_id)
    passed_adapter.evidence(passed_run.run_id)

    with pytest.raises(SandboxTestLabError, match="sandbox_run_terminal"):
        passed_adapter.cancel(passed_run.run_id)


class ScriptedLifecycleRunner(MockSandboxRunner):
    def __init__(self, *, status_results=(), evidence_results=(), cancel_results=()):
        super().__init__()
        self.status_results = list(status_results)
        self.evidence_results = list(evidence_results)
        self.cancel_results = list(cancel_results)

    def status(self, run_id):
        self.calls.append(("status", run_id))
        return self._result(self.status_results.pop(0))

    def evidence(self, run_id):
        self.calls.append(("evidence", run_id))
        return self._result(self.evidence_results.pop(0), evidence_validated=True)

    def cancel(self, run_id):
        self.calls.append(("cancel", run_id))
        return self._result(self.cancel_results.pop(0))


def _terminal_adapter():
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING, SandboxStatus.RUNNING),
        evidence_results=(SandboxStatus.PASSED, SandboxStatus.FAILED),
        cancel_results=(SandboxStatus.CANCELLED,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.status(prepared.run_id)
    terminal = adapter.evidence(prepared.run_id)
    return adapter, runner, prepared.run_id, terminal


def test_terminal_to_nonterminal_regression_returns_cached_snapshot():
    adapter, runner, run_id, terminal = _terminal_adapter()
    backend_calls = list(runner.calls)

    result = adapter.status(run_id)

    assert result == terminal
    assert result is not terminal
    assert result.status is SandboxStatus.PASSED
    assert runner.calls == backend_calls


def test_terminal_to_different_terminal_result_returns_cached_snapshot():
    adapter, runner, run_id, terminal = _terminal_adapter()
    backend_calls = list(runner.calls)

    result = adapter.evidence(run_id)

    assert result == terminal
    assert result is not terminal
    assert result.status is SandboxStatus.PASSED
    assert runner.calls == backend_calls


def test_status_and_evidence_after_terminal_never_call_backend():
    adapter, runner, run_id, terminal = _terminal_adapter()
    backend_calls = list(runner.calls)

    assert adapter.status(run_id) == terminal
    assert adapter.evidence(run_id) == terminal
    assert runner.calls == backend_calls


def test_cancel_after_terminal_is_rejected_without_backend_call():
    adapter, runner, run_id, _ = _terminal_adapter()
    backend_calls = list(runner.calls)

    with pytest.raises(SandboxTestLabError, match="sandbox_run_terminal"):
        adapter.cancel(run_id)

    assert runner.calls == backend_calls


def test_malicious_backend_mutation_cannot_change_independent_snapshots():
    class RetainingRunner(ScriptedLifecycleRunner):
        def evidence(self, run_id):
            self.calls.append(("evidence", run_id))
            self.backend_result = self._result(
                self.evidence_results.pop(0),
                validated_checks=(ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, True),),
                evidence_references=(str(uuid4()),),
                evidence_validated=True,
            )
            return self.backend_result

    runner = RetainingRunner(
        status_results=(SandboxStatus.RUNNING,),
        evidence_results=(SandboxStatus.PASSED,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.status(prepared.run_id)
    returned = adapter.evidence(prepared.run_id)
    registry_snapshot = adapter._runs[prepared.run_id].snapshot

    assert returned is not runner.backend_result
    assert registry_snapshot is not runner.backend_result
    assert returned is not registry_snapshot
    object.__setattr__(runner.backend_result, "status", SandboxStatus.RUNNING)
    object.__setattr__(runner.backend_result.validated_checks[0], "check_id", "secret_value")

    cached = adapter.status(prepared.run_id)
    assert returned.status is SandboxStatus.PASSED
    assert returned.validated_checks[0].check_id is SandboxCheckCode.APPLICATION_READY
    assert registry_snapshot.status is SandboxStatus.PASSED
    assert registry_snapshot.validated_checks[0].check_id is SandboxCheckCode.APPLICATION_READY
    assert cached == registry_snapshot
    assert cached is not registry_snapshot


def test_unknown_backend_status_is_controlled_and_registry_is_unchanged():
    class UnknownStatusRunner(MockSandboxRunner):
        def status(self, run_id):
            self.calls.append(("status", run_id))
            result = self._result(SandboxStatus.RUNNING)
            object.__setattr__(result, "status", "unknown")
            return result

    runner = UnknownStatusRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    registry_snapshot = adapter._runs[prepared.run_id].snapshot

    with pytest.raises(SandboxTestLabError, match="sandbox_runner_result_invalid"):
        adapter.status(prepared.run_id)

    assert adapter._runs[prepared.run_id].snapshot is registry_snapshot


def test_forbidden_transition_is_controlled_and_registry_is_unchanged():
    runner = ScriptedLifecycleRunner(evidence_results=(SandboxStatus.PASSED,))
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    registry_snapshot = adapter._runs[prepared.run_id].snapshot

    with pytest.raises(SandboxTestLabError, match="sandbox_operation_not_allowed"):
        adapter.evidence(prepared.run_id)

    assert adapter._runs[prepared.run_id].snapshot is registry_snapshot
    assert runner.calls == [("prepare", SandboxProfile.PRODUCTION_SELF_TEST)]


def test_allowed_prepared_running_terminal_transitions():
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING,),
        evidence_results=(SandboxStatus.PASSED,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    running = adapter.status(prepared.run_id)
    terminal = adapter.evidence(prepared.run_id)

    assert running.status is SandboxStatus.RUNNING
    assert terminal.status is SandboxStatus.PASSED


def test_allowed_running_cancelling_terminal_transitions():
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING, SandboxStatus.CANCELLED),
        cancel_results=(SandboxStatus.CANCELLING,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    running = adapter.status(prepared.run_id)
    cancelling = adapter.cancel(prepared.run_id)
    terminal = adapter.status(prepared.run_id)

    assert running.status is SandboxStatus.RUNNING
    assert cancelling.status is SandboxStatus.CANCELLING
    assert terminal.status is SandboxStatus.CANCELLED


@pytest.mark.parametrize(
    ("state", "operation", "error_code"),
    [
        ("prepared", "evidence", "sandbox_operation_not_allowed"),
        ("launching", "launch", "sandbox_operation_not_allowed"),
        ("running", "launch", "sandbox_operation_not_allowed"),
        ("cancelling", "launch", "sandbox_operation_not_allowed"),
        ("cancelling", "cancel", "sandbox_operation_not_allowed"),
        ("passed", "launch", "sandbox_run_terminal"),
        ("passed", "cancel", "sandbox_run_terminal"),
    ],
)
def test_forbidden_operation_state_combinations_never_call_backend(state, operation, error_code):
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING,),
        evidence_results=(SandboxStatus.PASSED,),
        cancel_results=(SandboxStatus.CANCELLING,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    if state == "launching":
        adapter.launch(prepared.run_id)
    elif state in {"running", "cancelling", "passed"}:
        adapter.status(prepared.run_id)
    if state == "cancelling":
        adapter.cancel(prepared.run_id)
    elif state == "passed":
        adapter.evidence(prepared.run_id)
    runner.calls.clear()

    with pytest.raises(SandboxTestLabError, match=error_code):
        getattr(adapter, operation)(prepared.run_id)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("errors", (r"C:\private\artifact.exe",)),
        ("warnings", ("--caller-argument",)),
        ("evidence_references", (r"C:\evidence\result.json",)),
    ],
)
def test_result_rejects_paths_commands_and_nonopaque_evidence(field, value):
    kwargs = {field: value}

    with pytest.raises(ValueError):
        SandboxTestLabResult(
            run_id=str(uuid4()),
            profile=SandboxProfile.PRODUCTION_SELF_TEST,
            status=SandboxStatus.FAILED,
            readiness=SandboxReadiness.READY,
            **kwargs,
        )


@pytest.mark.parametrize("code", list(SandboxDiagnosticCode))
def test_result_accepts_only_declared_diagnostic_codes(code):
    result = SandboxTestLabResult(
        run_id=str(uuid4()),
        profile=SandboxProfile.PRODUCTION_SELF_TEST,
        status=SandboxStatus.FAILED,
        readiness=SandboxReadiness.READY,
        errors=(code,),
    )

    assert result.errors == (code,)


@pytest.mark.parametrize(
    "dangerous_value",
    [
        "unknown_code",
        "token_value",
        "password_value",
        "auth_value",
        "secret_value",
        "api_key_value",
        r"C:\host\result.txt",
        "https://example.invalid/evidence",
        "--run-command",
    ],
)
def test_diagnostic_contract_rejects_unknown_or_dangerous_values_without_echo(dangerous_value):
    with pytest.raises(ValueError) as exc_info:
        SandboxTestLabResult(
            run_id=str(uuid4()),
            profile=SandboxProfile.PRODUCTION_SELF_TEST,
            status=SandboxStatus.FAILED,
            readiness=SandboxReadiness.READY,
            errors=(dangerous_value,),
        )

    assert dangerous_value not in str(exc_info.value)


def test_backend_exception_text_is_not_exposed_by_adapter():
    dangerous_value = "secret_provider_output"

    class FailingRunner(MockSandboxRunner):
        def cancel(self, run_id):
            raise RuntimeError(dangerous_value)

    runner = FailingRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    with pytest.raises(SandboxTestLabError) as exc_info:
        adapter.cancel(prepared.run_id)

    assert str(exc_info.value) == "sandbox_runner_operation_failed"
    assert dangerous_value not in str(exc_info.value)


def test_backend_result_subclass_is_rejected_fail_closed():
    class DerivedResult(SandboxTestLabResult):
        pass

    class SubclassRunner(MockSandboxRunner):
        def status(self, run_id):
            result = self._result(SandboxStatus.RUNNING)
            return DerivedResult(
                run_id=result.run_id,
                profile=result.profile,
                status=result.status,
                readiness=result.readiness,
                progress=result.progress,
            )

    runner = SubclassRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    with pytest.raises(SandboxTestLabError, match="sandbox_runner_result_invalid"):
        adapter.status(prepared.run_id)


class MaliciousStr(str):
    calls = []

    def _called(self, name):
        type(self).calls.append(name)
        raise AssertionError("malicious string method was called")

    def __eq__(self, other):
        return self._called("eq")

    def __hash__(self):
        return self._called("hash")

    def __str__(self):
        return self._called("str")

    def lower(self):
        return self._called("lower")

    def casefold(self):
        return self._called("casefold")


class MaliciousBoolLike:
    calls = []

    def _called(self, name):
        type(self).calls.append(name)
        raise AssertionError("malicious bool-like method was called")

    def __bool__(self):
        return self._called("bool")

    def __eq__(self, other):
        return self._called("eq")

    def __hash__(self):
        return self._called("hash")

    def __str__(self):
        return self._called("str")


def _assert_malicious_methods_not_called():
    assert MaliciousStr.calls == []
    assert MaliciousBoolLike.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", MaliciousStr(str(uuid4()))),
        ("profile", MaliciousStr("production_self_test")),
        ("status", MaliciousStr("failed")),
        ("readiness", MaliciousStr("ready")),
        ("progress", MaliciousStr("complete")),
        ("evidence_references", (MaliciousStr(str(uuid4())),)),
        ("errors", (MaliciousStr("sandbox_unavailable"),)),
        ("warnings", (MaliciousStr("manual_close_pending"),)),
    ],
)
def test_result_rejects_all_malicious_string_and_enum_like_scalars_without_calls(field, value):
    MaliciousStr.calls.clear()
    MaliciousBoolLike.calls.clear()
    values = {
        "run_id": str(uuid4()),
        "profile": SandboxProfile.PRODUCTION_SELF_TEST,
        "status": SandboxStatus.FAILED,
        "readiness": SandboxReadiness.READY,
    }
    values[field] = value

    with pytest.raises(ValueError):
        SandboxTestLabResult(**values)

    _assert_malicious_methods_not_called()


def test_check_code_rejects_malicious_string_subclass_without_calls():
    MaliciousStr.calls.clear()

    with pytest.raises(ValueError):
        ValidatedSandboxCheck(MaliciousStr("application_ready"), True)

    _assert_malicious_methods_not_called()


@pytest.mark.parametrize(
    "factory",
    [
        lambda value: ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, value),
        lambda value: SandboxAvailability(value, SandboxReadiness.READY),
        lambda value: SandboxTestLabResult(
            str(uuid4()),
            SandboxProfile.PRODUCTION_SELF_TEST,
            SandboxStatus.FAILED,
            SandboxReadiness.READY,
            manual_close_required=value,
        ),
        lambda value: SandboxTestLabResult(
            str(uuid4()),
            SandboxProfile.PRODUCTION_SELF_TEST,
            SandboxStatus.FAILED,
            SandboxReadiness.READY,
            evidence_validated=value,
        ),
        lambda value: _valid_handoff(automatic_repair_allowed=value),
    ],
)
def test_bool_fields_reject_malicious_bool_like_values_without_calls(factory):
    MaliciousStr.calls.clear()
    MaliciousBoolLike.calls.clear()

    with pytest.raises(ValueError):
        factory(MaliciousBoolLike())

    _assert_malicious_methods_not_called()


def test_backend_owned_malicious_run_id_is_rejected_before_scalar_methods():
    MaliciousStr.calls.clear()

    class MaliciousRunIdRunner(MockSandboxRunner):
        def status(self, run_id):
            result = self._result(SandboxStatus.RUNNING)
            object.__setattr__(result, "run_id", MaliciousStr(result.run_id))
            return result

    runner = MaliciousRunIdRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    with pytest.raises(SandboxTestLabError, match="sandbox_runner_result_invalid"):
        adapter.status(prepared.run_id)

    _assert_malicious_methods_not_called()


def test_backend_owned_malicious_evidence_reference_is_rejected_before_scalar_methods():
    MaliciousStr.calls.clear()

    class MaliciousEvidenceRunner(MockSandboxRunner):
        def evidence(self, run_id):
            result = super().evidence(run_id)
            object.__setattr__(
                result,
                "evidence_references",
                (MaliciousStr(result.evidence_references[0]),),
            )
            return result

    runner = MaliciousEvidenceRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.status(prepared.run_id)

    with pytest.raises(SandboxTestLabError, match="sandbox_runner_result_invalid"):
        adapter.evidence(prepared.run_id)

    _assert_malicious_methods_not_called()


def test_check_contract_accepts_declared_code_and_rejects_unknown_without_echo():
    assert ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, True).passed is True
    dangerous_value = "secret_check_value"

    with pytest.raises(ValueError) as exc_info:
        ValidatedSandboxCheck(dangerous_value, True)

    assert dangerous_value not in str(exc_info.value)


def _valid_handoff(**overrides):
    values = {
        "run_id": str(uuid4()),
        "profile": SandboxProfile.PRODUCTION_SELF_TEST,
        "status": SandboxStatus.PASSED,
        "readiness": SandboxReadiness.READY,
        "progress": SandboxProgress.COMPLETE,
        "validated_checks": (ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, True),),
        "evidence_references": (str(uuid4()),),
        "errors": (),
        "warnings": (SandboxDiagnosticCode.MANUAL_CLOSE_PENDING,),
        "manual_close_required": True,
        "evidence_validated": True,
        "automatic_repair_allowed": False,
    }
    values.update(overrides)
    return SandboxRepairHandoff(**values)


def test_repair_handoff_direct_construction_accepts_valid_normalized_payload():
    handoff = _valid_handoff()

    assert handoff.automatic_repair_allowed is False
    assert isinstance(handoff.validated_checks, tuple)
    assert isinstance(handoff.evidence_references, tuple)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "malformed"),
        ("profile", "production_self_test"),
        ("status", "passed"),
        ("status", SandboxStatus.RUNNING),
        ("readiness", "ready"),
        ("progress", "complete"),
        ("validated_checks", []),
        ("evidence_references", []),
        ("errors", []),
        ("warnings", []),
        ("manual_close_required", 1),
        ("evidence_validated", False),
        ("automatic_repair_allowed", True),
    ],
)
def test_repair_handoff_rejects_invalid_types_and_automatic_repair(field, value):
    with pytest.raises(ValueError):
        _valid_handoff(**{field: value})


@pytest.mark.parametrize(
    "reference",
    [
        r"C:\evidence\result.json",
        r"\\server\share\result.json",
        "https://example.invalid/result",
        "../result.json",
        "%TEMP%/result.json",
        "$HOME/result.json",
        "host/results/result.json",
    ],
)
def test_repair_handoff_rejects_nonlogical_evidence_without_echo(reference):
    with pytest.raises(ValueError) as exc_info:
        _valid_handoff(evidence_references=(reference,))

    assert reference not in str(exc_info.value)


def test_repair_handoff_rejects_unknown_extra_payload():
    with pytest.raises(TypeError):
        _valid_handoff(unknown_payload="value")


def test_repair_handoff_is_typed_and_never_enables_automatic_repair():
    runner = MockSandboxRunner()
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.status(prepared.run_id)
    adapter.evidence(prepared.run_id)
    runner.calls.clear()

    handoff = adapter.repair_handoff(prepared.run_id)

    assert handoff.status is SandboxStatus.PASSED
    assert handoff.automatic_repair_allowed is False
    assert handoff.validated_checks == (
        ValidatedSandboxCheck(SandboxCheckCode.APPLICATION_READY, True),
    )
    assert handoff.manual_close_required is True
    assert handoff.evidence_validated is True
    assert runner.calls == []


@pytest.mark.parametrize("state", ["prepared", "running", "cancelling"])
def test_repair_handoff_rejects_nonterminal_states_without_backend_call(state):
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING,),
        cancel_results=(SandboxStatus.CANCELLING,),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    if state in {"running", "cancelling"}:
        adapter.status(prepared.run_id)
    if state == "cancelling":
        adapter.cancel(prepared.run_id)
    runner.calls.clear()

    with pytest.raises(SandboxTestLabError, match="sandbox_repair_handoff_not_terminal"):
        adapter.repair_handoff(prepared.run_id)

    assert runner.calls == []


def test_repair_handoff_rejects_unvalidated_terminal_without_backend_call():
    runner = ScriptedLifecycleRunner(
        status_results=(SandboxStatus.RUNNING, SandboxStatus.FAILED),
    )
    adapter = _adapter(runner)
    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.status(prepared.run_id)
    adapter.status(prepared.run_id)
    runner.calls.clear()

    with pytest.raises(SandboxTestLabError, match="sandbox_repair_handoff_evidence_invalid"):
        adapter.repair_handoff(prepared.run_id)

    assert runner.calls == []


def test_repair_handoff_rejects_unknown_and_malformed_run_without_backend_call():
    runner = MockSandboxRunner()
    adapter = _adapter(runner)

    with pytest.raises(SandboxTestLabError, match="sandbox_run_not_prepared"):
        adapter.repair_handoff(str(uuid4()))
    with pytest.raises(ValueError, match="canonical UUID"):
        adapter.repair_handoff("malformed")

    assert runner.calls == []


def test_qa_engine_keeps_test_lab_disabled_by_default(tmp_path):
    engine = QAEngine({"title": "Test", "logs": []}, tmp_path, "p1", "test", "test", 0)

    assert engine.sandbox_availability().readiness is SandboxReadiness.DISABLED
    with pytest.raises(SandboxTestLabDisabledError):
        engine.prepare_sandbox_run(SandboxProfile.PRODUCTION_SELF_TEST)


def test_qa_engine_delegates_explicit_test_lab_handoff(tmp_path):
    runner = MockSandboxRunner()
    adapter = _adapter(runner)
    engine = QAEngine(
        {"title": "Test", "logs": []},
        tmp_path,
        "p1",
        "test",
        "test",
        0,
        sandbox_test_lab_enabled=True,
        sandbox_test_lab_adapter=adapter,
    )

    prepared = engine.prepare_sandbox_run(SandboxProfile.PRODUCTION_SELF_TEST)
    engine.sandbox_run_status(prepared.run_id)
    engine.sandbox_run_evidence(prepared.run_id)
    handoff = engine.sandbox_repair_handoff(prepared.run_id)

    assert handoff.run_id == prepared.run_id
    assert handoff.automatic_repair_allowed is False


class PassingQAEngine(QAEngine):
    def stage_check_syntax(self):
        return True, []

    def stage_install_deps(self):
        return True, [], []

    def stage_build_and_run(self):
        return True, [], []

    def stage_delivery_readiness(self):
        return True, [], []

    def stage_profile_checks(self):
        return True, [], []

    def stage_verify_files(self):
        return True, [], []


def test_normal_qa_never_invokes_injected_sandbox_runner(tmp_path):
    runner = MockSandboxRunner()
    engine = PassingQAEngine(
        {"title": "Test", "logs": [], "_qa_repair_limit": 0},
        tmp_path,
        "p1",
        "test",
        "test",
        0,
        sandbox_test_lab_enabled=True,
        sandbox_test_lab_adapter=_adapter(runner),
    )

    result = engine.run()

    assert result["success"] is True
    assert runner.calls == []


_EXECUTABLE_SANDBOX_MODULES = {
    "sandbox_test_lab.fixture_builder",
    "sandbox_test_lab.fixture_installation",
    "sandbox_test_lab.fixture_launch",
    "sandbox_test_lab.installation_runner",
    "sandbox_test_lab.installer",
    "sandbox_test_lab.launch_runner",
    "sandbox_test_lab.payload_load_probe",
    "sandbox_test_lab.production_runner",
    "sandbox_test_lab.production_self_test",
    "sandbox_test_lab.runner",
    "sandbox_test_lab.screenshot_runner",
}


def _fresh_python(code):
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("module_name", ["sandbox_test_lab.adapter", "qa_engine"])
def test_fresh_import_does_not_load_executable_sandbox_modules(module_name):
    loaded = _fresh_python(
        "import importlib, json, sys; "
        f"importlib.import_module({module_name!r}); "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert _EXECUTABLE_SANDBOX_MODULES.isdisjoint(loaded)


def test_legacy_runner_export_loads_only_requested_harness_module_lazily():
    loaded = _fresh_python(
        "import json, sys, sandbox_test_lab; "
        "before = sorted(sys.modules); "
        "runner = sandbox_test_lab.SandboxRunner; "
        "print(json.dumps({'before': before, 'after': sorted(sys.modules), 'name': runner.__name__}))"
    )

    assert "sandbox_test_lab.runner" not in loaded["before"]
    assert loaded["name"] == "SandboxRunner"
    assert "sandbox_test_lab.runner" in loaded["after"]
    assert "sandbox_test_lab.installer" not in loaded["after"]
    assert "sandbox_test_lab.production_runner" not in loaded["after"]


def test_unknown_lazy_export_raises_attribute_error_in_fresh_interpreter():
    result = _fresh_python(
        "import json, sandbox_test_lab; "
        "\ntry:\n sandbox_test_lab.unknown_export\nexcept AttributeError:\n "
        "print(json.dumps({'attribute_error': True}))"
    )

    assert result == {"attribute_error": True}


def test_legacy_public_exports_remain_compatible_in_fresh_interpreter():
    result = _fresh_python(
        "import json; "
        "from sandbox_test_lab import RunStatus, SandboxRunner, plan_installation; "
        "print(json.dumps({'status': RunStatus.CREATED.value, 'runner': SandboxRunner.__name__, "
        "'planner': plan_installation.__name__}))"
    )

    assert result == {
        "status": "created",
        "runner": "SandboxRunner",
        "planner": "plan_installation",
    }
