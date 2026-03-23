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

## Tray / background mode (Windows)

- Closing the window with the `X` now hides the app to the system tray instead of quitting.
- While hidden, the backend keeps running so automatic optimization continues in background.
- Tray menu includes `Abrir` (show/focus window) and `Salir` (quit app completely).
- On first minimize to tray, Windows shows a one-time balloon tip explaining background mode.

## Troubleshooting / recent hotfix

- If installation fails on `pywin32==306`, use the current pinned selector in `backend/requirements.txt`: `pywin32>=311; platform_system == "Windows"`.
- If you see WebSocket `Unsupported upgrade request`, use `websockets==12.0`.
- After updating dependencies, reinstall inside `.venv` and relaunch the app:

```bash
.venv\Scripts\activate
pip install -r backend/requirements.txt
npm start
```

## API endpoints

- `GET /health`
- `GET /system/metrics`
- `GET /hardware`
- `GET /games`
- `POST /optimize/apply`
- `WS /ws/metrics`

## Game discovery providers (Windows, best-effort)

The backend now uses a provider-based discovery pipeline and merges duplicates by normalized title + executable path.

- Steam manifests (`appmanifest_*.acf` + library folders)
- Epic Games manifests (`ProgramData/Epic/.../*.item`)
- GOG Galaxy (manifest-like JSON where present + uninstall registry fallback)
- Battle.net / Blizzard (registry install metadata, icon path hints)
- Ubisoft Connect (registry install metadata)
- EA App / Origin (registry install metadata)
- Xbox PC games where detectable (`C:\XboxGames` and `Program Files\XboxGames`, best-effort)
- Generic filesystem scan provider for non-launcher installs

`GET /games` still returns the same contract (`games`, `count`) with richer `metadata` fields:

- `metadata.providers` list (all providers that matched the same game)
- `metadata.executables` with executable metadata (`path`, `file_size`, `mtime`, optional hash/signature when requested by matcher)

### Non-launcher scan behavior

The generic scan provider searches practical roots and tries to detect plausible game executables while skipping typical launcher/updater binaries.

Default roots include:

- `C:\Program Files\Games`
- `C:\Program Files (x86)\Games`
- `C:\Games`, `D:\Games`, `E:\Games`
- `C:\Program Files\Epic Games`
- Steam library roots discovered from `libraryfolders.vdf`

Custom roots can be added with environment variable:

```bash
set GAME_OPTIMIZER_GAME_PATHS=C:\MyGames;D:\PortableGames
```

## GPU telemetry backends

`GET /system/metrics` now includes `gpu_source`:

- `nvml`: NVIDIA NVML backend via `pynvml` (`nvidia-ml-py`) when available
- `wmi`: WMI `GPUEngine` + `Win32_VideoController`
- `fallback`: vendor-aware summary from `Win32_VideoController` when richer telemetry is unavailable

AMD/Intel direct telemetry backends are currently stubs/hooks, and gracefully fall back to WMI/fallback mode.

## Notes on privileges and safety

- Setting process priorities may require Administrator rights for some processes.
- If access is denied, backend returns graceful error details and keeps running.
- GPU telemetry is best-effort and backend-dependent (`nvml`, `wmi`, `fallback`).
- Game discovery is best-effort and depends on installed metadata, filesystem visibility, and permissions.
