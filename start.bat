@echo off
REM ===== baleen one-click startup =====
REM Starts: docker(db+redis) + worker + API + frontend
REM Close each window to stop that service. Re-run is safe (idempotent).

echo [1/3] Starting database and Redis...
cd /d "%~dp0backend"
docker compose up -d

echo [2/3] Starting worker (import pipeline)...
start "baleen-worker" cmd /k "cd /d %~dp0backend && uv run arq app.worker.worker.WorkerSettings"

echo [3/3] Starting API and frontend...
start "baleen-api" cmd /k "cd /d %~dp0backend && uv run fastapi dev"
start "baleen-web" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo.
echo All started:
echo   worker = import pipeline (closes = imports stuck at queued)
echo   API    = http://127.0.0.1:8000/docs
echo   web    = http://localhost:5173
echo.
pause
