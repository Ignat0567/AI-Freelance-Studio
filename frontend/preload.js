const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('env', Object.freeze({
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
    openOfficialDownload: (downloadId) => ipcRenderer.invoke('open-official-download', downloadId),
    sandboxTestLab: Object.freeze({
        getCapabilities: () => ipcRenderer.invoke('sandbox-test-lab-capabilities'),
        launchRun: (operation, idempotencyKey) => ipcRenderer.invoke('sandbox-test-lab-launch', operation, idempotencyKey),
        getRun: (runId) => ipcRenderer.invoke('sandbox-test-lab-status', runId),
        cancelRun: (runId) => ipcRenderer.invoke('sandbox-test-lab-cancel', runId),
    }),
}));
