@echo off
rem ============ baleen 一键启动 ============
rem 双击本文件即可：docker(db+redis) + worker + API + 前端 各开一个窗口。
rem 关闭对应窗口即停止该服务。再次双击无副作用（docker 幂等，任务队列不丢）。
chcp 65001 >nul
title baleen-launcher

echo [1/3] 启动数据库与 Redis（已启动则跳过，幂等）...
cd /d "%~dp0backend"
docker compose up -d

echo [2/3] 拉起 worker（后台导入进程：解析/三闸/向量化都在这里跑）...
start "baleen-worker" cmd /k "cd /d %~dp0backend && uv run arq app.worker.WorkerSettings"

echo [3/3] 拉起 API 与前端...
start "baleen-api" cmd /k "cd /d %~dp0backend && uv run fastapi dev"
start "baleen-web" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo.
echo 全部已启动：
echo   worker 窗口 = 导入的后台进程（关了它导入就会卡在排队中）
echo   API    窗口 = http://127.0.0.1:8000/docs
echo   前端   窗口 = http://localhost:5173 （问答/导入/笔记库都在这里）
echo.
pause
