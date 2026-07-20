from pathlib import Path
import json
import subprocess

import opencode_bridge
import requirements_checker


def test_official_downloads_use_allowlisted_https_origins_and_electron_shell():
    links = Path("frontend/official-downloads.js").read_text(encoding="utf-8")
    main_source = Path("frontend/main.js").read_text(encoding="utf-8")
    preload = Path("frontend/preload.js").read_text(encoding="utf-8")

    assert "https://www.python.org/downloads/windows/" in links
    assert "https://docs.python.org/3/library/ensurepip.html" in links
    assert "https://nodejs.org/en/download" in links
    assert "https://opencode.ai/docs/" in links
    assert "target.protocol !== 'https:'" in links
    assert "ALLOWED_DOWNLOAD_ORIGINS.has(target.origin)" in links
    assert "Object.prototype.hasOwnProperty.call(OFFICIAL_DOWNLOAD_URLS, downloadId)" in links
    assert "return '';" in links
    assert "shell.openExternal(target)" in main_source
    assert "isAllowedDownloadSender(event, mainWindow?.webContents, expectedRendererOrigin)" in main_source
    assert "ipcMain.handle('open-official-download'" in main_source
    assert "openOfficialDownload: (downloadId) => ipcRenderer.invoke('open-official-download', downloadId)" in preload
    assert "openExternal: (url)" not in preload


def test_frontend_passes_only_fixed_download_ids_not_urls():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")
    info = Path("frontend/src/components/InfoModal.jsx").read_text(encoding="utf-8")

    assert "openOfficialDownload('nodejs')" in source
    assert "openOfficialDownload('opencode')" in source
    assert "https://" not in source
    assert "Download Node.js" in source
    assert "Download OpenCode" in source
    assert "window.env.openOfficialDownload(componentId)" in info
    assert "/api/system/install/" not in info
    assert "https://" not in info


def test_official_download_lookup_and_sender_validation_execute_in_node():
    script = r"""
const links = require('./frontend/official-downloads');
const ids = ['python', 'pip', 'nodejs', 'npm', 'opencode', 'git', 'github-cli', 'vscode', 'pycharm', 'android-platform-tools'];
const mainFrame = { url: 'http://127.0.0.1:8080/info' };
const contents = { mainFrame };
const output = {
  urls: Object.fromEntries(ids.map(id => [id, links.officialDownloadUrl(id)])),
  unknown: links.officialDownloadUrl('https://evil.example'),
  nonString: links.officialDownloadUrl({ toString: () => 'nodejs' }),
  trusted: links.isAllowedDownloadSender({ sender: contents, senderFrame: mainFrame }, contents, 'http://127.0.0.1:8080'),
  wrongOrigin: links.isAllowedDownloadSender({ sender: contents, senderFrame: mainFrame }, contents, 'http://127.0.0.1:9999'),
  wrongFrame: links.isAllowedDownloadSender({ sender: contents, senderFrame: { url: mainFrame.url } }, contents, 'http://127.0.0.1:8080'),
};
console.log(JSON.stringify(output));
"""
    completed = subprocess.run(["node", "-e", script], cwd=Path.cwd(), capture_output=True, text=True, check=True)
    output = json.loads(completed.stdout)

    assert all(url.startswith("https://") for url in output["urls"].values())
    assert output["urls"]["pip"].startswith("https://docs.python.org/")
    assert output["urls"]["npm"].startswith("https://nodejs.org/")
    assert output["unknown"] == ""
    assert output["nonString"] == ""
    assert output["trusted"] is True
    assert output["wrongOrigin"] is False
    assert output["wrongFrame"] is False


def test_external_url_and_navigation_policies_reject_untrusted_inputs_in_node():
    script = r"""
const links = require('./frontend/official-downloads');
const candidates = [
  'unknown',
  'https://evil.example',
  'http://nodejs.org',
  'https://nodejs.org.evil.example',
  'file:///C:/Windows/System32',
  'javascript:alert(1)',
  'data:text/html,<h1>unsafe</h1>',
];
const output = {
  classifications: candidates.map(url => links.classifyWindowOpenUrl(url)),
  official: links.classifyWindowOpenUrl('https://nodejs.org/en/download'),
  loopback: links.classifyWindowOpenUrl('http://localhost:5173/preview'),
  sameOrigin: links.isAllowedStudioNavigation('http://127.0.0.1:8080/info', 'http://127.0.0.1:8080'),
  evilNavigation: links.isAllowedStudioNavigation('https://evil.example', 'http://127.0.0.1:8080'),
  deceptiveNavigation: links.isAllowedStudioNavigation('http://127.0.0.1.evil.example:8080', 'http://127.0.0.1:8080'),
};
console.log(JSON.stringify(output));
"""
    completed = subprocess.run(["node", "-e", script], cwd=Path.cwd(), capture_output=True, text=True, check=True)
    output = json.loads(completed.stdout)

    assert output["classifications"] == ["reject"] * 7
    assert output["official"] == "official"
    assert output["loopback"] == "loopback"
    assert output["sameOrigin"] is True
    assert output["evilNavigation"] is False
    assert output["deceptiveNavigation"] is False


