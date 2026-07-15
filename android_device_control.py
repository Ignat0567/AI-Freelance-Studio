import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&]+"),
]


class AndroidControlError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_logcat(text: str) -> str:
    redacted = str(text or "")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}<redacted>", redacted)
    return redacted


def _local_sdk_candidates() -> list[Path]:
    candidates = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value).expanduser())
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Android" / "Sdk")
    candidates.append(Path.home() / "AppData" / "Local" / "Android" / "Sdk")
    return candidates


def detect_android_sdk() -> dict[str, Any]:
    checked = []
    for candidate in _local_sdk_candidates():
        expanded = Path(os.path.expandvars(str(candidate))).expanduser()
        checked.append(str(expanded))
        if expanded.exists():
            return {"found": True, "path": str(expanded), "checked": checked}
    return {"found": False, "path": "", "checked": checked}


def _find_executable(name: str, relative_parts: list[str]) -> dict[str, Any]:
    checked = []
    found = shutil.which(name)
    if found:
        return {"found": True, "path": found, "checked": [found]}
    sdk = detect_android_sdk()
    if sdk["path"]:
        candidate = Path(sdk["path"]).joinpath(*relative_parts)
        checked.append(str(candidate))
        if candidate.exists():
            return {"found": True, "path": str(candidate), "checked": checked}
    return {"found": False, "path": "", "checked": checked}


def detect_adb() -> dict[str, Any]:
    return _find_executable("adb.exe" if os.name == "nt" else "adb", ["platform-tools", "adb.exe" if os.name == "nt" else "adb"])


def detect_emulator() -> dict[str, Any]:
    return _find_executable("emulator.exe" if os.name == "nt" else "emulator", ["emulator", "emulator.exe" if os.name == "nt" else "emulator"])


def detect_scrcpy() -> dict[str, Any]:
    found = shutil.which("scrcpy.exe" if os.name == "nt" else "scrcpy")
    return {"found": bool(found), "path": found or "", "checked": [found] if found else []}


def _require_path(record: dict[str, Any], label: str) -> str:
    path = record.get("path") or ""
    if not path:
        raise AndroidControlError(f"{label} was not found")
    return path


def _run(args: list[str], timeout: int = 20, binary: bool = False) -> subprocess.CompletedProcess:
    if not args or not all(isinstance(arg, str) and arg for arg in args):
        raise AndroidControlError("Invalid command arguments")
    return subprocess.run(args, capture_output=True, text=not binary, timeout=timeout, shell=False)


def _validate_id(value: str, label: str = "value") -> str:
    text = str(value or "").strip()
    if not text or not SAFE_ID_RE.match(text):
        raise AndroidControlError(f"Invalid {label}")
    return text


def _validate_package(package_id: str) -> str:
    text = str(package_id or "").strip()
    if not PACKAGE_RE.match(text):
        raise AndroidControlError("Invalid package ID")
    return text


def _validate_apk(apk_path: str) -> str:
    text = os.path.abspath(os.path.expandvars(os.path.expanduser(str(apk_path or "").strip().strip('"'))))
    if not text.lower().endswith(".apk") or not os.path.exists(text):
        raise AndroidControlError("APK path must point to an existing .apk file")
    return text


def adb_command(serial: str | None, *parts: str) -> list[str]:
    adb = _require_path(detect_adb(), "adb")
    args = [adb]
    if serial:
        args.extend(["-s", _validate_id(serial, "device serial")])
    args.extend(str(part) for part in parts)
    return args


def list_avds() -> list[str]:
    emulator = detect_emulator()
    if not emulator["path"]:
        return []
    result = _run([emulator["path"], "-list-avds"], timeout=15)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def parse_adb_devices(output: str) -> list[dict[str, Any]]:
    devices = []
    for line in str(output or "").splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        details = {}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                details[key] = value
        devices.append({"serial": serial, "status": state, "details": details})
    return devices


def _device_prop(serial: str, prop: str, timeout: int = 5) -> str:
    result = _run(adb_command(serial, "shell", "getprop", prop), timeout=timeout)
    return result.stdout.strip() if result.returncode == 0 else ""


def _device_wm_size(serial: str) -> str:
    result = _run(adb_command(serial, "shell", "wm", "size"), timeout=5)
    if result.returncode != 0:
        return ""
    return result.stdout.strip().replace("Physical size: ", "")


def list_connected_devices() -> list[dict[str, Any]]:
    adb = detect_adb()
    if not adb["path"]:
        return []
    result = _run([adb["path"], "devices", "-l"], timeout=15)
    devices = parse_adb_devices(result.stdout)
    for device in devices:
        if device["status"] == "device":
            boot_completed = _device_prop(device["serial"], "sys.boot_completed")
            if boot_completed != "1":
                device["status"] = "booting"
            device["android_version"] = _device_prop(device["serial"], "ro.build.version.release")
            device["api_level"] = _device_prop(device["serial"], "ro.build.version.sdk")
            device["resolution"] = _device_wm_size(device["serial"])
        device["avd_name"] = device.get("details", {}).get("model", "") if device["serial"].startswith("emulator-") else ""
    return devices


def get_status() -> dict[str, Any]:
    return {
        "timestamp": _now(),
        "sdk": detect_android_sdk(),
        "adb": detect_adb(),
        "emulator": detect_emulator(),
        "scrcpy": detect_scrcpy(),
        "avds": list_avds(),
        "devices": list_connected_devices(),
    }


def start_avd(avd_name: str) -> dict[str, Any]:
    avd = _validate_id(avd_name, "AVD name")
    if avd not in list_avds():
        raise AndroidControlError("AVD does not exist")
    emulator = _require_path(detect_emulator(), "emulator")
    subprocess.Popen([emulator, "-avd", avd], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "starting", "avd": avd, "command": [emulator, "-avd", avd]}


