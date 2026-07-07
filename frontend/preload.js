const { contextBridge, shell } = require('electron');
const { spawn } = require('child_process');

contextBridge.exposeInMainWorld('env', {
    openExternal: (url) => shell.openExternal(url),
    openInEditor: (editor, projectPath) => {
        return new Promise((resolve, reject) => {
            const useShell = process.platform === 'win32' && /\.(cmd|bat)$/i.test(editor || '');
            const child = spawn(editor, [projectPath], {
                detached: true,
                stdio: 'ignore',
                shell: useShell,
            });
            child.once('error', (err) => reject(err.message));
            child.once('spawn', () => {
                child.unref();
                resolve('opened');
            });
        });
    },
    revealInExplorer: (projectPath) => shell.openPath(projectPath),
});
