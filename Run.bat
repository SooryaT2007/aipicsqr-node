@echo off
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo [ERROR] Node is not installed. Run Install.bat first.
    pause
    exit /b 1
)

REM Launch the node with no console window (pythonw = windowless Python)
start "" /B "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"

echo Node started in background. Use App.py to view activity.
timeout /t 2 /nobreak >nul
