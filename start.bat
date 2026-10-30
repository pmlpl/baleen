@echo off
REM ===== baleen one-click startup =====
REM Order matters: docker (db+redis) -> worker -> API -> wait until healthy -> frontend.
REM Frontend talks to the API on first paint, so it must start last.
REM Close each window to stop that service. Re-run is safe (idempotent).
setlocal

echo [1/4] Starting database and Redis...
cd /d "%~dp0backend"
docker compose up -d
if errorlevel 1 (
  echo   ERROR: docker compose failed. Is Docker Desktop running?
  pause
  exit /b 1
)

echo [2/4] Starting worker (import pipeline)...
start "baleen-worker" cmd /k "cd /d %~dp0backend && uv run arq app.worker.worker.WorkerSettings"

echo [3/4] Starting API...
start "baleen-api" cmd /k "cd /d %~dp0backend && uv run fastapi dev"

REM ---- wait for API: poll /healthz until status=ok (db+redis both up) ----
REM healthz returns HTTP 200 even when db/redis are down, so check the body.
REM curl ships with Windows 10 1803+; if missing, fall back to a fixed wait
REM (no probe possible) instead of spinning until the timeout.
where curl >nul 2>&1
if errorlevel 1 (
  echo       WARNING: curl not found - cannot probe the API.
  echo       Waiting 20s instead, then starting the frontend.
  timeout /t 20 /nobreak >nul
  goto startweb
)

echo       Waiting for API to be ready...
set /a tries=0
:waitloop
set /a tries+=1
curl -s -m 2 http://127.0.0.1:8000/healthz 2>nul | findstr /C:"\"status\":\"ok\"" >nul
if not errorlevel 1 goto ready
if %tries% GEQ 45 goto timeout
timeout /t 2 /nobreak >nul
goto waitloop

:ready
echo       API is ready (after %tries% checks).
goto startweb

:timeout
echo       WARNING: API did not become healthy within ~90s.
echo       Starting frontend anyway; check the baleen-api window for errors.

:startweb
echo [4/4] Starting frontend...
start "baleen-web" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo.
echo All started:
echo   worker = import pipeline (closes = imports stuck at queued)
echo   API    = http://127.0.0.1:8000/docs
echo   web    = http://localhost:5173
echo.
echo Notes:
echo   - First run is slow: bge model loads on first import, API warms up.
echo   - The web window may print its port after the API window is ready.
pause
