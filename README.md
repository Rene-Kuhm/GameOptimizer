# Game Optimizer

Windows-first desktop app for safer, reversible game-session optimization.

Game Optimizer combines an Electron tray UI with a local FastAPI backend. It discovers installed games, watches running processes, streams hardware telemetry, applies per-game optimization profiles, and rolls changes back when the session ends.

> Goal: improve gaming-session conditions without turning the machine into a mystery box. Every optimization is best-effort, bounded, and designed to fail safely.

## Highlights

- Desktop UI with Windows system tray behavior.
- Local FastAPI API plus WebSocket metrics stream.
- Game discovery for common launchers and filesystem roots.
- Runtime watcher that handles launcher child processes and ignores overlays/anti-cheats.
- Multi-provider GPU telemetry chain for NVIDIA, AMD, Intel, WMI, PDH, and safe fallbacks.
- Dual-GPU friendly adapter reporting for laptop setups such as Intel + NVIDIA.
- Reversible optimization sessions with rollback on normal stop, crash-like exits, or watcher shutdown.
- Per-game optimization profiles with optional CPU affinity tuning.
- Windows CI test coverage for backend edge cases.

## Architecture

```text
GameOptimizer/
  electron/                 Electron shell, tray, renderer UI
  backend/
    app/                    FastAPI app, watcher, optimizer, telemetry, discovery
    config/profiles.json    Default and per-game optimization profiles
    tests/                  Pytest suite for discovery, telemetry, watcher, rollback
  scripts/run-python.js     Cross-platform Python launcher for npm scripts
  .github/workflows/        CI and Windows release workflows
```

### Runtime flow

```text
Electron UI
  ├─ starts local FastAPI backend
  ├─ subscribes to WS /ws/metrics
  └─ displays telemetry, watcher status, diagnostics

FastAPI backend
  ├─ discovers games
  ├─ watches process table
  ├─ matches game processes, including launcher children
  ├─ applies selected optimization profile
  ├─ stores reversible state
  └─ rolls back on game exit or watcher shutdown
```

## Requirements

### For development

- Windows 10/11 recommended for full behavior.
- Python 3.10+.
- Node.js 18+.
- npm.

macOS/Linux can run syntax checks and most tests because Windows-only imports are guarded, but real optimization behavior must be validated on Windows.

### For packaged usage

- Windows 10/11 x64.
- No separate Python install should be required by the packaged app.

## Setup

```bash
npm install

python3 -m venv .venv
.venv\Scripts\activate   # Windows PowerShell/CMD equivalent
pip install -r backend/requirements-dev.txt
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
npm install
```

`backend/requirements.txt` keeps Windows-only dependencies behind platform markers, so non-Windows installs do not try to install `pywin32`, `WMI`, or NVIDIA bindings.

## Run locally

```bash
npm start
```

Electron starts the backend at:

```text
http://127.0.0.1:8765
```

## Scripts

```bash
npm run backend:syntax  # Python syntax check for backend/app/*.py
npm test                # pytest backend/tests
npm run dist:win        # Windows installer/portable build; do this only on release flow
```

The npm Python scripts use `scripts/run-python.js`, which prefers the project `.venv`, then falls back to `python3`, then `python`. This avoids the classic `python: command not found` issue across developer machines.

## Runtime configuration

