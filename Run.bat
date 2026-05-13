@echo off
title AIPICSQR Photographer Node

IF NOT EXIST "venv\Scripts\python.exe" (
    echo [ERROR] Node is not set up yet.
    echo.
    echo Please run Install.bat first.
    echo.
    pause
    exit /b 1
)

echo ===================================================
echo        AIPICSQR Photographer Node
echo ===================================================
echo.
echo Folders are managed from your dashboard.
echo Press Ctrl+C to stop.
echo.

venv\Scripts\python.exe main.py

echo.
echo [INFO] Node stopped.
pause
