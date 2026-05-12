@echo off
setlocal EnableDelayedExpansion
title AIPICSQR Photographer Node

echo ===================================================
echo        AIPICSQR Photographer Node - Startup
echo ===================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in your system PATH!
    echo.
    echo Please install Python 3.12 from: https://www.python.org/downloads/
    echo IMPORTANT: Check "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 1b. Check Python version is within supported range (3.10 - 3.13)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
IF !PYMAJOR! NEQ 3 (
    echo [ERROR] Python 3 is required. Found: !PYVER!
    echo Please install Python 3.12 from: https://www.python.org/downloads/
    pause
    exit /b 1
)
IF !PYMINOR! LSS 10 (
    echo [ERROR] Python 3.10 or newer is required. Found: !PYVER!
    echo Please install Python 3.12 from: https://www.python.org/downloads/
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
echo [INFO] Python !PYVER! detected. OK.

:: 2. Check for .env file
IF NOT EXIST ".env" (
    echo [INFO] First time setup: Creating .env file...
    copy .env.example .env >nul
    echo.
    echo ==========================================================
    echo [INFO] A new .env file has been created from the example.
    echo You can now login through the web app and pair this node.
    echo The node will use the preconfigured public Supabase settings.
    echo ==========================================================
)

:: 3. Setup Virtual Environment
IF NOT EXIST "venv" (
    echo [INFO] Creating Python virtual environment ^(this might take a minute^)...
    python -m venv venv
    IF !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 4. Activate Virtual Environment
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

:: 5. Install Dependencies
echo [INFO] Installing/Updating required packages...
python -m pip install --upgrade pip setuptools wheel -q
pip install --only-binary :all: -r requirements.txt
IF !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)

:: 6. Download AI Models
echo.
echo [INFO] Checking/Downloading AI Models ^(Face Detection ^& Recognition^)...
python download_models.py
IF !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Failed to download AI models.
    pause
    exit /b 1
)

:: 7. Run the Node
echo.
echo ===================================================
echo          Starting AIPICSQR Node Server
echo ===================================================
echo.
python main.py

:: If main.py crashes or exits, pause so the user can see the error
echo.
echo [INFO] Node stopped.
pause
