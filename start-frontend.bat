@echo off
REM =====================================================
REM Start frontend Vite dev server (foreground)
REM Auto-cleans any stale process bound to port 5173.
REM =====================================================
setlocal

cd /d "%~dp0"

if not exist "frontend\node_modules" (
  echo [init] node_modules not found, running npm install ...
  cd frontend
  call npm.cmd install --no-fund --no-audit
  if errorlevel 1 (
    echo [error] npm install failed
    pause
    exit /b 1
  )
  cd ..
)

REM Kill any stale process on port 5173
echo [check] Looking for stale process on port 5173 ...
for /f "tokens=5" %%a in ('netstat -ano -p tcp ^| findstr ":5173 " ^| findstr LISTENING') do (
  echo [cleanup] Killing stale PID %%a on port 5173
  taskkill /F /PID %%a >nul 2>&1
)

title BFT - Frontend (vite)

echo ============================================
echo  Binance Fast Trade - Frontend
echo  URL:   http://127.0.0.1:5173
echo  Close this window to stop the dev server.
echo ============================================
echo.

cd frontend
call npm.cmd run dev

echo.
echo [exit] Frontend stopped.
pause
