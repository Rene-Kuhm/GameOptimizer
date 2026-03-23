# Game Optimizer (Electron + Python)

Starter project for a Windows desktop game optimizer using:

- Electron for desktop UI
- FastAPI backend for local API + WebSocket streaming
- `psutil`, `wmi`, `winreg`, `pywin32` for Windows metrics and optimization actions

## Folder structure

```text
GameOptimizer/
  electron/
    main.js
    preload.js
    index.html
    styles.css
    renderer.js
  backend/
    requirements.txt
    app/
      __init__.py
      main.py
      models.py
      system.py
      discovery.py
      optimizer.py
      watcher.py
  package.json
  .gitignore
```

## Prerequisites (Windows)

- Python 3.10+
- Node.js 18+
- PowerShell or CMD

## Setup

1. Install Python dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
```

2. Install Electron dependency:

```bash
npm install
```

## Run locally

```bash
npm start
```

Electron `main.js` starts the backend automatically with Uvicorn at `http://127.0.0.1:8765`.

## API endpoints

- `GET /health`
- `GET /system/metrics`
- `GET /hardware`
- `GET /games`
- `POST /optimize/apply`
- `WS /ws/metrics`

## Notes on privileges and safety

- Setting process priorities may require Administrator rights for some processes.
- If access is denied, backend returns graceful error details and keeps running.
- GPU info is best-effort through WMI (`Win32_VideoController`) and can be partial/missing on some systems.
- Game discovery is best-effort and depends on installed launcher metadata and manifest consistency.
