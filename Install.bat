@echo off
setlocal EnableDelayedExpansion
title AIPICSQR Node - Installer

echo ===================================================
echo        AIPICSQR Photographer Node - Setup
echo ===================================================
echo.

:: 1. Find Python — try 'python' first, fall back to 'py' launcher
set PYTHON_CMD=python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    py --version >nul 2>&1
    IF !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Python not found.
        echo.
        echo Please install Python 3.12 from:
        echo   https://www.python.org/downloads/release/python-3120/
        echo IMPORTANT: Check "Add python.exe to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    set PYTHON_CMD=py
)

:: 2. Check Python version (3.10 - 3.13 supported)
for /f "tokens=2" %%v in ('!PYTHON_CMD! --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
IF !PYMAJOR! NEQ 3 (
    echo [ERROR] Python 3 required. Found: !PYVER!
    pause & exit /b 1
)
IF !PYMINOR! LSS 10 (
    echo [ERROR] Python 3.10 or newer required. Found: !PYVER!
    pause & exit /b 1
)
IF !PYMINOR! GEQ 14 (
    echo [ERROR] Python !PYVER! not yet supported ^(use 3.12^).
    echo   https://www.python.org/downloads/release/python-3120/
    pause & exit /b 1
)
echo [OK] Python !PYVER!

:: 3. Create virtual environment
IF NOT EXIST "venv" (
    echo [INFO] Creating virtual environment...
    !PYTHON_CMD! -m venv venv
    IF !ERRORLEVEL! NEQ 0 ( echo [ERROR] venv creation failed. & pause & exit /b 1 )
)

set VENV_PY=venv\Scripts\python.exe
set VENV_PIP=venv\Scripts\pip.exe

:: 4. Install dependencies
echo [INFO] Installing packages (first run may take a few minutes)...
%VENV_PY% -m pip install --upgrade pip setuptools wheel -q
%VENV_PIP% install --only-binary :all: -r requirements.txt
IF !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] Package installation failed.
    echo If you see a version error, install Python 3.12:
    echo   https://www.python.org/downloads/release/python-3120/
    pause & exit /b 1
)
echo [OK] Packages installed

:: 5. Download AI models
echo.
echo [INFO] Downloading AI models (face detection + recognition)...
%VENV_PY% download_models.py
IF !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Model download failed. Check your internet connection.
    pause & exit /b 1
)

echo.
echo ===================================================
echo   Setup complete!
echo.
echo   Next step: double-click Run.bat to start the node.
echo   On first launch it will ask for your Photographer ID
echo   (copy it from dashboard.aipicsqr.com > Nodes).
echo ===================================================
echo.
pause
