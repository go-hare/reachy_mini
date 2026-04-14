const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn, spawnSync } = require('child_process');

if (process.platform === 'win32') {
    try { require('child_process').execSync('chcp 65001', { stdio: 'ignore' }); } catch (_) {}
    process.stdout.setEncoding?.('utf8');
    process.stderr.setEncoding?.('utf8');
}

let mainWindow = null;
let backendProcess = null;
let backendReady = null;

const isDev = process.env.NODE_ENV === 'development';
const TITLE_BAR_BASE_HEIGHT = 44;

function testPythonCommand(command, prefixArgs = []) {
    const result = spawnSync(
        command,
        [...prefixArgs, '-c', 'import aiohttp,sys;print(sys.executable)'],
        { encoding: 'utf8' },
    );
    if (result.status !== 0) return null;
    const executable = (result.stdout || '').trim().split(/\r?\n/).filter(Boolean).pop() || command;
    return { command, prefixArgs, executable };
}

function resolvePythonExecutable() {
    const seen = new Set();
    const probes = [];
    const explicit = (process.env.PYTHON || '').trim();
    if (explicit) {
        probes.push({ command: explicit, prefixArgs: [] });
    }

    if (process.platform === 'win32') {
        const wherePython = spawnSync('where.exe', ['python'], { encoding: 'utf8' });
        if (wherePython.status === 0) {
            for (const line of (wherePython.stdout || '').split(/\r?\n/)) {
                const candidate = line.trim();
                if (candidate) probes.push({ command: candidate, prefixArgs: [] });
            }
        }
        probes.push({ command: 'py', prefixArgs: ['-3'] });
    }

    probes.push({ command: 'python', prefixArgs: [] });
    probes.push({ command: 'python3', prefixArgs: [] });

    for (const probe of probes) {
        const key = `${probe.command} ${probe.prefixArgs.join(' ')}`.trim();
        if (seen.has(key)) continue;
        seen.add(key);
        const resolved = testPythonCommand(probe.command, probe.prefixArgs);
        if (resolved) {
            return resolved;
        }
    }

    throw new Error('Unable to find a Python interpreter with aiohttp installed.');
}

function startBackend() {
    if (backendReady) return backendReady;

    backendReady = new Promise((resolve, reject) => {
        const python = resolvePythonExecutable();
        const runnerPath = path.join(__dirname, '..', '..', 'frontend_host.py');
        const runnerCwd = path.dirname(runnerPath);
        const forwardedArgs = [...python.prefixArgs, runnerPath];
        const optionMap = [
            ['CCMINI_PROVIDER', '--provider'],
            ['CCMINI_MODEL', '--model'],
            ['CCMINI_API_KEY', '--api-key'],
            ['CCMINI_BASE_URL', '--base-url'],
            ['CCMINI_SYSTEM_PROMPT', '--system-prompt'],
        ];
        for (const [envName, flag] of optionMap) {
            const value = (process.env[envName] || '').trim();
            if (value) {
                forwardedArgs.push(flag, value);
            }
        }
        const subprocess = spawn(
            python.command,
            forwardedArgs,
            {
                cwd: runnerCwd,
                env: {
                    ...process.env,
                    PYTHONUNBUFFERED: '1',
                    PYTHONUTF8: '1',
                    PYTHONIOENCODING: 'utf-8',
                },
                stdio: ['ignore', 'pipe', 'inherit'],
            },
        );
        backendProcess = subprocess;

        let buffer = '';
        let settled = false;

        const cleanup = () => {
            subprocess.stdout.off('data', onData);
            subprocess.off('exit', onExit);
        };

        const onExit = (code, signal) => {
            if (settled) return;
            settled = true;
            cleanup();
            reject(new Error(`ccmini backend exited early (code=${code}, signal=${signal})`));
        };

        const onData = (chunk) => {
            buffer += chunk.toString('utf8');
            const newlineIndex = buffer.indexOf('\n');
            if (newlineIndex < 0) return;
            const line = buffer.slice(0, newlineIndex).trim();
            if (!line) return;
            try {
                const parsed = JSON.parse(line);
                settled = true;
                cleanup();
                resolve(parsed);
            } catch (error) {
                settled = true;
                cleanup();
                reject(new Error(`Invalid ccmini backend ready payload: ${error.message}`));
            }
        };

        subprocess.stdout.on('data', onData);
        subprocess.once('exit', onExit);
    });

    return backendReady;
}

