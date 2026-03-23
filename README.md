# Game Optimizer (Electron + FastAPI)

Game Optimizer is a Windows-first desktop app focused on practical game-session optimization:

- Electron desktop UI
- FastAPI local backend API + WebSocket streaming
- Best-effort game discovery and watcher-based optimization
- Telemetry provider chain for NVIDIA, AMD, Intel, and Windows fallbacks

## Production usage guidance

This app is intended to run locally on Windows desktop systems. For day-to-day usage:

1. Use the default profile first.
2. Add per-game overrides only when you can validate behavior on your machine.
3. Prefer small, measurable tuning changes over aggressive global tuning.
4. Keep telemetry and optimization logs enabled (default) while validating new profiles.

## Folder structure

```text
GameOptimizer/
  electron/
  backend/
    app/
    config/
    tests/
  .github/workflows/
```

## Prerequisites (Windows)

- Python 3.10+
- Node.js 18+

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
npm install
```

Optional test dependencies:

```bash
pip install -r backend/requirements-dev.txt
```

## Run locally

```bash
npm start
```

Electron `electron/main.js` starts backend Uvicorn at `http://127.0.0.1:8765`.

## Runtime configuration

Copy `.env.example` values into your shell/session as needed:

- `GAME_OPTIMIZER_LOG_LEVEL` (default: `INFO`)
- `GAME_OPTIMIZER_POLL_INTERVAL_SECONDS` (default: `3`)
- `GAME_OPTIMIZER_GAME_PATHS` (optional scan roots, `;` separated)
- `GAME_OPTIMIZER_TELEMETRY_PROVIDER` (optional preferred provider)
- `GAME_OPTIMIZER_PROFILES_PATH` (optional custom profiles JSON path)

All variables are optional; defaults are safe when not set.

## Optimization profile system

Profiles are defined in `backend/config/profiles.json`.

### Defaults

- `default`: game process to `high`, selected background apps to `below_normal`
- `safe`: game process to `normal`, no background priority changes

### Per-game overrides

Overrides can match by:

- executable name
- executable path contains
- discovery provider

Each override can either:

- switch to another profile (`profile`), and/or
- patch settings inline (`settings`)

CPU affinity is optional and only applied when explicitly configured with a valid non-empty CPU list.

## Rollback behavior

When the watcher applies changes for a detected game session, it stores reversible state and attempts rollback on game stop:

- restore original process priority when changed
- restore original CPU affinity when changed
- skip gracefully if process no longer exists

Rollback is idempotent and result details are included in watcher events.

## Safety model and limitations

- Optimization actions are best-effort and do not crash backend on per-process failures.
- Some process operations require Administrator rights.
- Telemetry and discovery are best-effort and depend on local permissions, drivers, and launcher metadata quality.
- Optional affinity tuning is bounded and validated to avoid invalid CPU masks.

## API endpoints

- `GET /health`
- `GET /system/metrics`
- `GET /hardware`
- `GET /games`
- `POST /optimize/apply`
- `WS /ws/metrics`

`/health` includes additive diagnostics fields:

- active telemetry source/confidence
- watcher summary
- last optimization action status

## CI status

GitHub Actions runs lightweight checks on push and PR:

- Python syntax compile (`backend/app/*.py`)
- Node syntax check (`electron/main.js`)
- Node syntax check (`electron/renderer.js`)

No build step runs in CI by default.

## Tray / background mode (Windows)

- Closing with `X` hides app to system tray.
- Backend keeps running while app is hidden.
- Tray menu supports open and full quit.

## Telemetry provider chain

Current source order is vendor-native first, then safe fallbacks:

1. `nvml`
2. `amd`
3. `intel`
4. `intel_counter`
5. `wmi`
6. `pdh`
7. `fallback`

Use `GAME_OPTIMIZER_TELEMETRY_PROVIDER` to prefer one provider at runtime.

## Contributing and policies

- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `LICENSE` (MIT)
