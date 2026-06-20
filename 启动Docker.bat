@echo off
chcp 65001 >nul
title Agent Docker
cd /d "D:\MyPython code"

echo === Agent Docker Startup ===
echo.
echo Waiting for Docker engine...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo Docker Desktop not ready! Starting...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    timeout /t 30 /nobreak >nul
)

echo Starting services...
docker compose up -d

echo.
echo Done! http://localhost:5173/chat
pause
