# Backend Runbook

The backend is managed with `uv` and is expected to run inside the repository `.venv`.

## One-time setup

```powershell
uv venv .venv
uv sync --group test
```

## Start the backend

```powershell
./scripts/start-backend.ps1
```

The script runs `backend.app.main:create_app` with `.venv\Scripts\python.exe` and fills the minimum runtime environment variables required by the current summary flow.

## Manual smoke test

Start the backend in another PowerShell window, then run:

```powershell
./scripts/manual-smoke-test.ps1
```

The smoke script exercises the current minimal loop:

`health -> create pool item -> list pool -> precheck -> summary run -> get run -> get events`
