import subprocess

from fastapi.testclient import TestClient

import api.android as android_routes
import android_device_control
import main
from test_security_support import authorized_test_client


client = authorized_test_client(main.app)


def test_android_status_route_uses_router(monkeypatch):
    monkeypatch.setattr(android_routes.android_device_control, "get_status", lambda: {"status": "ok", "devices": []})

    response = client.get("/api/android/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "devices": []}


def test_android_route_payload_mapping_is_unchanged(monkeypatch):
    captured = {}

    def fake_install(serial, apk_path):
        captured["serial"] = serial
        captured["apk_path"] = apk_path
        return {"status": "installed"}

    monkeypatch.setattr(android_routes.android_device_control, "install_apk", fake_install)

    response = client.post("/api/android/apk/install", json={"serial": "emulator-5554", "apk_path": "app.apk"})

    assert response.status_code == 200
    assert response.json() == {"status": "installed"}
    assert captured == {"serial": "emulator-5554", "apk_path": "app.apk"}


def test_android_control_errors_still_return_400(monkeypatch):
    def fail():
        raise android_device_control.AndroidControlError("bad android request")

    monkeypatch.setattr(android_routes.android_device_control, "get_status", fail)

    response = client.post("/api/android/refresh")

    assert response.status_code == 400
    assert response.json() == {"detail": "bad android request"}


def test_android_timeouts_still_return_408(monkeypatch):
    def fail(serial, seconds):
        raise subprocess.TimeoutExpired(cmd=["adb"], timeout=seconds)

    monkeypatch.setattr(android_routes.android_device_control, "record_screen", fail)

    response = client.post("/api/android/record", json={"serial": "emulator-5554", "seconds": 3})

    assert response.status_code == 408
    assert response.json() == {"detail": "Android command timed out"}
