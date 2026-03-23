# Contributing to GameOptimizer

Thanks for helping improve GameOptimizer.

## Local setup

1. Create and activate a Python virtual environment.
2. Install backend dependencies from `backend/requirements.txt`.
3. Install optional dev dependencies from `backend/requirements-dev.txt`.
4. Install Node dependencies with `npm install`.

## Development workflow

- Keep changes focused and small.
- Preserve API compatibility unless a breaking change is discussed first.
- Add or update tests when behavior changes.
- Validate syntax before opening a PR:
  - `python -m py_compile backend/app/*.py`
  - `node --check electron/main.js`
  - `node --check electron/renderer.js`

## Coding style

- Python: explicit types where practical, defensive error handling for Windows APIs.
- JavaScript: keep Electron scripts simple and dependency-light.
- Prefer additive changes over large rewrites.

## Pull requests

- Use clear conventional commit messages (`feat:`, `fix:`, `docs:`, etc.).
- Describe the motivation and risk profile in the PR description.
- Link related issues and include validation steps you ran locally.

## Reporting bugs

Open a GitHub issue with:

- Windows version
- GPU vendor/driver context if relevant
- Steps to reproduce
- Expected vs actual behavior
