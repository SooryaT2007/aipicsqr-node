@echo off
setlocal EnableDelayedExpansion
title AIPIXQR Photographer Node — Installer

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║    AIPIXQR Photographer Node  —  Setup       ║
echo  ╚══════════════════════════════════════════════╝
echo.

REM ── Locate Python ─────────────────────────────────────────────────────────────
set PYTHON=
for %%p in (python py python3) do (
    if not defined PYTHON (
        %%p --version >nul 2>&1 && set PYTHON=%%p
    )
)

if not defined PYTHON (
    echo [ERROR] Python not found. Install Python 3.10-3.13 from https://python.org
    echo         Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

%PYTHON% -c "import sys; v=sys.version_info; exit(0 if v.major==3 and 10<=v.minor<=13 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10-3.13 is required. Install from https://python.org
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON% -c "import sys; print(sys.version.split()[0])"') do set PY_VER=%%v
echo [OK] Python %PY_VER% found

REM ── Virtual environment ────────────────────────────────────────────────────────
if not exist "%~dp0venv\Scripts\activate" (
    echo Creating virtual environment...
    %PYTHON% -m venv "%~dp0venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
)
echo [OK] Virtual environment ready

REM ── Dependencies ──────────────────────────────────────────────────────────────
echo Installing dependencies ^(may take a minute^)...
"%~dp0venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install --quiet -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] Dependencies installed

REM ── AI models ─────────────────────────────────────────────────────────────────
echo Downloading AI models ^(first time only, ~40 MB^)...
"%~dp0venv\Scripts\python.exe" "%~dp0download_models.py"
if errorlevel 1 (
    echo [ERROR] Model download failed. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] AI models ready

REM ── Configure photographer ID + create startup shortcut ───────────────────────
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0_setup.py"

REM ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║          Installation Complete!              ║
echo  ╠══════════════════════════════════════════════╣
echo  ║                                              ║
echo  ║  App.py   — manage IDs and view activity     ║
echo  ║  Run.bat  — start the node silently          ║
echo  ║                                              ║
echo  ║  The node will auto-start on next login.     ║
echo  ╚══════════════════════════════════════════════╝
echo.
pause
