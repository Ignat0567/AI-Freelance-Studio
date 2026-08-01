from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from sandbox_test_lab.sandbox_process_model import (
    LEGACY_CLIENT_NAME,
    REMOTE_SESSION_NAME,
    SERVER_NAME,
    SANDBOX_LAUNCHER_NAME,
    SandboxProcessModel,
    ProcessIdentity,
    _is_trusted_path,
    build_process_identity,
    is_trusted_system32_path,
    is_trusted_windows_apps_path,
    normalize_windows_path,
    validate_launched_after,
    validate_parent_chain,
    validate_identity_stable,
)
from sandbox_test_lab.sandbox_session import (
    OwnedSandboxSession,
    capture_owned_sandbox_session,
    _system32_process_path,
    _powershell_path,
    _controlled_environment,
    CLIENT_PROCESS_NAME,
    REMOTE_SESSION_PROCESS_NAME,
    SERVER_PROCESS_NAME,
)
from sandbox_test_lab import sandbox_session as session_module
from sandbox_test_lab.workspace import WorkspaceError

pytestmark = pytest.mark.unit
LAUNCHED_AT = datetime.now(timezone.utc) - timedelta(seconds=5)


def test_sandbox_process_model_enum_values():
    assert SandboxProcessModel.LEGACY_CLIENT.value == "legacy_client"
    assert SandboxProcessModel.REMOTE_SESSION.value == "remote_session"
    assert SandboxProcessModel.UNKNOWN.value == "unknown"
    assert SandboxProcessModel.AMBIGUOUS.value == "ambiguous"


def test_sandbox_process_model_missing_fallback():
    assert SandboxProcessModel("nonexistent") is SandboxProcessModel.UNKNOWN


def test_sandbox_process_model_sanitized():
    assert SandboxProcessModel.LEGACY_CLIENT.sanitized() == "legacy_client"
    assert SandboxProcessModel.REMOTE_SESSION.sanitized() == "remote_session"


class TestProcessIdentity:
    def test_valid_identity(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\WindowsSandboxClient.exe",
            process_name="WindowsSandboxClient.exe", trusted=True,
        )
        assert identity.pid == 1234
        assert identity.parent_pid == 5678
        assert identity.start_ticks == 638800000000000000
        assert identity.trusted is True

    def test_invalid_pid_rejected(self):
        with pytest.raises(ValueError, match="pid must be a positive integer"):
            ProcessIdentity(
                pid=-1, parent_pid=5678, start_ticks=638800000000000000,
                started_at="2026-07-24T10:00:00.0000000Z",
                path=r"C:\Windows\System32\test.exe",
                process_name="test.exe", trusted=True,
            )

    def test_zero_pid_rejected(self):
        with pytest.raises(ValueError, match="pid must be a positive integer"):
            ProcessIdentity(
                pid=0, parent_pid=5678, start_ticks=638800000000000000,
                started_at="2026-07-24T10:00:00.0000000Z",
                path=r"C:\Windows\System32\test.exe",
                process_name="test.exe", trusted=True,
            )

    def test_bool_pid_rejected(self):
        with pytest.raises(ValueError, match="pid must be a positive integer"):
            ProcessIdentity(
                pid=True, parent_pid=5678, start_ticks=638800000000000000,
                started_at="2026-07-24T10:00:00.0000000Z",
                path=r"C:\Windows\System32\test.exe",
                process_name="test.exe", trusted=True,
            )

    def test_invalid_start_ticks_rejected(self):
        with pytest.raises(ValueError, match="start_ticks must be a positive integer"):
            ProcessIdentity(
                pid=1234, parent_pid=5678, start_ticks=-1,
                started_at="2026-07-24T10:00:00.0000000Z",
                path=r"C:\Windows\System32\test.exe",
                process_name="test.exe", trusted=True,
            )

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="path must be a non-empty string"):
            ProcessIdentity(
                pid=1234, parent_pid=5678, start_ticks=638800000000000000,
                started_at="2026-07-24T10:00:00.0000000Z",
                path="", process_name="test.exe", trusted=True,
            )


class TestNormalizeWindowsPath:
    def test_normalize_standard_path(self):
        result = normalize_windows_path(r"C:\Windows\System32\TEST.EXE")
        assert "\\" in result
        assert result == normalize_windows_path(r"c:\windows\system32\test.exe")

    def test_normalize_empty_path(self):
        assert normalize_windows_path("") == ""

    def test_normalize_none_path(self):
        assert normalize_windows_path(None) == ""  # type: ignore


