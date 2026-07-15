import subprocess

import pytest

import android_device_control as android


def test_sdk_detection_uses_localappdata(monkeypatch, tmp_path):
    sdk = tmp_path / "Android" / "Sdk"
    sdk.mkdir(parents=True)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = android.detect_android_sdk()
    assert result["found"] is True
    assert result["path"] == str(sdk)


def test_adb_detection_uses_sdk_platform_tools(monkeypatch, tmp_path):
    sdk = tmp_path / "Android" / "Sdk"
    platform_tools = sdk / "platform-tools"
    platform_tools.mkdir(parents=True)
    adb = platform_tools / ("adb.exe" if android.os.name == "nt" else "adb")
    adb.write_text("", encoding="utf-8")
    monkeypatch.setattr(android.shutil, "which", lambda _: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = android.detect_adb()
    assert result["found"] is True
    assert result["path"] == str(adb)


def test_avd_listing_parses_emulator_output(monkeypatch):
    monkeypatch.setattr(android, "detect_emulator", lambda: {"found": True, "path": "emulator.exe"})
    monkeypatch.setattr(android, "_run", lambda *_, **__: subprocess.CompletedProcess([], 0, stdout="Pixel_9\nTablet_API_35\n", stderr=""))
    assert android.list_avds() == ["Pixel_9", "Tablet_API_35"]


def test_connected_device_parsing_handles_offline_and_unauthorized():
    output = "List of devices attached\nemulator-5554 offline product:sdk model:Pixel\nabc123 unauthorized usb:1-1\nxyz device product:test model:Phone\n"
    devices = android.parse_adb_devices(output)
    assert [item["status"] for item in devices] == ["offline", "unauthorized", "device"]
    assert devices[0]["details"]["model"] == "Pixel"


def test_command_construction_rejects_unsafe_serial(monkeypatch):
    monkeypatch.setattr(android, "detect_adb", lambda: {"found": True, "path": "adb.exe"})
    with pytest.raises(android.AndroidControlError):
        android.adb_command("emulator-5554 & del *", "devices")


def test_apk_install_result_is_parsed(monkeypatch, tmp_path):
    apk = tmp_path / "app.apk"
    apk.write_text("apk", encoding="utf-8")
    monkeypatch.setattr(android, "detect_adb", lambda: {"found": True, "path": "adb.exe"})
    monkeypatch.setattr(android, "_run", lambda *_, **__: subprocess.CompletedProcess([], 0, stdout="Success\n", stderr=""))
    result = android.install_apk("emulator-5554", str(apk))
    assert result["status"] == "installed"
    assert result["apk"] == str(apk)


def test_app_launch_result_records_timestamp(monkeypatch):
    monkeypatch.setattr(android, "detect_adb", lambda: {"found": True, "path": "adb.exe"})
    monkeypatch.setattr(android, "_run", lambda *_, **__: subprocess.CompletedProcess([], 0, stdout="Events injected: 1\n", stderr=""))
    result = android.launch_app("emulator-5554", "com.example.app")
    assert result["status"] == "launched"
    assert result["timestamp"]


def test_screenshot_evidence_is_stored(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(android, "detect_adb", lambda: {"found": True, "path": "adb.exe"})
    monkeypatch.setattr(android, "_run", lambda *_, **__: subprocess.CompletedProcess([], 0, stdout=b"PNGDATA", stderr=b""))
    result = android.capture_screenshot("emulator-5554")
    assert result["status"] == "captured"
    assert (tmp_path / result["path"]).read_bytes() == b"PNGDATA"


def test_logcat_is_redacted():
    text = "Authorization: Bearer secret-token\napi_key=abc123\npassword=hunter2"
    redacted = android.redact_logcat(text)
    assert "secret-token" not in redacted
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "<redacted>" in redacted


def test_no_avd_delete_operation_is_exposed():
    assert not hasattr(android, "delete_avd")
    assert not hasattr(android, "remove_avd")
