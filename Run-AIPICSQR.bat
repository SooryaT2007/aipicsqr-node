@echo off
setlocal EnableDelayedExpansion
title AIPICSQR - Node Installer

echo ===================================================
echo       AIPICSQR Photographer Node - Installer
echo ===================================================
echo.

:: 1. Find Python — try 'python' first, fall back to 'py' launcher
::    (py.exe is installed to Windows\ even when user skips "Add to PATH")
set PYTHON_CMD=python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    py --version >nul 2>&1
    IF !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Python not found.
        echo.
        echo Please install Python 3.12 from:
        echo   https://www.python.org/downloads/release/python-3120/
        echo IMPORTANT: During installation, check "Add python.exe to PATH".
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
    echo Please install Python 3.12: https://www.python.org/downloads/
    pause
    exit /b 1
)
IF !PYMINOR! LSS 10 (
    echo [ERROR] Python 3.10 or newer required. Found: !PYVER!
    echo Please install Python 3.12: https://www.python.org/downloads/
    pause
    exit /b 1
)
IF !PYMINOR! GEQ 14 (
    echo [ERROR] Python !PYVER! is not yet supported by all required packages.
    echo.
    echo Please install Python 3.12 ^(recommended^):
    echo   https://www.python.org/downloads/release/python-3120/
    echo.
    echo Python 3.14 lacks prebuilt wheels for some C-extension dependencies.
    pause
    exit /b 1
)
echo [INFO] Python !PYVER! OK.

:: 3. Create .env from template if this is the first run
IF NOT EXIST ".env" (
    echo [INFO] First run: creating .env from template...
    copy .env.example .env >nul
)

:: 4. Create virtual environment
IF NOT EXIST "venv" (
    echo [INFO] Creating virtual environment...
    !PYTHON_CMD! -m venv venv
    IF !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: From here we use the venv's Python directly — no PATH dependency needed
set VENV_PY=venv\Scripts\python.exe
set VENV_PIP=venv\Scripts\pip.exe

:: 5. Install / update dependencies
echo [INFO] Installing packages ^(this may take a few minutes on first run^)...
%VENV_PY% -m pip install --upgrade pip setuptools wheel -q
%VENV_PIP% install --only-binary :all: -r requirements.txt
IF !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo.
    echo If you see a Python version error, install Python 3.12:
    echo   https://www.python.org/downloads/release/python-3120/
    echo.
    pause
    exit /b 1
)

:: 6. Download AI models
echo.
echo [INFO] Checking / downloading AI models ^(face detection + recognition^)...
%VENV_PY% download_models.py
IF !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Failed to download AI models. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ===================================================
echo   Installation complete!
echo.
echo   Next steps:
echo   1. Open .env in a text editor
echo   2. Set PHOTOGRAPHER_ID  ^(copy from dashboard.aipicsqr.com ^> Nodes^)
echo   3. Set EVENT_ID         ^(from the Events page in the dashboard^)
echo   4. Double-click AIPICSQR.bat to start the node
echo ===================================================
echo.
pause