All variables are optional.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GAME_OPTIMIZER_LOG_LEVEL` | `INFO` | Backend log level. |
| `GAME_OPTIMIZER_POLL_INTERVAL_SECONDS` | `3` | Watcher process scan interval. |
| `GAME_OPTIMIZER_OPTIMIZATION_DELAY_SECONDS` | `0` | Delay before applying optimization after a game is detected. Useful for launcher handoff/loading. |
| `GAME_OPTIMIZER_GAME_PATHS` | empty | Extra game scan roots, separated by `;`. |
| `GAME_OPTIMIZER_TELEMETRY_PROVIDER` | auto | Preferred telemetry provider. |
| `GAME_OPTIMIZER_PROFILES_PATH` | bundled config | Custom profiles JSON path. |

## Optimization profiles

Profiles live in:

```text
backend/config/profiles.json
```

Built-in profiles:

- `default`: raises the game process priority and lowers selected safe background processes.
- `safe`: avoids aggressive process changes.

Per-game overrides can match by:

- executable name,
- executable path fragment,
- discovery provider.

Overrides may either switch profile or patch settings inline.

CPU affinity tuning is optional. It is only applied when explicitly configured with a valid, non-empty CPU list.

## Game discovery and watcher behavior

The watcher scans running processes and compares them against discovered game entries using executable name, path, metadata, and confidence scoring.

It now handles common real-world launcher behavior:

- ignores known launchers, overlays, and anti-cheat helper processes,
- tracks parent process names,
- boosts confidence for likely game child processes started by launchers,
- supports a configurable optimization delay before applying changes.

This matters because Steam, Epic, GOG, EA, Battle.net, and Ubisoft often launch a helper first, then the real game process after a short delay. Optimizing the helper is the wrong target; optimizing too early is fragile. The watcher avoids both when possible.

## Telemetry provider chain

Provider selection is vendor-native first, fallback second:

1. `nvml`
2. `amd`
3. `intel`
4. `intel_counter`
5. `wmi`
6. `pdh`
7. `fallback`

`GAME_OPTIMIZER_TELEMETRY_PROVIDER` can request a preferred provider, but availability still depends on drivers, permissions, OS support, and hardware.

### Dual-GPU laptops

On systems such as Intel iGPU + NVIDIA dGPU, a native provider may report the active dedicated GPU while Windows WMI can still see additional adapters. Game Optimizer keeps native telemetry as the primary source but appends missing WMI adapter metadata, so diagnostics show the full GPU picture instead of hiding the integrated adapter.

## GPU diagnostics

`GET /system/metrics` includes `gpu_diagnostics`:

- `status`: `ok`, `no_sample`, `metadata_only`, or `provider_unavailable`.
- `reason`: user-facing explanation for numeric utilization or `n/a`.
- `source_note`: provider-specific technical note.
- `provider_notes[]`: probe chain notes.
- `sample_state`: sample quality marker for UI detail panels.

When utilization is `n/a`, common causes are:

- no active 3D engine sample during the current WMI window,
- fallback provider returning metadata only,
- telemetry provider unavailable on the host.

The Electron UI can copy a sanitized diagnostics payload for bug reports.

## Rollback and safety model

Game Optimizer stores reversible state for each optimization session.

Rollback attempts to restore:

- original process priority,
- original CPU affinity when it was captured,
- tracked session state when the watcher stops.

Rollback is intentionally idempotent. If a process is already gone, the backend records a skipped action instead of crashing. If previous affinity was unavailable, rollback records `missing_previous_affinity` rather than applying an invalid empty mask.

Important limitations:

- Some operations require Administrator rights.
- Windows process telemetry is permission- and driver-dependent.
- PID reuse is always a risk in process tools; validation on real Windows machines is required before aggressive tuning.
- Optimization is best-effort, not a guaranteed FPS boost.

## API

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health` | Backend health, watcher summary, telemetry diagnostics. |
| `GET` | `/system/metrics` | Current system and GPU metrics. |
| `GET` | `/hardware` | Hardware summary. |
| `GET` | `/games` | Discovered games. |
| `POST` | `/optimize/apply` | Manual optimization apply request. |
| `WS` | `/ws/metrics` | Live metrics and watcher events. |

## Testing

```bash
npm test
```

Current backend tests cover:

- import compatibility on non-Windows hosts,
- GPU provider selection and WMI adapter merge behavior,
- launcher/overlay ignore lists,
- watcher matching and optimization delay,
- rollback behavior for CPU affinity and process failures,
- profile loading.

## CI

GitHub Actions runs:

- lightweight syntax checks on Ubuntu,
- backend pytest suite on `windows-latest`.

The Windows runner is important because several runtime paths depend on Windows-only APIs and package markers.

## Release installer

Windows release assets are generated by `.github/workflows/release.yml`.

Release flow:

1. Create and push a semantic version tag, for example `v0.3.0`.
2. GitHub Actions runs the Windows release workflow.
3. The workflow installs Node and Python dependencies, runs checks, and executes `npm run dist:win`.
4. Generated installer/portable `.exe` assets are attached to the GitHub Release.

The workflow can also be started manually with `workflow_dispatch`.

## Tray/background behavior

- Closing the window with `X` hides the app to the system tray.
- The backend keeps running while hidden.
- Tray menu supports reopening the window and fully quitting the app.

## Troubleshooting

### `python: command not found`

Use the npm scripts. They go through `scripts/run-python.js` and prefer `.venv` automatically.

```bash
npm run backend:syntax
npm test
```

### `pytest` is missing

Install dev dependencies into the project venv:

```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
pip install -r backend/requirements-dev.txt
```

On Windows, activate with your shell's `.venv\Scripts\activate` command.

### GPU utilization shows `n/a`

Open the GPU diagnostics panel and copy diagnostics. `n/a` usually means the active provider only has metadata, no current utilization sample, or the required driver/API is unavailable.

## Contributing

Before opening a PR:

```bash
npm run backend:syntax
npm test
node --check electron/main.js
node --check electron/renderer.js
```

Useful project docs:

- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `LICENSE` (MIT)
