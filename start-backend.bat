@echo off
REM =====================================================
REM Start backend uvicorn (foreground; close window to stop)
REM Auto-cleans any stale process bound to port 8000.
REM =====================================================
setlocal

cd /d "%~dp0"

REM 1. Ensure .env exists
if not exist ".env" (
  echo [init] .env not found, copying from .env.example ...
  copy /Y ".env.example" ".env" >nul
)

REM 2. Check backend venv
if not exist "backend\.venv\Scripts\python.exe" (
  echo [error] backend\.venv not found. Setup first:
  echo   cd backend
  echo   python -m venv .venv
  echo   .venv\Scripts\activate
  echo   pip install -e ".[dev]"
  pause
  exit /b 1
)

REM 3. Kill any stale process on port 8000
echo [check] Looking for stale process on port 8000 ...
for /f "tokens=5" %%a in ('netstat -ano -p tcp ^| findstr ":8000 " ^| findstr LISTENING') do (
  echo [cleanup] Killing stale PID %%a on port 8000
  taskkill /F /PID %%a >nul 2>&1
)

title BFT - Backend (uvicorn)

echo ============================================
echo  Binance Fast Trade - Backend
echo  Docs:  http://127.0.0.1:8000/docs
echo  Close this window to stop the server.
echo ============================================
echo.

cd backend
".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo [exit] Backend stopped.
pause
