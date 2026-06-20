chcp 65001
title Agent System
echo === Agent System Starting ===
echo.
echo [1/3] Backend...
start /min "Backend" "D:\MyPython code\agent-project\venv\Scripts\python.exe" "D:\MyPython code\agent-project\src\api\server.py"
echo [2/3] Waiting 20s for model loading...
ping 127.0.0.1 -n 21 >nul
echo [3/3] Frontend...
start "Frontend" cmd /c "cd /d D:\MyPython code\agent-frontend && npm run dev"
ping 127.0.0.1 -n 6 >nul
start http://localhost:5173/chat
echo.
echo === Done. Chat: http://localhost:5173/chat ===
pause