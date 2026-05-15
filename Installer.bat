@echo off
REM AIPIXQR Node — launches the installer.
REM Requires Python 3.10-3.13 to be installed and in PATH.

python Installer.py 2>nul && exit /b 0
py Installer.py 2>nul && exit /b 0

echo.
echo [ERROR] Python not found.
echo Install Python 3.10-3.13 from https://python.org
echo Make sure to check "Add Python to PATH" during install.
echo.
pause
