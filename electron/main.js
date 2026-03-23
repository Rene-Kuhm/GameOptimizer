const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = 8765;
const BACKEND_BASE_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

let backendProcess = null;

function backendCommand() {
  return process.env.GO_BACKEND_PYTHON || 'python';
}

function startBackend() {
  if (backendProcess) {
    return;
  }

  const backendDir = path.resolve(__dirname, '..', 'backend');
  const args = ['-m', 'uvicorn', 'app.main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)];

  backendProcess = spawn(backendCommand(), args, {
    cwd: backendDir,
    windowsHide: true,
    stdio: 'pipe',
  });

  backendProcess.stdout.on('data', (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });

  backendProcess.stderr.on('data', (data) => {
    console.error(`[backend:error] ${data.toString().trim()}`);
  });

  backendProcess.on('close', (code) => {
    console.log(`[backend] exited with code ${code}`);
    backendProcess = null;
  });
}

function stopBackend() {
  if (!backendProcess) {
    return;
  }

  backendProcess.kill();
  backendProcess = null;
}

function waitForBackend(retries = 25, delayMs = 400) {
  return new Promise((resolve, reject) => {
    const tryHealth = (remaining) => {
      const req = http.get(`${BACKEND_BASE_URL}/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          resolve();
          return;
        }

        if (remaining <= 0) {
          reject(new Error('Backend failed health check'));
          return;
        }

        setTimeout(() => tryHealth(remaining - 1), delayMs);
      });

      req.on('error', () => {
        if (remaining <= 0) {
          reject(new Error('Backend not reachable'));
          return;
        }
        setTimeout(() => tryHealth(remaining - 1), delayMs);
      });
    };

    tryHealth(retries);
  });
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: '#080d14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await win.loadFile(path.join(__dirname, 'index.html'));
}

app.whenReady().then(async () => {
  startBackend();

  try {
    await waitForBackend();
  } catch (error) {
    console.error(error);
  }

  await createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopBackend();
});