async function stopBackend() {
    if (!backendProcess || backendProcess.exitCode !== null) return;
    backendProcess.kill('SIGTERM');
    await Promise.race([
        new Promise((resolve) => backendProcess.once('exit', resolve)),
        new Promise((resolve) => setTimeout(resolve, 1000)),
    ]);
    if (backendProcess.exitCode === null) {
        backendProcess.kill('SIGKILL');
    }
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1150,
        height: 700,
        minWidth: 800,
        minHeight: 600,
        webPreferences: {
            preload: path.join(__dirname, 'preload.cjs'),
            contextIsolation: true,
            nodeIntegration: false,
        },
        ...(process.platform === 'darwin'
            ? { titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 12, y: 12 } }
            : {
                titleBarStyle: 'hidden',
                titleBarOverlay: {
                    color: '#00000000',
                    symbolColor: '#808080',
                    height: TITLE_BAR_BASE_HEIGHT,
                },
            }),
        icon: path.join(__dirname, '..', 'public', 'favicon.png'),
        backgroundColor: '#F8F8F6',
        show: false,
    });

    mainWindow.once('ready-to-show', () => {
        mainWindow.webContents.setZoomFactor(1.0);
        mainWindow.show();
    });

    const applyZoom = (factor) => {
        const wc = mainWindow.webContents;
        wc.setZoomFactor(factor);
        if (process.platform !== 'darwin') {
            try {
                mainWindow.setTitleBarOverlay({
                    color: '#00000000',
                    symbolColor: '#808080',
                    height: Math.round(TITLE_BAR_BASE_HEIGHT * factor),
                });
            } catch (_) {}
        }
        wc.send('zoom-changed', factor);
    };

    mainWindow.webContents.on('before-input-event', (event, input) => {
        if (!input.control && !input.meta) return;
        const current = mainWindow.webContents.getZoomFactor();
        if (input.key === '=' || input.key === '+') {
            event.preventDefault();
            applyZoom(Math.min(+(current + 0.1).toFixed(1), 2.0));
        } else if (input.key === '-') {
            event.preventDefault();
            applyZoom(Math.max(+(current - 0.1).toFixed(1), 0.5));
        } else if (input.key === '0') {
            event.preventDefault();
            applyZoom(1.0);
        }
    });

    if (isDev) {
        mainWindow.loadURL('http://localhost:3000');
    } else {
        mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
    }

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (url.startsWith('http://') || url.startsWith('https://')) {
            shell.openExternal(url);
        }
        return { action: 'deny' };
    });

    mainWindow.webContents.on('will-navigate', (event, url) => {
        if (url.startsWith('file://') || url.startsWith('http://localhost')) return;
        event.preventDefault();
        shell.openExternal(url);
    });

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

app.whenReady().then(async () => {
    let backendInfo = null;
    try {
        backendInfo = await startBackend();
    } catch (error) {
        dialog.showErrorBox('ccmini backend failed to start', error instanceof Error ? error.message : String(error));
        app.exit(1);
        return;
    }

    process.env.CCMINI_API_BASE = `${String(backendInfo.serverUrl || 'http://127.0.0.1:7779').replace(/\/$/, '')}/api`;

    createWindow();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('window-all-closed', async () => {
    if (process.platform !== 'darwin') {
        await stopBackend();
        app.quit();
    }
});

app.on('before-quit', async () => {
    await stopBackend();
});

ipcMain.handle('get-app-path', () => app.getPath('userData'));
ipcMain.handle('get-platform', () => process.platform);
ipcMain.handle('install-update', () => false);
ipcMain.handle('open-external', (_, url) => shell.openExternal(url));
ipcMain.handle('resize-window', (_, width, height) => {
    if (mainWindow) {
        mainWindow.setSize(width, height);
        mainWindow.center();
    }
});
ipcMain.handle('select-directory', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openDirectory'],
    });
    if (result.canceled) return null;
    return result.filePaths[0];
});

const recentlyOpenedFolders = new Map();
ipcMain.handle('show-item-in-folder', (_, filePath) => {
    if (!filePath || !fs.existsSync(filePath)) return false;
    const folder = path.dirname(filePath);
    const now = Date.now();
    const lastOpened = recentlyOpenedFolders.get(folder);
    if (lastOpened && now - lastOpened < 2000) return true;
    recentlyOpenedFolders.set(folder, now);
    shell.showItemInFolder(filePath);
    return true;
});

const recentlyOpenedDirs = new Map();
ipcMain.handle('open-folder', (_, folderPath) => {
    if (!folderPath || !fs.existsSync(folderPath)) return false;
    const now = Date.now();
    const lastOpened = recentlyOpenedDirs.get(folderPath);
    if (lastOpened && now - lastOpened < 2000) return true;
    recentlyOpenedDirs.set(folderPath, now);
    shell.openPath(folderPath);
    return true;
});

ipcMain.handle('export-workspace', async (_, workspaceId, contextMarkdown, defaultFilename) => {
    const result = await dialog.showSaveDialog(mainWindow, {
        title: '导出对话',
        defaultPath: defaultFilename || `conversation-${workspaceId}.md`,
        filters: [
            { name: 'Markdown Files', extensions: ['md'] },
            { name: 'All Files', extensions: ['*'] },
        ],
    });
    if (result.canceled || !result.filePath) {
        return { success: false, reason: 'canceled' };
    }
    fs.writeFileSync(result.filePath, contextMarkdown || '', 'utf-8');
    return { success: true, path: result.filePath, size: Buffer.byteLength(contextMarkdown || '', 'utf8') };
});
