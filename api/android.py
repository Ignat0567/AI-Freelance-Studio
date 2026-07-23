import subprocess

from fastapi import APIRouter, HTTPException
from backend_security import StrictRequestModel as BaseModel

import android_device_control


router = APIRouter()


class AndroidAvdPayload(BaseModel):
    avd_name: str = ""


class AndroidDevicePayload(BaseModel):
    serial: str = ""


class AndroidPackagePayload(BaseModel):
    serial: str = ""
    package_id: str = ""


class AndroidInstallPayload(BaseModel):
    serial: str = ""
    apk_path: str = ""


class AndroidRecordPayload(BaseModel):
    serial: str = ""
    seconds: int = 10


class AndroidLogcatPayload(BaseModel):
    serial: str = ""
    package_id: str = ""
    lines: int = 300


def _android_response(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except android_device_control.AndroidControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Android command timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Android command failed: {exc}")


@router.get("/api/android/status")
def android_status():
    return _android_response(android_device_control.get_status)


@router.post("/api/android/refresh")
def android_refresh():
    return _android_response(android_device_control.get_status)


@router.post("/api/android/avd/start")
def android_start_avd(payload: AndroidAvdPayload):
    return _android_response(android_device_control.start_avd, payload.avd_name)


@router.post("/api/android/emulator/stop")
def android_stop_emulator(payload: AndroidDevicePayload):
    return _android_response(android_device_control.stop_emulator, payload.serial)


@router.post("/api/android/scrcpy/open")
def android_open_scrcpy(payload: AndroidDevicePayload):
    return _android_response(android_device_control.open_scrcpy, payload.serial)


@router.post("/api/android/apk/install")
def android_install_apk(payload: AndroidInstallPayload):
    return _android_response(android_device_control.install_apk, payload.serial, payload.apk_path)


@router.post("/api/android/app/uninstall")
def android_uninstall_app(payload: AndroidPackagePayload):
    return _android_response(android_device_control.uninstall_app, payload.serial, payload.package_id)


@router.post("/api/android/app/launch")
def android_launch_app(payload: AndroidPackagePayload):
    return _android_response(android_device_control.launch_app, payload.serial, payload.package_id)


@router.post("/api/android/app/stop")
def android_stop_app(payload: AndroidPackagePayload):
    return _android_response(android_device_control.stop_app, payload.serial, payload.package_id)


@router.post("/api/android/app/clear-data")
def android_clear_app_data(payload: AndroidPackagePayload):
    return _android_response(android_device_control.clear_app_data, payload.serial, payload.package_id)


@router.post("/api/android/app/package-info")
def android_package_info(payload: AndroidPackagePayload):
    return _android_response(android_device_control.package_info, payload.serial, payload.package_id)


@router.post("/api/android/screenshot")
def android_screenshot(payload: AndroidDevicePayload):
    return _android_response(android_device_control.capture_screenshot, payload.serial)


@router.post("/api/android/record")
def android_record(payload: AndroidRecordPayload):
    return _android_response(android_device_control.record_screen, payload.serial, payload.seconds)


@router.post("/api/android/logcat")
def android_logcat(payload: AndroidLogcatPayload):
    return _android_response(android_device_control.filtered_logcat, payload.serial, payload.package_id, payload.lines)
