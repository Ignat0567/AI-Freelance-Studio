import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

FRONTEND = Path("frontend")


def _text(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


def test_packaged_version_comes_from_electron_app_metadata():
    main = _text("main.js")

    assert "ipcMain.handle('get-app-version'" in main
    assert "return app.getVersion();" in main


def test_preload_exposes_a_read_only_narrow_version_api():
    preload = _text("preload.js")

    assert "Object.freeze({" in preload
    assert "getAppVersion: () => ipcRenderer.invoke('get-app-version')" in preload
    assert "ipcRenderer," not in preload
    assert "require:" not in preload


def test_renderer_uses_version_contract_without_legacy_hardcode():
    renderer = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (FRONTEND / "src").rglob("*.js*")
    )

    assert "v1.0" not in renderer
    assert "window.env?.getAppVersion" in renderer
    assert "Version unavailable" in renderer
    assert "Version ${appVersion}" in renderer


def test_browser_fallback_is_injected_from_package_metadata():
    vite = _text("vite.config.js")
    contract = _text("src/app-version.js")

    assert "packageMetadata.version" in vite
    assert "import.meta.env.VITE_APP_VERSION" in vite
    assert "return cleanVersion(buildVersion);" in contract


def test_package_lock_and_installer_metadata_use_package_version():
    package = json.loads(_text("package.json"))
    lock = json.loads(_text("package-lock.json"))

    assert lock["version"] == package["version"]
    assert lock["packages"][""]["version"] == package["version"]
    assert package["build"]["win"]["artifactName"] == "${productName}-Setup-${version}-win.${ext}"
    assert package["build"]["nsis"]["include"] == "build/installer.nsh"


def test_uninstall_only_removes_known_application_files_then_empty_root():
    nsis = _text("build/installer.nsh")

    assert "!macro customRemoveFiles" in nsis
    assert 'RMDir "$INSTDIR"' in nsis
    assert 'RMDir /r "$INSTDIR"' not in nsis
    assert "Install directory retained because it is not empty" in nsis
    assert "FindFirst" not in nsis


def test_uninstall_path_guard_rejects_dangerous_or_reparsed_targets():
    nsis = _text("build/installer.nsh")

    assert 'StrCmp $INSTDIR "" done' in nsis
    assert 'StrCpy $R3 $R1 3' in nsis
    assert 'findParentSeparator:' in nsis
    assert 'GetFullPathName $R4 "$PROGRAMFILES"' in nsis
    assert 'GetFullPathName $R4 "$PROFILE"' in nsis
    assert "InstallLocation" in nsis
    assert "GetFileAttributesW(w r10)i.r11" in nsis
    assert "0x400" in nsis


def test_uninstall_does_not_request_app_data_deletion():
    package = json.loads(_text("package.json"))
    nsis = _text("build/installer.nsh")

    assert package["build"]["nsis"].get("deleteAppDataOnUninstall") is not True
    assert "$APPDATA" not in nsis
    assert "$LOCALAPPDATA" not in nsis
