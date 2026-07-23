// Clean fault-tolerant Electron entry point. Slashes fixed for root execution.
// Complies with strict code conventions. All comments are in English.

const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
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
let backendToken = '';
let backendLaunchId = '';
let backendDescriptor = null;
let backendHost = '127.0.0.1';
let requestAuthorizationConfigured = false;

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

function checkBackend(host, port, launchId, instanceId, timeoutMs = 800) {
    return new Promise((resolve) => {
        const challenge = crypto.randomBytes(32).toString('base64url');
        const req = http.get({
            hostname: host,
            port,
            path: '/health/owner',
            timeout: timeoutMs,
            headers: {
                'X-FreelancerStudio-Challenge': challenge,
            },
        }, (res) => {
            let body = '';
            res.setEncoding('utf8');
            res.on('data', chunk => {
                if (body.length < 4096) body += chunk;
            });
            res.on('end', () => {
                try {
                    const payload = JSON.parse(body);
                    const expectedProof = crypto.createHmac('sha256', backendToken)
                        .update(`${challenge}:${launchId}:${instanceId}:${port}`)
                        .digest('base64url');
                    resolve(
                        res.statusCode === 200
                        && payload.status === 'ok'
                        && payload.service === 'FreelancerStudio'
                        && payload.port === port
                        && payload.launch_id === launchId
                        && payload.instance_id === instanceId
                        && payload.proof === expectedProof
                    );
                } catch (error) {
                    resolve(false);
                }
            });
        });
        req.on('timeout', () => {
            req.destroy();
            resolve(false);
        });
        req.on('error', () => resolve(false));
    });
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
            const descriptor = backendDescriptor;
            if (descriptor) {
                if (await checkBackend(descriptor.connect_host, descriptor.port, descriptor.launch_id, descriptor.instance_id)) {
                    backendHost = descriptor.connect_host;
                    console.log(`[Electron]: Backend is ready on port ${descriptor.port}`);
                    resolve(descriptor.port);
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
    backendToken = crypto.randomBytes(32).toString('base64url');
    backendLaunchId = crypto.randomUUID();
    backendDescriptor = null;

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
        const stalePortFile = path.join(runtimeDir, 'studio_port.txt');
        if (fs.existsSync(stalePortFile)) fs.unlinkSync(stalePortFile);
        backendProcess = spawn(spec.command, spec.args, {
            cwd: spec.cwd,
            shell: false,
            windowsHide: true,
            stdio: ['pipe', 'pipe', 'pipe', 'pipe'],
            env: {
                ...process.env,
                FREELANCERSTUDIO_RUNTIME_DIR: runtimeDir,
                FREELANCERSTUDIO_USER_DATA: runtimeDir,
                FREELANCERSTUDIO_FRONTEND_DIR: frontendDir,
                FREELANCERSTUDIO_AUTH_STDIN: '1',
                FREELANCERSTUDIO_CONTROL_FD: '3',
                ...(app.isPackaged ? {
                    SSL_CERT_FILE: path.join(spec.cwd, '_internal', 'certifi', 'cacert.bundle'),
                    REQUESTS_CA_BUNDLE: path.join(spec.cwd, '_internal', 'certifi', 'cacert.bundle')
                } : {})
            }
        });

        backendOwnedByElectron = true;
        backendProcess.stdin.end(JSON.stringify({ token: backendToken, launch_id: backendLaunchId }));
        let controlOutput = '';
        backendProcess.stdio[3].setEncoding('utf8');
        backendProcess.stdio[3].on('data', chunk => {
            controlOutput = `${controlOutput}${chunk}`.slice(0, 4096);
            const newline = controlOutput.indexOf('\n');
            if (newline < 0 || backendDescriptor) return;
            try {
                const descriptor = JSON.parse(controlOutput.slice(0, newline));
                if (
                    descriptor.launch_id === backendLaunchId
                    && typeof descriptor.instance_id === 'string'
                    && Number.isInteger(descriptor.port)
                    && descriptor.port >= 1
                    && descriptor.port <= 65535
                    && ['127.0.0.1', '::1'].includes(descriptor.connect_host)
                ) {
                    backendDescriptor = descriptor;
                }
            } catch (error) {
                backendStartupFailure = 'launch_error';
            }
        });
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
    backendToken = '';
    backendLaunchId = '';
    backendDescriptor = null;
    backendHost = '127.0.0.1';
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
    const rendererHost = backendHost.includes(':') ? `[${backendHost}]` : backendHost;
    expectedRendererOrigin = `http://${rendererHost}:${backendPort}`;

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

    if (!requestAuthorizationConfigured) {
        mainWindow.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
            const requestHeaders = { ...details.requestHeaders };
            for (const name of Object.keys(requestHeaders)) {
                if (name.toLowerCase() === 'x-freelancerstudio-token') delete requestHeaders[name];
            }
            let target;
            try {
                target = new URL(details.url);
            } catch (error) {
                callback({ requestHeaders });
                return;
            }
            const targetBackendOrigin = target.protocol === 'ws:'
                ? `http://${target.host}`
                : target.origin;
            const trustedTarget = details.webContentsId === mainWindow?.webContents.id
                && targetBackendOrigin === expectedRendererOrigin
                && (target.pathname.startsWith('/api/') || target.pathname.startsWith('/ws/'));
            if (!trustedTarget) {
                callback({ requestHeaders });
                return;
            }
            requestHeaders['X-FreelancerStudio-Token'] = backendToken;
            if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(details.method)) {
                requestHeaders.Origin = expectedRendererOrigin;
                if (!requestHeaders['Content-Type'] && !requestHeaders['content-type']) {
                    requestHeaders['Content-Type'] = 'application/json';
                }
            }
            callback({ requestHeaders });
        });
        requestAuthorizationConfigured = true;
    }

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
                'content-security-policy': [`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${expectedRendererOrigin.replace('http:', 'ws:')}; img-src 'self' data: blob:; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self';`]
            }
        });
    });

    mainWindow.loadURL(`${expectedRendererOrigin}/`);
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
    const started = startBackend();
    const backendPort = started ? await waitForBackend(15000, backendProcess) : null;
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