def stop_emulator(serial: str) -> dict[str, Any]:
    serial = _validate_id(serial, "device serial")
    if not serial.startswith("emulator-"):
        raise AndroidControlError("Only emulator serials can be stopped")
    result = _run(adb_command(serial, "emu", "kill"), timeout=10)
    return {"status": "stopping" if result.returncode == 0 else "failed", "serial": serial, "stderr": redact_logcat(result.stderr)}


def open_scrcpy(serial: str) -> dict[str, Any]:
    scrcpy = _require_path(detect_scrcpy(), "scrcpy")
    serial = _validate_id(serial, "device serial")
    subprocess.Popen([scrcpy, "--serial", serial], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "opened", "serial": serial, "scrcpy": scrcpy}


def install_apk(serial: str, apk_path: str) -> dict[str, Any]:
    apk = _validate_apk(apk_path)
    result = _run(adb_command(serial, "install", "-r", apk), timeout=180)
    text = redact_logcat(result.stdout + result.stderr)
    return {"status": "installed" if "Success" in text else "failed", "serial": serial, "apk": apk, "output": text[-2000:]}


def uninstall_app(serial: str, package_id: str) -> dict[str, Any]:
    package_id = _validate_package(package_id)
    result = _run(adb_command(serial, "uninstall", package_id), timeout=60)
    text = redact_logcat(result.stdout + result.stderr)
    return {"status": "uninstalled" if "Success" in text else "failed", "serial": serial, "package_id": package_id, "output": text[-2000:]}


def launch_app(serial: str, package_id: str) -> dict[str, Any]:
    package_id = _validate_package(package_id)
    result = _run(adb_command(serial, "shell", "monkey", "-p", package_id, "-c", "android.intent.category.LAUNCHER", "1"), timeout=20)
    text = redact_logcat(result.stdout + result.stderr)
    return {"status": "launched" if result.returncode == 0 and "Error" not in text else "failed", "serial": serial, "package_id": package_id, "output": text[-2000:], "timestamp": _now()}


def stop_app(serial: str, package_id: str) -> dict[str, Any]:
    package_id = _validate_package(package_id)
    result = _run(adb_command(serial, "shell", "am", "force-stop", package_id), timeout=20)
    return {"status": "stopped" if result.returncode == 0 else "failed", "serial": serial, "package_id": package_id}


def clear_app_data(serial: str, package_id: str) -> dict[str, Any]:
    package_id = _validate_package(package_id)
    result = _run(adb_command(serial, "shell", "pm", "clear", package_id), timeout=30)
    text = redact_logcat(result.stdout + result.stderr)
    return {"status": "cleared" if "Success" in text else "failed", "serial": serial, "package_id": package_id, "output": text[-2000:]}


def package_info(serial: str, package_id: str) -> dict[str, Any]:
    package_id = _validate_package(package_id)
    result = _run(adb_command(serial, "shell", "dumpsys", "package", package_id), timeout=20)
    text = redact_logcat(result.stdout)
    version_name = re.search(r"versionName=([^\s]+)", text)
    version_code = re.search(r"versionCode=(\d+)", text)
    return {"status": "found" if result.returncode == 0 and package_id in text else "not_found", "serial": serial, "package_id": package_id, "version_name": version_name.group(1) if version_name else "", "version_code": version_code.group(1) if version_code else "", "raw_tail": text[-2000:]}


def _artifact_dir() -> Path:
    target = Path("android_artifacts")
    target.mkdir(exist_ok=True)
    return target


def capture_screenshot(serial: str) -> dict[str, Any]:
    serial = _validate_id(serial, "device serial")
    result = _run(adb_command(serial, "exec-out", "screencap", "-p"), timeout=30, binary=True)
    if result.returncode != 0:
        raise AndroidControlError("Screenshot capture failed")
    path = _artifact_dir() / f"screenshot_{serial}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path.write_bytes(result.stdout)
    return {"status": "captured", "serial": serial, "path": str(path), "timestamp": _now()}


def record_screen(serial: str, seconds: int = 10) -> dict[str, Any]:
    serial = _validate_id(serial, "device serial")
    duration = max(1, min(int(seconds or 10), 180))
    remote = f"/sdcard/freelancerstudio_record_{int(datetime.now().timestamp())}.mp4"
    result = _run(adb_command(serial, "shell", "screenrecord", "--time-limit", str(duration), remote), timeout=duration + 20)
    if result.returncode != 0:
        raise AndroidControlError("Screen recording failed")
    path = _artifact_dir() / f"record_{serial}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    pull = _run(adb_command(serial, "pull", remote, str(path)), timeout=60)
    _run(adb_command(serial, "shell", "rm", remote), timeout=10)
    return {"status": "recorded" if pull.returncode == 0 else "failed", "serial": serial, "path": str(path), "seconds": duration, "timestamp": _now()}


def filtered_logcat(serial: str, package_id: str = "", lines: int = 300) -> dict[str, Any]:
    serial = _validate_id(serial, "device serial")
    limit = max(20, min(int(lines or 300), 2000))
    args = adb_command(serial, "logcat", "-d", "-t", str(limit))
    if package_id:
        _validate_package(package_id)
    result = _run(args, timeout=30)
    text = redact_logcat(result.stdout + result.stderr)
    if package_id:
        text = "\n".join(line for line in text.splitlines() if package_id in line)
    return {"status": "ok" if result.returncode == 0 else "failed", "serial": serial, "package_id": package_id, "logcat": text[-20000:]}