def test_electron_renderer_has_deny_by_default_security_boundaries():
    main_source = Path("frontend/main.js").read_text(encoding="utf-8")
    preload = Path("frontend/preload.js").read_text(encoding="utf-8")
    app_source = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    preview = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    index = Path("frontend/index.html").read_text(encoding="utf-8")
    backend = Path("main.py").read_text(encoding="utf-8")

    assert "setWindowOpenHandler" in main_source
    assert "return { action: 'deny' }" in main_source
    assert "webContents.on('will-navigate'" in main_source
    assert "webContents.on('will-redirect'" in main_source
    assert "isAllowedStudioNavigation(details.url, expectedRendererOrigin)" in main_source
    assert "sandbox: true" in main_source
    assert "script-src 'self';" in main_source
    assert "object-src 'none'" in main_source
    assert "frame-ancestors 'none'" in main_source
    assert "child_process" not in preload
    assert "openInEditor" not in preload
    assert "revealInExplorer" not in preload
    assert "window.env.openInEditor" not in app_source
    assert "window.env.revealInExplorer" not in app_source
    assert "sandbox=\"allow-forms allow-scripts\"" in preview
    assert "0.0.0.0" not in preview
    assert ".endsWith('.local')" not in preview
    assert "<script>" not in index
    assert "window.BACKEND_PORT" not in backend


def test_electron_package_includes_official_download_allowlist():
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))

    assert "main.js" in package["build"]["files"]
    assert "preload.js" in package["build"]["files"]
    assert "official-downloads.js" in package["build"]["files"]


def test_detect_again_checks_node_npm_and_opencode_separately(monkeypatch):
    commands = []
    paths = {
        "node.exe": r"C:\Program Files\nodejs\node.exe",
        "npm.cmd": r"C:\Program Files\nodejs\npm.cmd",
        "opencode.cmd": r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd",
    }
    monkeypatch.setattr(opencode_bridge.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(opencode_bridge, "_run_capture", lambda command, timeout: commands.append((command, timeout)) or (0, "1.0.0", ""))

    result = opencode_bridge.get_opencode_onboarding_dependencies()

    assert all(result["components"][tool]["installed"] for tool in ("node", "npm", "opencode"))
    assert [command for command, _timeout in commands] == [
        [paths["node.exe"], "--version"],
        [paths["npm.cmd"], "--version"],
        [paths["opencode.cmd"], "--version"],
    ]


def test_standard_windows_path_detection_recommends_restart(monkeypatch):
    standard = r"C:\Program Files\nodejs\node.exe"
    monkeypatch.setattr(opencode_bridge.shutil, "which", lambda _name: None)
    monkeypatch.setattr(opencode_bridge, "_standard_windows_tool_paths", lambda _tool: [standard])
    monkeypatch.setattr(opencode_bridge.os.path, "isfile", lambda path: path == standard)
    monkeypatch.setattr(opencode_bridge, "_run_capture", lambda _command, timeout: (0, "v22.0.0", ""))

    result = opencode_bridge._detect_onboarding_tool("node")

    assert result["installed"] is True
    assert result["path_refresh_recommended"] is True
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")
    assert "restart Studio" in source
    assert "Detect Again" in source


def test_no_automatic_download_or_install_commands_remain():
    bridge = Path("opencode_bridge.py").read_text(encoding="utf-8").lower()
    frontend = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8").lower()

    assert '"npm", "install"' not in bridge
    assert "npm install -g" not in bridge
    assert "powershell" not in bridge
    assert "invoke-webrequest" not in bridge
    assert "/api/opencode/install" not in frontend


def test_requirement_registry_exposes_no_installer_callbacks_or_commands():
    source = Path("requirements_checker.py").read_text(encoding="utf-8")

    assert all("install" not in component for component in requirements_checker.COMPONENTS)
    assert all(component["can_auto_install"] is False for component in requirements_checker.COMPONENTS)
    assert requirements_checker._install_python_packages()[0] is False
    assert requirements_checker._install_node_packages()[0] is False
    assert '_run([sys.executable, "-m", "pip", "install"' not in source
    assert '_run(["npm", "install"]' not in source


def test_dependency_recovery_messages_match_real_buttons():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")

    assert "Node.js is required to install OpenCode with npm. Download the official Windows LTS installer, install it, restart Studio, and select Detect Again." in source
    assert "Download OpenCode from the official website or install it with npm. Then return to Studio and select Detect Again." in source
    assert "OpenCode Login" not in source
