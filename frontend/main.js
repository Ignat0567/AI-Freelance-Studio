// Clean fault-tolerant Electron entry point. Slashes fixed for root execution.
// Complies with strict code conventions. All comments are in English.

const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const {
    officialDownloadUrl,
    classifyWindowOpenUrl,
    isAllowedStudioNavigation,
    isAllowedDownloadSender,
} = require('./official-downloads');

let mainWindow = null;
let backendProcess = null;
let backendOwnedByElectron = false;
let backendStarting = false;
let backendStartupFailure = '';
let backendStartupOutput = '';
let expectedRendererOrigin = '';

ipcMain.handle('open-official-download', async (event, downloadId) => {
    if (!isAllowedDownloadSender(event, mainWindow?.webContents, expectedRendererOrigin)) {
        return { status: 'rejected', message: 'This request did not come from the trusted Studio window.' };
    }
    const target = officialDownloadUrl(downloadId);
    if (!target) return { status: 'rejected', message: 'This download destination is not allowed.' };
    try {
        await shell.openExternal(target);
        return { status: 'opened', downloadId };
    } catch (error) {
        console.error(`[Electron]: Failed to open official download page: ${error.message}`);
        return { status: 'error', message: 'The system browser could not be opened.' };
    }
});

ipcMain.handle('get-app-version', (event) => {
    if (!isAllowedDownloadSender(event, mainWindow?.webContents, expectedRendererOrigin)) return '';
    return app.getVersion();
});

