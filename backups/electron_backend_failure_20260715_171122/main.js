// Clean fault-tolerant Electron entry point. Slashes fixed for root execution.
// Complies with strict code conventions. All comments are in English.

const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');

let mainWindow = null;
let backendProcess = null;
let backendOwnedByElectron = false;
let backendStarting = false;

function appendBoundedLog(prefix, chunk) {
    const text = String(chunk || '')
        .replace(/((api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&]+/gi, '$1<redacted>')
        .trim();
    if (!text) return;
    console.log(`${prefix}: ${text.slice(-2000)}`);
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
    const spec = backendLaunchSpec();
    const runtimeDir = runtimeDirectory();
    const frontendDir = app.isPackaged ? path.join(process.resourcesPath, 'frontend-dist') : path.join(path.resolve(__dirname, '..'), 'frontend', 'dist');

    if (!spec.command || !path.isAbsolute(spec.command) || !fs.existsSync(spec.command)) {
        console.error(`[Critical]: Backend executable not found: ${spec.command || '<unresolved>'}`);
        backendStarting = false;
        return false;
    }
    if (!fs.existsSync(spec.cwd)) {
        console.error(`[Critical]: Backend working directory not found: ${spec.cwd}`);
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
                FREELANCERSTUDIO_FRONTEND_DIR: frontendDir
            }
        });

        backendOwnedByElectron = true;
        backendProcess.stdout.on('data', chunk => appendBoundedLog('[Backend stdout]', chunk));
        backendProcess.stderr.on('data', chunk => appendBoundedLog('[Backend stderr]', chunk));
        backendProcess.on('error', err => {
            console.error(`[Backend Process Error]: ${err.message}`);
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

    mainWindow = new BrowserWindow({
        width: 1280,
        height: 720,
        minWidth: 1024,
        minHeight: 576,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        },
        backgroundColor: '#0f172a',
        title: "AI Freelance Studio"
    });

    // Set Content-Security-Policy to suppress Electron security warning
    mainWindow.webContents.session.webRequest.onHeadersReceived((details, callback) => {
        callback({
            responseHeaders: {
                ...details.responseHeaders,
                'content-security-policy': ["default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; img-src 'self' data:;"]
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
        console.error('[Electron]: Backend startup failed; window will not be created.');
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