class TestIsTrustedSystem32Path:
    def test_system32_path_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\Windows\System32\WindowsSandboxClient.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is True

    def test_syswow64_path_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\Windows\SysWOW64\WindowsSandboxRemoteSession.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxRemoteSession.exe") is True

    def test_user_writable_path_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\Users\evil\WindowsSandboxClient.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is False

    def test_wrong_basename_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\Windows\System32\TotallyLegit.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is False

    def test_temp_path_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\Temp\WindowsSandboxClient.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is False

    def test_case_insensitive_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        path = r"C:\WINDOWS\SYSTEM32\WINDOWSSANDBOXCLIENT.EXE"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is True

    def test_no_system_root_returns_false(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {})
        path = r"C:\Windows\System32\WindowsSandboxClient.exe"
        assert is_trusted_system32_path(path, "WindowsSandboxClient.exe") is False

    def test_empty_path_returns_false(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        assert is_trusted_system32_path("", "WindowsSandboxClient.exe") is False


class TestIsTrustedWindowsAppsPath:
    WINDOWS_APPS_RS = (
        r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0_x64__cw5n1h2txyewy"
        r"\WindowsSandboxRemoteSession.exe"
    )
    WINDOWS_APPS_SV = (
        r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0_x64__cw5n1h2txyewy"
        r"\WindowsSandboxServer.exe"
    )

    def test_windows_apps_remote_session_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        assert is_trusted_windows_apps_path(self.WINDOWS_APPS_RS, "WindowsSandboxRemoteSession.exe") is True

    def test_windows_apps_server_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        assert is_trusted_windows_apps_path(self.WINDOWS_APPS_SV, "WindowsSandboxServer.exe") is True

    def test_windows_apps_wrong_basename_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        assert is_trusted_windows_apps_path(self.WINDOWS_APPS_RS, "WindowsSandboxServer.exe") is False

    def test_user_writable_path_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        path = r"C:\Users\evil\WindowsSandboxRemoteSession.exe"
        assert is_trusted_windows_apps_path(path, "WindowsSandboxRemoteSession.exe") is False

    def test_wrong_package_name_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        path = r"C:\Program Files\WindowsApps\SomeOtherPackage\WindowsSandboxRemoteSession.exe"
        assert is_trusted_windows_apps_path(path, "WindowsSandboxRemoteSession.exe") is False

    def test_no_program_files_returns_false(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {})
        assert is_trusted_windows_apps_path(self.WINDOWS_APPS_RS, "WindowsSandboxRemoteSession.exe") is False

    def test_empty_path_returns_false(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        assert is_trusted_windows_apps_path("", "WindowsSandboxRemoteSession.exe") is False

    def test_system32_remote_session_not_rejected_by_windows_apps(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        path = r"C:\Windows\System32\WindowsSandboxRemoteSession.exe"
        assert is_trusted_windows_apps_path(path, "WindowsSandboxRemoteSession.exe") is False

    def test_client_path_rejected_by_windows_apps(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"ProgramFiles": r"C:\Program Files"})
        path = r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0\WindowsSandboxClient.exe"
        assert is_trusted_windows_apps_path(path, "WindowsSandboxClient.exe") is False


class TestIsTrustedPath:
    def test_system32_legacy_client_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = r"C:\Windows\System32\WindowsSandboxClient.exe"
        assert _is_trusted_path(path, "WindowsSandboxClient.exe") is True

    def test_windows_apps_remote_session_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = TestIsTrustedWindowsAppsPath.WINDOWS_APPS_RS
        assert _is_trusted_path(path, "WindowsSandboxRemoteSession.exe") is True

    def test_windows_apps_server_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = TestIsTrustedWindowsAppsPath.WINDOWS_APPS_SV
        assert _is_trusted_path(path, "WindowsSandboxServer.exe") is True

    def test_system32_remote_session_also_trusted(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = r"C:\Windows\System32\WindowsSandboxRemoteSession.exe"
        assert _is_trusted_path(path, "WindowsSandboxRemoteSession.exe") is True

    def test_user_path_remote_session_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = r"C:\Users\evil\WindowsSandboxRemoteSession.exe"
        assert _is_trusted_path(path, "WindowsSandboxRemoteSession.exe") is False

    def test_legacy_client_windows_apps_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        path = r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0\WindowsSandboxClient.exe"
        assert _is_trusted_path(path, "WindowsSandboxClient.exe") is False


class TestValidateLaunchedAfter:
    def test_process_after_launch_accepted(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at=(LAUNCHED_AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_launched_after(identity, LAUNCHED_AT) is True

    def test_process_before_launch_rejected(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at=(LAUNCHED_AT - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_launched_after(identity, LAUNCHED_AT) is False

    def test_naive_datetime_rejected(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00",  # no timezone
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_launched_after(identity, LAUNCHED_AT) is False


class TestValidateParentChain:
    def test_correct_parent_accepted(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_parent_chain(identity, 5678) is True

    def test_wrong_parent_rejected(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_parent_chain(identity, 9999) is False


class TestValidateIdentityStable:
    def test_unchanged_identity_accepted(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_identity_stable(identity, 1234, 638800000000000000, r"C:\Windows\System32\test.exe")

    def test_pid_reuse_detected_by_ticks(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_identity_stable(identity, 1234, 999999999999999999, r"C:\Windows\System32\test.exe") is False

    def test_pid_reuse_detected_by_path(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_identity_stable(identity, 1234, 638800000000000000, r"C:\Windows\System32\hacker.exe") is False

    def test_different_pid_rejected(self):
        identity = ProcessIdentity(
            pid=1234, parent_pid=5678, start_ticks=638800000000000000,
            started_at="2026-07-24T10:00:00.0000000Z",
            path=r"C:\Windows\System32\test.exe",
            process_name="test.exe", trusted=True,
        )
        assert validate_identity_stable(identity, 9999, 638800000000000000, r"C:\Windows\System32\test.exe") is False


class TestBuildProcessIdentity:
    def _after_launch(self, seconds: float = 1.0) -> str:
        return (LAUNCHED_AT + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    def test_valid_legacy_client(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})
        identity = build_process_identity(
            pid=4343, parent_pid=4242, start_ticks=638800000000000000,
            started_at=self._after_launch(),
            path=r"C:\Windows\System32\WindowsSandboxClient.exe",
            process_name="WindowsSandboxClient.exe",
            launched_at=LAUNCHED_AT,
            expected_parent_pid=4242,
        )
        assert identity.pid == 4343
        assert identity.trusted is True

    def test_valid_remote_session_system32(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        identity = build_process_identity(
            pid=47884, parent_pid=47324, start_ticks=638800000000000000,
            started_at=self._after_launch(),
            path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            process_name="WindowsSandboxRemoteSession.exe",
            launched_at=LAUNCHED_AT,
            expected_parent_pid=47324,
        )
        assert identity.pid == 47884
        assert identity.trusted is True

    def test_valid_remote_session_windows_apps(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        rs_path = (
            r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0_x64__cw5n1h2txyewy"
            r"\WindowsSandboxRemoteSession.exe"
        )
        identity = build_process_identity(
            pid=15628, parent_pid=5576, start_ticks=638800000000000000,
            started_at=self._after_launch(),
            path=rs_path,
            process_name="WindowsSandboxRemoteSession.exe",
            launched_at=LAUNCHED_AT,
            expected_parent_pid=5576,
        )
        assert identity.pid == 15628
        assert identity.trusted is True

    def test_valid_server_windows_apps(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        sv_path = (
            r"C:\Program Files\WindowsApps\MicrosoftWindows.WindowsSandbox_0.8.107.0_x64__cw5n1h2txyewy"
            r"\WindowsSandboxServer.exe"
        )
        identity = build_process_identity(
            pid=44124, parent_pid=15628, start_ticks=638800000000000000,
            started_at=self._after_launch(),
            path=sv_path,
            process_name="WindowsSandboxServer.exe",
            launched_at=LAUNCHED_AT,
            expected_parent_pid=15628,
        )
        assert identity.pid == 44124
        assert identity.trusted is True

    def test_untrusted_path_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        with pytest.raises(ValueError, match="untrusted"):
            build_process_identity(
                pid=4343, parent_pid=4242, start_ticks=638800000000000000,
                started_at=(LAUNCHED_AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                path=r"C:\Users\evil\WindowsSandboxClient.exe",
                process_name="WindowsSandboxClient.exe",
                launched_at=LAUNCHED_AT,
                expected_parent_pid=4242,
            )

    def test_lookalike_path_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        with pytest.raises(ValueError, match="untrusted"):
            build_process_identity(
                pid=4343, parent_pid=4242, start_ticks=638800000000000000,
                started_at=(LAUNCHED_AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                path=r"C:\Windows\System32\TotallyLegit.exe",
                process_name="WindowsSandboxClient.exe",
                launched_at=LAUNCHED_AT,
                expected_parent_pid=4242,
            )

    def test_wrong_parent_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        with pytest.raises(ValueError, match="parent identity"):
            build_process_identity(
                pid=4343, parent_pid=9999, start_ticks=638800000000000000,
                started_at=(LAUNCHED_AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                path=r"C:\Windows\System32\WindowsSandboxClient.exe",
                process_name="WindowsSandboxClient.exe",
                launched_at=LAUNCHED_AT,
                expected_parent_pid=4242,
            )

    def test_before_launch_rejected(self, monkeypatch):
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"})
        with pytest.raises(ValueError, match="not created by the current run"):
            build_process_identity(
                pid=4343, parent_pid=4242, start_ticks=638800000000000000,
                started_at=(LAUNCHED_AT - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                path=r"C:\Windows\System32\WindowsSandboxClient.exe",
                process_name="WindowsSandboxClient.exe",
                launched_at=LAUNCHED_AT,
                expected_parent_pid=4242,
            )


class TestOwnedSandboxSession:
    def test_legacy_client_construction(self):
        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        assert session.model == "legacy_client"
        assert session.launcher_pid == 4242
        assert session.client_pid == 4343
        assert session.remote_session_pid is None
        assert session.server_pid is None

    def test_remote_session_construction(self):
        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            server_pid=44352, server_start_ticks=638800000000000002,
            server_path=r"C:\Windows\System32\WindowsSandboxServer.exe",
        )
        assert session.model == "remote_session"
        assert session.launcher_pid == 47324
        assert session.remote_session_pid == 47884
        assert session.server_pid == 44352
        assert session.client_pid is None

    def test_remote_session_without_server(self):
        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        )
        assert session.model == "remote_session"
        assert session.server_pid is None

    def test_legacy_client_missing_fields_rejected(self):
        with pytest.raises(WorkspaceError, match="legacy client identity is incomplete"):
            OwnedSandboxSession(
                model="legacy_client", launcher_pid=4242,
            )

    def test_remote_session_missing_fields_rejected(self):
        with pytest.raises(WorkspaceError, match="remote session identity is incomplete"):
            OwnedSandboxSession(
                model="remote_session", launcher_pid=47324,
            )


class TestLegacyClientControl:
    def test_legacy_client_request_close(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        session.request_close()
        assert len(calls) == 1
        assert "Get-Process -Id 4343" in calls[0]
        assert "CloseMainWindow" in calls[0]
        assert "taskkill" not in calls[0].casefold()

    def test_legacy_client_terminate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        session.terminate()
        assert len(calls) == 1
        assert "Get-Process -Id 4343" in calls[0]
        assert "Kill()" in calls[0]

    def test_legacy_client_pid_reuse_detected_on_close(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            return type("Result", (), {"returncode": 11})()  # wrong ticks = PID reuse
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        with pytest.raises(WorkspaceError, match="owned_sandbox_client_identity_rejected"):
            session.request_close()

    def test_legacy_client_path_change_detected_on_terminate(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            return type("Result", (), {"returncode": 12})()  # wrong path
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        with pytest.raises(WorkspaceError, match="owned_sandbox_client_identity_rejected"):
            session.terminate()


class TestRemoteSessionControl:
    def test_remote_session_request_close(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        )
        session.request_close()
        assert len(calls) == 1
        assert "Get-Process -Id 47884" in calls[0]
        assert "CloseMainWindow" in calls[0]

    def test_remote_session_terminate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        )
        session.terminate()
        assert len(calls) == 1
        assert "Get-Process -Id 47884" in calls[0]
        assert "Kill()" in calls[0]

    def test_terminate_server(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            server_pid=44352, server_start_ticks=638800000000000002,
            server_path=r"C:\Windows\System32\WindowsSandboxServer.exe",
        )
        session.terminate_server()
        assert len(calls) == 1
        assert "Get-Process -Id 44352" in calls[0]
        assert "Kill()" in calls[0]

    def test_terminate_server_without_server_is_noop(self):
        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        )
        session.terminate_server()

    def test_terminate_server_legacy_client_is_noop(self):
        session = OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )
        session.terminate_server()


class TestOwnedSandboxSessionIsRunning:
    """Read-only liveness check -- never issues CloseMainWindow/Kill."""

    def _session(self):
        return OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )

    def test_is_running_true_when_identity_matches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        assert self._session().is_running() is True
        assert len(calls) == 1
        assert "Get-Process -Id 4343" in calls[0]
        assert "CloseMainWindow" not in calls[0]
        assert "Kill()" not in calls[0]
        assert "taskkill" not in calls[0].casefold()

    def test_is_running_false_when_process_gone(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr(session_module.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 1})())

        assert self._session().is_running() is False

    def test_is_running_false_on_timeout(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=10)
        monkeypatch.setattr(session_module.subprocess, "run", run)

        assert self._session().is_running() is False

    def test_is_running_remote_session_model(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        )
        assert session.is_running() is True
        assert "Get-Process -Id 47884" in calls[0]

    def test_is_running_true_when_client_gone_but_server_survives(self, monkeypatch):
        """Regression test for a real 2026-07-31 finding: the remote-session client can
        exit while its server process keeps running -- is_running() must not miss that."""
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            return type("Result", (), {"returncode": 1 if "47884" in argv[-1] else 0})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            server_pid=44352, server_start_ticks=638800000000000002,
            server_path=r"C:\Windows\System32\WindowsSandboxServer.exe",
        )
        assert session.is_running() is True

    def test_is_running_false_when_both_client_and_server_gone(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr(session_module.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 1})())

        session = OwnedSandboxSession(
            model="remote_session", launcher_pid=47324,
            remote_session_pid=47884, remote_session_start_ticks=638800000000000001,
            remote_session_path=r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            server_pid=44352, server_start_ticks=638800000000000002,
            server_path=r"C:\Windows\System32\WindowsSandboxServer.exe",
        )
        assert session.is_running() is False


class TestCaptureWindowPng:
    """Tests for capture_window_png with mocked PowerShell. Real end-to-end capture was
    validated by hand against a live Windows Sandbox on 2026-07-31 (see
    docs/interactive-sandbox-test-lab-phase-5-design.md) -- these tests only cover the
    Python-side plumbing around that PowerShell command."""

    def _session(self):
        return OwnedSandboxSession(
            model="legacy_client", launcher_pid=4242,
            client_pid=4343, client_start_ticks=638800000000000000,
            client_started_at="2026-07-24T10:00:00.0000000Z",
            client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
        )

    def test_capture_returns_decoded_bytes_on_success(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        expected = b"not a real png but good enough to round-trip"
        encoded = base64.b64encode(expected).decode("ascii")
        def run(argv, **kwargs):
            calls.append(argv[-1])
            stdout = f"FRAME_B64_START\n{encoded}\nFRAME_B64_END\n"
            return type("Result", (), {"returncode": 0, "stdout": stdout})()
        monkeypatch.setattr(session_module.subprocess, "run", run)

        result = self._session().capture_window_png(max_width=640)
        assert result == expected
        assert "$TargetPid = 4343" in calls[0]
        assert "$MaxWidth = 640" in calls[0]

    def test_capture_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr(
            session_module.subprocess, "run",
            lambda *a, **k: type("Result", (), {"returncode": 1, "stdout": ""})(),
        )
        assert self._session().capture_window_png() is None

    def test_capture_returns_none_without_markers(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr(
            session_module.subprocess, "run",
            lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": "no markers here"})(),
        )
        assert self._session().capture_window_png() is None

    def test_capture_returns_none_on_timeout(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=20)
        monkeypatch.setattr(session_module.subprocess, "run", run)
        assert self._session().capture_window_png() is None

    def test_capture_returns_none_on_malformed_base64(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr(
            session_module.subprocess, "run",
            lambda *a, **k: type("Result", (), {
                "returncode": 0, "stdout": "FRAME_B64_START\nnot-valid-base64!!!\nFRAME_B64_END\n",
            })(),
        )
        assert self._session().capture_window_png() is None

    def test_capture_rejects_invalid_max_width(self):
        with pytest.raises(ValueError, match="max_width"):
            self._session().capture_window_png(max_width=50)
        with pytest.raises(ValueError, match="max_width"):
            self._session().capture_window_png(max_width=True)

class TestCaptureOwnedSandboxSession:
    """Tests for capture_owned_sandbox_session with mocked PowerShell."""

    RUN_ID = "81a509d2-9f12-43cc-a326-c126aca8187a"
    BASE = datetime.now(timezone.utc) - timedelta(seconds=5)
    CLIENT_PATH = r"C:\Windows\System32\WindowsSandboxClient.exe"
    RS_PATH = r"C:\Windows\System32\WindowsSandboxRemoteSession.exe"
    SV_PATH = r"C:\Windows\System32\WindowsSandboxServer.exe"

    def _payload(self, pid, ppid, start_ticks, path, base=None):
        started_at = (base or self.BASE) + timedelta(milliseconds=100)
        return {
            "process_id": pid,
            "parent_process_id": ppid,
            "start_ticks": start_ticks,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "path": path,
        }

    def test_legacy_client_detected(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(self.CLIENT_PATH) if name == CLIENT_PROCESS_NAME else Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        payload = self._payload(4343, 4242, 638800000000000000, self.CLIENT_PATH)

        def client_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            commands.append(cmd)
            return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

        monkeypatch.setattr(session_module.subprocess, "run", client_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.model == "legacy_client"
        assert identity.client_pid == 4343
        assert identity.launcher_pid == 4242

    def test_remote_session_detected(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path",
            lambda name: Path(self.RS_PATH) if name == REMOTE_SESSION_PROCESS_NAME
            else Path(self.SV_PATH) if name == SERVER_PROCESS_NAME
            else Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        payload_rs = self._payload(47884, 47324, 638800000000000001, self.RS_PATH)
        payload_sv = self._payload(44352, 47884, 638800000000000002, self.SV_PATH)

        def smart_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            commands.append(cmd)
            if "WindowsSandboxClient.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            if "WindowsSandboxRemoteSession.exe" in cmd:
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload_rs)})()
            if "WindowsSandboxServer.exe" in cmd:
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload_sv)})()
            return type("Result", (), {"returncode": 10, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", smart_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(47324, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.model == "remote_session"
        assert identity.remote_session_pid == 47884
        assert identity.server_pid == 44352
        assert identity.launcher_pid == 47324

    def test_remote_session_without_server(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path",
            lambda name: Path(self.RS_PATH) if name == REMOTE_SESSION_PROCESS_NAME
            else Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        payload_rs = self._payload(47884, 47324, 638800000000000001, self.RS_PATH)

        def smart_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            commands.append(cmd)
            if "WindowsSandboxClient.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            if "WindowsSandboxRemoteSession.exe" in cmd:
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload_rs)})()
            if "WindowsSandboxServer.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            return type("Result", (), {"returncode": 10, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", smart_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(47324, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.model == "remote_session"
        assert identity.remote_session_pid == 47884
        assert identity.server_pid is None

    def test_no_process_discovered_fails(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path",
            lambda name: Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})

        def always_10_run(argv, **kwargs):
            return type("Result", (), {"returncode": 10, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", always_10_run)

        clock = type("Clock", (), {"value": 0.0})()

        def monotonic():
            return clock.value

        def sleeper(seconds):
            clock.value += seconds

        clock.value = 999.0

        with pytest.raises(WorkspaceError, match="owned_sandbox_client_discovery_failed"):
            capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)

    def test_untrusted_client_path_rejected(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(self.CLIENT_PATH))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        def path_mismatch_run(argv, **kwargs):
            return type("Result", (), {"returncode": 12, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", path_mismatch_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds
        clock.value = 999.0

        with pytest.raises(WorkspaceError, match="owned_sandbox_client_discovery_failed"):
            capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)

    def test_untrusted_remote_session_path_rejected(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(self.RS_PATH))
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        def smart_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            if "WindowsSandboxClient.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            return type("Result", (), {"returncode": 12, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", smart_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds
        clock.value = 999.0

        with pytest.raises(WorkspaceError, match="owned_sandbox_client_discovery_failed"):
            capture_owned_sandbox_session(47324, self.BASE, monotonic=monotonic, sleeper=sleeper)

    def test_invalid_launcher_pid_rejected(self):
        with pytest.raises(WorkspaceError, match="launcher PID is invalid"):
            capture_owned_sandbox_session(-1, self.BASE)
        with pytest.raises(WorkspaceError, match="launcher PID is invalid"):
            capture_owned_sandbox_session(0, self.BASE)

    def test_naive_launch_time_rejected(self):
        with pytest.raises(WorkspaceError, match="launch time must include a timezone"):
            capture_owned_sandbox_session(4242, datetime(2026, 1, 1))

    def test_pid_reuse_detected_during_capture(self, monkeypatch):
        """PID reuse is detected by the PowerShell identity revalidation in _control_process."""
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(self.CLIENT_PATH) if name == CLIENT_PROCESS_NAME else Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        payload = self._payload(4343, 4242, 638800000000000000, self.CLIENT_PATH)

        def client_run(argv, **kwargs):
            commands.append(argv[-1])
            return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

        monkeypatch.setattr(session_module.subprocess, "run", client_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.client_pid == 4343

    def test_poll_retries_until_deadline(self, monkeypatch):
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(self.CLIENT_PATH) if name == CLIENT_PROCESS_NAME else Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        payload = self._payload(4343, 4242, 638800000000000000, self.CLIENT_PATH)

        call_count = 0
        def delayed_run(argv, **kwargs):
            nonlocal call_count
            call_count += 1
            commands.append(argv[-1])
            if call_count < 3:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

        monkeypatch.setattr(session_module.subprocess, "run", delayed_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.client_pid == 4343
        assert call_count >= 3


class TestRemoteSessionCleanupInRunner:
    """Test that complete_owned_sandbox_session handles RemoteSession cascade."""

    def _make_session_mock(self, **attrs):
        """Create a duck-typed session mock (not a frozen dataclass)."""
        return type("SessionMock", (), {
            "model": "remote_session",
            "launcher_pid": 47324,
            "remote_session_pid": 47884,
            "remote_session_start_ticks": 638800000000000001,
            "remote_session_path": r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
            "server_pid": 44352,
            "server_start_ticks": 638800000000000002,
            "server_path": r"C:\Windows\System32\WindowsSandboxServer.exe",
            "request_close": lambda self: None,
            "terminate": lambda self: None,
            "terminate_server": lambda self: None,
            **attrs,
        })()

    def _clock_and_sleeper(self):
        """Return a mutable clock and sleeper for testing timeout loops."""
        value = [0.0]
        def monotonic():
            return value[0]
        def sleeper(seconds):
            value[0] += seconds if seconds is not None else 0.25
        return value, monotonic, sleeper

    def test_cleanup_remote_session_inactive_immediately(self):
        from sandbox_test_lab.runner import complete_owned_sandbox_session
        process = type("Process", (), {
            "pid": 47324, "returncode": 0,
            "poll": lambda self: self.returncode,
            "terminate": lambda self: None,
            "kill": lambda self: None,
            "wait": lambda self, timeout=None: 0,
        })()
        session = self._make_session_mock()
        session_guard_calls = []

        def guard():
            session_guard_calls.append(True)

        code, exited_at = complete_owned_sandbox_session(
            process, session=session, session_guard=guard,
        )
        assert code == 0
        assert session_guard_calls

    def test_cleanup_remote_session_terminate_server(self):
        from sandbox_test_lab.runner import complete_owned_sandbox_session
        process = type("Process", (), {
            "pid": 47324, "returncode": 0,
            "poll": lambda self: self.returncode,
            "terminate": lambda self: None,
            "kill": lambda self: None,
            "wait": lambda self, timeout=None: 0,
        })()
        state = {"close_called": False, "terminate_called": False}
        active = [True]

        def guard():
            if active[0]:
                raise WorkspaceError("active_windows_sandbox_session")

        def make_request_close(s):
            def fn(self_):
                s["close_called"] = True
            return fn

        def make_terminate(s, a):
            def fn(self_):
                s["terminate_called"] = True
                a[0] = False
            return fn

        session = self._make_session_mock(
            request_close=make_request_close(state),
            terminate=make_terminate(state, active),
            terminate_server=lambda self_: None,
        )

        _, clock_fn, sleeper_fn = self._clock_and_sleeper()
        code, exited_at = complete_owned_sandbox_session(
            process, session=session, session_guard=guard,
            monotonic=clock_fn, sleeper=sleeper_fn,
        )
        assert code == 0
        assert state["close_called"]
        assert state["terminate_called"]

    def test_cleanup_remote_session_server_terminate_needed(self):
        from sandbox_test_lab.runner import complete_owned_sandbox_session
        process = type("Process", (), {
            "pid": 47324, "returncode": 0,
            "poll": lambda self: self.returncode,
            "terminate": lambda self: None,
            "kill": lambda self: None,
            "wait": lambda self, timeout=None: 0,
        })()
        state = {"terminate_called": False, "terminate_server_called": False}
        active = [True]

        def guard():
            if active[0]:
                raise WorkspaceError("active_windows_sandbox_session")

        def make_terminate(s):
            def fn(self_):
                s["terminate_called"] = True
            return fn

        def make_terminate_server(s, a):
            def fn(self_):
                s["terminate_server_called"] = True
                a[0] = False
            return fn

        session = self._make_session_mock(
            request_close=lambda self_: None,
            terminate=make_terminate(state),
            terminate_server=make_terminate_server(state, active),
        )

        _, clock_fn, sleeper_fn = self._clock_and_sleeper()
        code, exited_at = complete_owned_sandbox_session(
            process, session=session, session_guard=guard,
            monotonic=clock_fn, sleeper=sleeper_fn,
        )
        assert code == 0
        assert state["terminate_called"]
        assert state["terminate_server_called"]

    def test_cleanup_remote_session_manual_close_required(self):
        from sandbox_test_lab.runner import complete_owned_sandbox_session
        process = type("Process", (), {
            "pid": 47324, "returncode": 0,
            "poll": lambda self: self.returncode,
            "terminate": lambda self: None,
            "kill": lambda self: None,
            "wait": lambda self, timeout=None: 0,
        })()

        def guard():
            raise WorkspaceError("active_windows_sandbox_session")

        session = self._make_session_mock(
            request_close=lambda self_: None,
            terminate=lambda self_: None,
            terminate_server=lambda self_: None,
        )

        _, clock_fn, sleeper_fn = self._clock_and_sleeper()
        with pytest.raises(WorkspaceError, match="owned_windows_sandbox_session_did_not_exit"):
            complete_owned_sandbox_session(
                process, session=session, session_guard=guard,
                monotonic=clock_fn, sleeper=sleeper_fn,
            )


class TestMultipleSandboxSessions:
    """Simulate two independent Sandbox sessions to verify isolation."""

    BASE = LAUNCHED_AT

    def test_unrelated_session_not_affected(self, monkeypatch):
        """Studio-owned launcher produces one RemoteSession chain;
        an unrelated existing chain must not be adopted."""
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path",
            lambda name: Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})
        monkeypatch.setattr("sandbox_test_lab.sandbox_process_model.os.environ", {"SystemRoot": r"C:\Windows"})

        commands = []
        owned_payload = {
            "process_id": 1001, "parent_process_id": 1000,
            "start_ticks": 638800000000000000,
            "started_at": (self.BASE + timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z"),
            "path": r"C:\Windows\System32\WindowsSandboxRemoteSession.exe",
        }

        def smart_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            commands.append(cmd)
            if "WindowsSandboxClient.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            if "WindowsSandboxRemoteSession.exe" in cmd:
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(owned_payload)})()
            if "WindowsSandboxServer.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            return type("Result", (), {"returncode": 10, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", smart_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds

        identity = capture_owned_sandbox_session(1000, self.BASE, monotonic=monotonic, sleeper=sleeper)
        assert identity.model == "remote_session"
        assert identity.remote_session_pid == 1001
        assert identity.server_pid is None

        query_cmds = [c for c in commands if "ParentProcessId=1000" in c]
        assert len(query_cmds) > 0
        unrelated_queries = [c for c in commands if "ParentProcessId=2000" in c]
        assert len(unrelated_queries) == 0


class TestAmbiguousChain:
    BASE = LAUNCHED_AT

    def test_multiple_candidates_from_launcher_rejected(self, monkeypatch):
        """If two RemoteSessions appear from the same launcher, fail."""
        monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
        monkeypatch.setattr(session_module, "_system32_process_path", lambda name: Path(r"C:\Windows\System32") / name)
        monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})

        def ambiguous_run(argv, **kwargs):
            cmd = argv[-1] if len(argv) > 0 else ""
            if "WindowsSandboxClient.exe" in cmd:
                return type("Result", (), {"returncode": 10, "stdout": ""})()
            # PowerShell exits with 11 = multiple candidates
            return type("Result", (), {"returncode": 11, "stdout": ""})()

        monkeypatch.setattr(session_module.subprocess, "run", ambiguous_run)

        clock = type("Clock", (), {"value": 0.0})()
        def monotonic():
            return clock.value
        def sleeper(seconds):
            clock.value += seconds
        clock.value = 999.0

        with pytest.raises(WorkspaceError, match="owned_sandbox_client_discovery_failed"):
            capture_owned_sandbox_session(4242, self.BASE, monotonic=monotonic, sleeper=sleeper)