function appendBoundedLog(prefix, chunk) {
    const text = String(chunk || '')
        .replace(/((api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&]+/gi, '$1<redacted>')
        .trim();
    if (!text) return '';
    const boundedText = text.slice(-2000);
    console.log(`${prefix}: ${boundedText}`);
    return boundedText;
}

function showBackendStartupFailure() {
    const messages = {
        executable_missing: 'The bundled backend files are missing or damaged. Reinstall AI Freelance Studio, then try again.',
        exited_early: 'The local backend stopped while starting. Restart AI Freelance Studio and try again.',
        health_timeout: 'The local backend did not become ready in time. Restart AI Freelance Studio and try again.',
        port_in_use: 'The local backend could not find an available local network port. Close other copies of AI Freelance Studio and try again.',
        launch_error: 'The local backend could not be started. Restart AI Freelance Studio and try again.'
    };
    const message = messages[backendStartupFailure] || messages.launch_error;
    const logPath = path.join(runtimeDirectory(), 'backend-startup.log');
    try {
        fs.mkdirSync(runtimeDirectory(), { recursive: true });
        fs.appendFileSync(logPath, `${new Date().toISOString()} failure=${backendStartupFailure || 'launch_error'}\n${backendStartupOutput}\n`, 'utf8');
    } catch (err) {
        console.error(`[Electron]: Failed to write backend startup log: ${err.message}`);
    }
    console.error(`[Electron]: Backend startup failed: ${backendStartupFailure || 'launch_error'}`);
    dialog.showErrorBox('AI Freelance Studio could not start', message);
}

function resolveExecutable(name) {
    const finder = process.platform === 'win32' ? 'where.exe' : 'which';
    const result = spawnSync(finder, [name], { shell: false, windowsHide: true, encoding: 'utf8' });
    if (result.status !== 0 || !result.stdout) return '';
    const candidate = result.stdout.split(/\r?\n/).map(line => line.trim()).find(Boolean);
    return candidate && path.isAbsolute(candidate) && fs.existsSync(candidate) ? candidate : '';
}

function runtimeDirectory() {
    return app.isPackaged ? app.getPath('userData') : path.resolve(__dirname, '..');
}

function readPortFile() {
    const portFilePath = path.join(runtimeDirectory(), 'studio_port.txt');
    if (!fs.existsSync(portFilePath)) return null;
    try {
        const port = parseInt(fs.readFileSync(portFilePath, 'utf8').trim(), 10);
        return Number.isInteger(port) && port > 0 ? port : null;
    } catch (e) {
        return null;
    }
}

function checkBackend(port, timeoutMs = 800) {
    return new Promise((resolve) => {
        const req = http.get({ hostname: '127.0.0.1', port, path: '/health', timeout: timeoutMs }, (res) => {
            res.resume();
            resolve(res.statusCode >= 200 && res.statusCode < 500);
        });
        req.on('timeout', () => {
            req.destroy();
            resolve(false);
        });
        req.on('error', () => resolve(false));
    });
}

async function discoverRunningBackend() {
    const filePort = readPortFile();
    if (filePort && await checkBackend(filePort)) {
        console.log(`[Electron]: Reusing owned backend on port ${filePort}`);
        return filePort;
    }
    return null;
}

function waitForBackend(maxWaitMs, processRef = null) {
    return new Promise((resolve) => {
        let waited = 0;
        const check = async () => {
            if (processRef && processRef.exitCode !== null) {
                console.error(`[Electron]: Backend exited before health check passed: ${processRef.exitCode}`);
                backendStartupFailure = backendStartupOutput.includes('Could not find any available network ports') ? 'port_in_use' : 'exited_early';
                resolve(null);
                return;
            }
            const filePort = readPortFile();
            if (filePort) {
                if (await checkBackend(filePort)) {
                    console.log(`[Electron]: Backend is ready on port ${filePort}`);
                    resolve(filePort);
                    return;
                }
            }
            waited += 200;
            if (waited >= maxWaitMs) {
                console.error(`[Electron]: Backend health check timed out after ${maxWaitMs}ms.`);
                if (!backendStartupFailure) backendStartupFailure = 'health_timeout';
                resolve(null);
                return;
            }
            setTimeout(check, 200);
        }
        check();
    });
}

function resolvePythonCommand(rootDir) {
    const configuredPython = process.env.PYTHON_PATH || process.env.PYTHON_EXECUTABLE || '';
    if (configuredPython && path.isAbsolute(configuredPython) && fs.existsSync(configuredPython)) return configuredPython;
    const venvPython = process.platform === 'win32'
        ? path.join(rootDir, '.venv', 'Scripts', 'python.exe')
        : path.join(rootDir, '.venv', 'bin', 'python');
    if (fs.existsSync(venvPython)) return venvPython;
    const resolved = resolveExecutable(process.platform === 'win32' ? 'python.exe' : 'python3');
    if (resolved) return resolved;
    return '';
}

function backendLaunchSpec() {
    const rootDir = path.resolve(__dirname, '..');
    if (app.isPackaged) {
        const sidecarDir = path.join(process.resourcesPath, 'backend', 'freelancerstudio-backend');
        const executable = path.join(sidecarDir, process.platform === 'win32' ? 'freelancerstudio-backend.exe' : 'freelancerstudio-backend');
        return { command: executable, args: [], cwd: sidecarDir };
    }
    const mainScript = path.join(rootDir, 'main.py');
    const pythonCmd = resolvePythonCommand(rootDir);
    return { command: pythonCmd, args: [mainScript], cwd: rootDir };
}

function startBackend() {
    if (backendProcess || backendStarting) {
        console.error('[Electron]: Refusing to start a second backend process.');
        return false;
    }
    backendStarting = true;
    backendStartupFailure = '';
    backendStartupOutput = '';
    const spec = backendLaunchSpec();
    const runtimeDir = runtimeDirectory();
    const frontendDir = app.isPackaged ? path.join(process.resourcesPath, 'frontend-dist') : path.join(path.resolve(__dirname, '..'), 'frontend', 'dist');

    if (!spec.command || !path.isAbsolute(spec.command) || !fs.existsSync(spec.command)) {
        console.error(`[Critical]: Backend executable not found: ${spec.command || '<unresolved>'}`);
        backendStartupFailure = 'executable_missing';
        backendStartupOutput = 'Backend executable not found.';
        backendStarting = false;
        return false;
    }
    if (!fs.existsSync(spec.cwd)) {
        console.error(`[Critical]: Backend working directory not found: ${spec.cwd}`);
        backendStartupFailure = 'launch_error';
        backendStartupOutput = 'Backend working directory not found.';
        backendStarting = false;
        return false;
    }

    try {
        backendProcess = spawn(spec.command, spec.args, {
            cwd: spec.cwd,
            shell: false,
            windowsHide: true,
            stdio: ['ignore', 'pipe', 'pipe'],
            env: {
                ...process.env,
                FREELANCERSTUDIO_RUNTIME_DIR: runtimeDir,
                FREELANCERSTUDIO_USER_DATA: runtimeDir,
                FREELANCERSTUDIO_FRONTEND_DIR: frontendDir,
                ...(app.isPackaged ? {
                    SSL_CERT_FILE: path.join(spec.cwd, '_internal', 'certifi', 'cacert.bundle'),
                    REQUESTS_CA_BUNDLE: path.join(spec.cwd, '_internal', 'certifi', 'cacert.bundle')
                } : {})
            }
        });

        backendOwnedByElectron = true;
        backendProcess.stdout.on('data', chunk => appendBoundedLog('[Backend stdout]', chunk));
        backendProcess.stderr.on('data', chunk => {
            backendStartupOutput = `${backendStartupOutput}\n${appendBoundedLog('[Backend stderr]', chunk)}`.slice(-4000);
        });
        backendProcess.on('error', err => {
            console.error(`[Backend Process Error]: ${err.message}`);
            backendStartupFailure = 'launch_error';
            backendStartupOutput = `Backend process error: ${appendBoundedLog('[Backend Process Error]', err.message)}`;
        });
        backendProcess.on('exit', (code, signal) => {
            console.log(`[Backend Process Exit]: code=${code ?? 'null'} signal=${signal ?? 'null'}`);
            backendProcess = null;
            backendOwnedByElectron = false;
            backendStarting = false;
        });
        console.log(`[Electron]: Backend process spawned successfully: ${backendProcess.pid}`);
        return true;
    } catch (err) {
        console.error(`[Critical]: Failed to start backend: ${err.message}`);
        backendStartupFailure = 'launch_error';
        backendStartupOutput = `Failed to start backend: ${appendBoundedLog('[Critical]', err.message)}`;
        backendProcess = null;
        backendOwnedByElectron = false;
        backendStarting = false;
        return false;
    }
}

function stopBackend() {
    if (!backendProcess || !backendOwnedByElectron) return;
    const proc = backendProcess;
    backendProcess = null;
    backendOwnedByElectron = false;
    backendStarting = false;
    try {
        if (process.platform === 'win32') {
            spawnSync('taskkill.exe', ['/PID', String(proc.pid), '/T', '/F'], { shell: false, windowsHide: true });
        } else {
            proc.kill('SIGTERM');
        }
        console.log("[Electron]: Backend process termination requested.");
    } catch (e) {
        console.error(`[Error]: Failed killing backend worker: ${e.message}`);
    }
}

async function createWindow(backendPort) {
    console.log("[Electron]: Creating main application viewport...");
    console.log(`[Electron]: Using backend port: ${backendPort}`);
    expectedRendererOrigin = `http://127.0.0.1:${backendPort}`;

    mainWindow = new BrowserWindow({
        width: 1280,
        height: 720,
        minWidth: 1024,
        minHeight: 576,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            sandbox: true,
            preload: path.join(__dirname, 'preload.js')
        },
        backgroundColor: '#0f172a',
        title: "AI Freelance Studio",
        icon: path.join(__dirname, 'build', 'icon.ico')
    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (classifyWindowOpenUrl(url) !== 'reject') {
            shell.openExternal(url).catch(error => {
                console.error(`[Electron]: Failed to open trusted external URL: ${error.message}`);
            });
        }
        return { action: 'deny' };
    });

    const preventUntrustedNavigation = (event, url) => {
        if (!isAllowedStudioNavigation(url, expectedRendererOrigin)) event.preventDefault();
    };
    mainWindow.webContents.on('will-navigate', preventUntrustedNavigation);
    mainWindow.webContents.on('will-redirect', preventUntrustedNavigation);

    mainWindow.webContents.session.webRequest.onHeadersReceived((details, callback) => {
        if (!isAllowedStudioNavigation(details.url, expectedRendererOrigin)) {
            callback({ responseHeaders: details.responseHeaders });
            return;
        }
        callback({
            responseHeaders: {
                ...details.responseHeaders,
                'content-security-policy': ["default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; img-src 'self' data: blob:; frame-src 'self' http://localhost:* http://127.0.0.1:*; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self';"]
            }
        });
    });

    mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
    if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
        mainWindow.webContents.openDevTools();
    }

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

if (!app.requestSingleInstanceLock()) {
    app.quit();
} else {
// Global lifecycle hooks handling application state changes safely
app.whenReady().then(async () => {
    let backendPort = await discoverRunningBackend();
    if (!backendPort) {
        const started = startBackend();
        backendPort = started ? await waitForBackend(15000, backendProcess) : null;
    }
    if (!backendPort) {
        showBackendStartupFailure();
        stopBackend();
        app.quit();
        return;
    }
    await createWindow(backendPort);

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow(backendPort);
    });
});

app.on('window-all-closed', () => {
    console.log("[Electron]: App closing. Disposing allocated resources...");
    stopBackend();
    if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
    stopBackend();
});

app.on('second-instance', () => {
    if (mainWindow) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.focus();
    }
});
}
