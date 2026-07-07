// Clean fault-tolerant Electron entry point. Slashes fixed for root execution.
// Complies with strict code conventions. All comments are in English.

const { app, BrowserWindow } = require('electron');
const path = require('path');
const { exec } = require('child_process');
const fs = require('fs');
const http = require('http');

let mainWindow = null;
let pythonProcess = null;

function readPortFile() {
    const portFilePath = path.resolve(__dirname, '..', 'studio_port.txt');
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
    const startPort = parseInt(process.env.BACKEND_START_PORT || '8080', 10);
    const filePort = readPortFile();
    const candidates = [...new Set([startPort, filePort].filter(Boolean))];
    for (const port of candidates) {
        if (await checkBackend(port)) {
            console.log(`[Electron]: Reusing running backend on port ${port}`);
            return port;
        }
    }
    return null;
}

function startBackend() {
    console.log("[Electron]: Launching background Python backend from root directory...");

    const rootDir = path.resolve(__dirname, '..');
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

    console.log(`[Electron]: Root operational directory: ${rootDir}`);

    try {
        pythonProcess = exec(`${pythonCmd} main.py`, { cwd: rootDir }, (error, stdout, stderr) => {
            if (error) {
                console.error(`[Backend Process Error]: ${error.message}`);
                return;
            }
            if (stderr) {
                console.warn(`[Backend stderr]: ${stderr}`);
            }
            console.log(`[Backend stdout]: ${stdout}`);
        });
        console.log("[Electron]: Python backend process spawned successfully from root.");
    } catch (err) {
        console.error(`[Critical]: Failed to start Python backend: ${err.message}`);
    }
}

function waitForBackend(maxWaitMs) {
    return new Promise((resolve) => {
        let waited = 0;
        const check = async () => {
            const startPort = parseInt(process.env.BACKEND_START_PORT || '8080', 10);
            const filePort = readPortFile();
            const candidates = [...new Set([filePort, startPort].filter(Boolean))];
            for (const port of candidates) {
                if (await checkBackend(port)) {
                    console.log(`[Electron]: Backend is ready on port ${port}`);
                    resolve(port);
                    return;
                }
            }
            waited += 200;
            if (waited >= maxWaitMs) {
                console.warn(`[Electron]: Backend health check timed out, defaulting to ${filePort || startPort}`);
                resolve(filePort || startPort);
                return;
            }
            setTimeout(check, 200);
        };
        check();
    });
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

// Global lifecycle hooks handling application state changes safely
app.whenReady().then(async () => {
    let backendPort = await discoverRunningBackend();
    if (!backendPort) {
        startBackend();
        backendPort = await waitForBackend(15000);
    }
    await createWindow(backendPort);

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow(backendPort);
    });
});

app.on('window-all-closed', () => {
    console.log("[Electron]: App closing. Disposing allocated resources...");
    if (pythonProcess) {
        try {
            pythonProcess.kill();
            console.log("[Electron]: Python backend process terminated successfully.");
        } catch (e) {
            console.error(`[Error]: Failed killing backend worker: ${e.message}`);
        }
    }
    if (process.platform !== 'darwin') app.quit();
});
