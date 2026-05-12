@echo off
setlocal EnableDelayedExpansion
title AIPICSQR Photographer Node

:: Check that the installer has been run
IF NOT EXIST "venv\Scripts\python.exe" (
    echo [ERROR] Node is not installed yet.
    echo.
    echo Please run Run-AIPICSQR.bat first to install.
    echo.
    pause
    exit /b 1
)

echo ===================================================
echo        AIPICSQR Photographer Node
echo ===================================================
echo.
echo Type 'help' for available commands.
echo Press Ctrl+C to stop the node.
echo.

:: Run directly via venv Python — works even if Python is not in PATH
venv\Scripts\python.exe main.py

echo.
echo [INFO] Node stopped.
pause
