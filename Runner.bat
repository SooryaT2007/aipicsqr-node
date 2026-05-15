@echo off
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo [ERROR] Node is not installed. Run Installer.bat first.
    pause
    exit /b 1
)

REM Launch the runner with no console window (pythonw = windowless Python)
start "" /B "%~dp0venv\Scripts\pythonw.exe" "%~dp0Runner.py"

echo Runner started in background. Use APP.py to view activity.
timeout /t 2 /nobreak >nul
