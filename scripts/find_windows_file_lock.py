import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path


CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
ERROR_MORE_DATA = 234


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", wintypes.DWORD),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def locking_processes(path):
    restart_manager = ctypes.WinDLL("Rstrtmgr")
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    result = restart_manager.RmStartSession(ctypes.byref(session), 0, key)
    if result:
        raise OSError(result, "RmStartSession failed")
    try:
        resources = (wintypes.LPCWSTR * 1)(str(path))
        result = restart_manager.RmRegisterResources(session, 1, resources, 0, None, 0, None)
        if result:
            raise OSError(result, "RmRegisterResources failed")
        needed = wintypes.UINT()
        count = wintypes.UINT()
        reasons = wintypes.DWORD()
        result = restart_manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if result not in (0, ERROR_MORE_DATA):
            raise OSError(result, "RmGetList sizing failed")
        if not needed.value:
            return []
        processes = (RM_PROCESS_INFO * needed.value)()
        count.value = needed.value
        result = restart_manager.RmGetList(
            session,
            ctypes.byref(needed),
            ctypes.byref(count),
            processes,
            ctypes.byref(reasons),
        )
        if result:
            raise OSError(result, "RmGetList failed")
        return [
            {
                "pid": processes[index].Process.dwProcessId,
                "name": processes[index].strAppName,
                "service": processes[index].strServiceShortName,
                "restartable": bool(processes[index].bRestartable),
            }
            for index in range(count.value)
        ]
    finally:
        restart_manager.RmEndSession(session)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(locking_processes(args.path.resolve()))


if __name__ == "__main__":
    main()
