const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('env', Object.freeze({
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
    openOfficialDownload: (downloadId) => ipcRenderer.invoke('open-official-download', downloadId),
}));
