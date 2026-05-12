@echo off
REM =====================================================
REM Start both backend and frontend in separate windows
REM =====================================================
setlocal

cd /d "%~dp0"

echo [BFT] Launching backend window...
start "BFT-Backend" cmd /k "call start-backend.bat"

timeout /t 2 /nobreak >nul

echo [BFT] Launching frontend window...
start "BFT-Frontend" cmd /k "call start-frontend.bat"

echo.
echo ============================================
echo  Two windows launched:
echo    Backend : http://127.0.0.1:8000/docs
echo    Frontend: http://127.0.0.1:5173
echo.
echo  Close each window to stop each service.
echo ============================================
timeout /t 5 /nobreak >nul
