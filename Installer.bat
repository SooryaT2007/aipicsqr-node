@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AIPICSQR Node — Installer
color 0F

echo.
echo  =============================================
echo    AIPICSQR Photographer Node -- Installer
echo  =============================================
echo.

:: ── Require PowerShell (Windows 10+) ────────────────────────────────────────
where powershell >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] PowerShell is required. Please update Windows to version 10 or later.
    pause & exit /b 1
)

:: ── Detect compatible Python 3.10-3.13 ──────────────────────────────────────
echo  Checking for Python 3.10-3.13...
set "PYTHON_EXE="
call :find_python

if not defined PYTHON_EXE (
    echo  No compatible Python found. Installing Python 3.12.9 automatically...
    echo.
    call :install_python
    if errorlevel 1 goto :python_failed
    echo.
    echo  Locating newly installed Python...
    call :find_python
)

if not defined PYTHON_EXE (
    :python_failed
    echo.
    echo  [ERROR] Could not install or locate a compatible Python.
    echo.
    echo  Please install Python 3.10-3.13 manually from https://python.org
    echo  During install, check the box "Add Python to PATH", then run this file again.
    echo.
    pause & exit /b 1
)

echo  Python OK: !PYTHON_EXE!
echo.

:: ── Launch Installer.py ───────────────────────────────────────────────────────
if not exist "%~dp0Installer.py" (
    echo  [ERROR] Installer.py not found in %~dp0
    echo  Make sure this bat file is in the same folder as Installer.py.
    pause & exit /b 1
)

echo  Launching setup wizard...
start "" "!PYTHON_EXE!" "%~dp0Installer.py"
echo  Setup wizard launched. You can close this window.
echo.
timeout /t 3 /nobreak >nul
exit /b 0


:: =============================================================================
:find_python
:: Sets PYTHON_EXE to the path of the first compatible Python 3.10-3.13 found.
:: =============================================================================

:: 1. py launcher — try each supported minor version, newest first
for %%M in (13 12 11 10) do (
    if not defined PYTHON_EXE (
        for /f "usebackq tokens=*" %%P in (`py -3.%%M -c "import sys; print(sys.executable)" 2^>nul`) do (
            if exist "%%P" set "PYTHON_EXE=%%P"
        )
    )
)

:: 2. 'python' command — validate version before trusting it
if not defined PYTHON_EXE (
    for /f "usebackq tokens=2" %%V in (`python --version 2^>nul`) do (
        for /f "usebackq tokens=1,2 delims=." %%A in ("%%V") do (
            if "%%A"=="3" (
                if %%B GEQ 10 if %%B LEQ 13 (
                    for /f "usebackq tokens=*" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do (
                        if exist "%%P" set "PYTHON_EXE=%%P"
                    )
                )
            )
        )
    )
)

:: 3. Registry — finds installs that weren't added to PATH
if not defined PYTHON_EXE (
    set "_ROUT=%TEMP%\aipicsqr_pypath.txt"
    powershell -NoProfile -Command "foreach($m in @(13,12,11,10)){foreach($h in @('HKCU','HKLM')){try{$k=\"${h}:\Software\Python\PythonCore\3.$m\InstallPath\";$b=(Get-ItemProperty $k -EA Stop).'(default)';$e=[IO.Path]::Combine($b,'python.exe');if(Test-Path $e){[IO.File]::WriteAllText('!_ROUT!',$e);exit}}catch{}}}" 2>nul
    if exist "!_ROUT!" (
        set /p PYTHON_EXE=<"!_ROUT!"
        del "!_ROUT!" 2>nul
    )
)

:: 4. Well-known filesystem paths (last resort)
if not defined PYTHON_EXE (
    for %%M in (313 312 311 310) do (
        if not defined PYTHON_EXE (
            for %%D in (
                "%LOCALAPPDATA%\Programs\Python\Python%%M"
                "%PROGRAMFILES%\Python%%M"
                "%PROGRAMFILES(X86)%\Python%%M"
                "C:\Python%%M"
            ) do (
                if not defined PYTHON_EXE (
                    if exist "%%~D\python.exe" set "PYTHON_EXE=%%~D\python.exe"
                )
            )
        )
    )
)
exit /b 0


:: =============================================================================
:install_python
:: Downloads and silently installs Python 3.12.9 for the current user.
:: No administrator rights required (InstallAllUsers=0).
:: =============================================================================
set "_PY_VER=3.12.9"

set "_PY_ARCH=amd64"
powershell -NoProfile -Command "if (-not [Environment]::Is64BitOperatingSystem) { exit 1 }" >nul 2>&1
if errorlevel 1 set "_PY_ARCH=x86"

if "%_PY_ARCH%"=="amd64" (
    set "_PY_URL=https://www.python.org/ftp/python/%_PY_VER%/python-%_PY_VER%-amd64.exe"
    set "_PY_FILE=%TEMP%\python-%_PY_VER%-amd64.exe"
) else (
    set "_PY_URL=https://www.python.org/ftp/python/%_PY_VER%/python-%_PY_VER%.exe"
    set "_PY_FILE=%TEMP%\python-%_PY_VER%-x86.exe"
)

echo  Downloading Python %_PY_VER% (%_PY_ARCH%, ~25 MB)...
powershell -NoProfile -Command "try { (New-Object Net.WebClient).DownloadFile('%_PY_URL%','%_PY_FILE%') } catch { Write-Host ('  [ERROR] '+$_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo  [ERROR] Could not download the Python installer. Check your internet connection.
    exit /b 1
)
if not exist "%_PY_FILE%" (
    echo  [ERROR] Python installer file missing after download.
    exit /b 1
)

echo  Installing Python %_PY_VER% — please wait (1-2 minutes)...
"%_PY_FILE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0
set "_RC=%ERRORLEVEL%"
del /q "%_PY_FILE%" 2>nul

if %_RC% equ 0  ( echo  Python %_PY_VER% installed. & exit /b 0 )
if %_RC% equ 1638 ( echo  Python %_PY_VER% already installed. & exit /b 0 )
if %_RC% equ 1602 ( echo  [ERROR] Installation cancelled by user. & exit /b 1 )
if %_RC% equ 1618 ( echo  [ERROR] Another installer is running. Close it and retry. & exit /b 1 )
echo  [ERROR] Python installer exited with code %_RC%.
exit /b 1
