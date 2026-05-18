@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ─────────────────────────────────────────────────────────────────────────────
:: AIPICSQR Node Setup
:: https://dashboard.aipicsqr.com
:: ─────────────────────────────────────────────────────────────────────────────
:: Double-click to run. No admin required.
::
:: On first launch this script relaunches itself as a hidden process so the
:: user never sees a terminal window. The only UI is the Python installer GUI.
:: ─────────────────────────────────────────────────────────────────────────────

if /i "%~1"=="--run" goto :main

:: Relaunch hidden. %SELF% lets PowerShell read the path via $env: to avoid
:: quoting issues when the path contains spaces.
set "SELF=%~f0"
powershell -NoProfile -WindowStyle Hidden ^
    "& {$f=$env:SELF; Start-Process cmd -ArgumentList @('/D','/C',$f,'--run') -WindowStyle Hidden}"
if errorlevel 1 goto :main
exit /b 0


:main
:: ── Require PowerShell ────────────────────────────────────────────────────────
where powershell >nul 2>&1
if errorlevel 1 (
    set "_mb_msg=PowerShell is required. Please update Windows to version 10 or later."
    set "_mb_ttl=AIPICSQR Setup"
    call :msgbox & exit /b 1
)

:: ── Detect Python 3.10–3.13 ──────────────────────────────────────────────────
set "PYTHON_EXE="
call :find_python

if not defined PYTHON_EXE (
    call :install_python
    if errorlevel 1 (
        set "_mb_msg=Python installation failed. Install Python 3.10-3.13 from python.org (tick Add Python to PATH), then run this file again."
        set "_mb_ttl=AIPICSQR Setup"
        call :msgbox & exit /b 1
    )
    call :find_python
)

if not defined PYTHON_EXE (
    set "_mb_msg=Could not find a compatible Python (3.10-3.13). Install Python from python.org (tick Add Python to PATH), then run again."
    set "_mb_ttl=AIPICSQR Setup"
    call :msgbox & exit /b 1
)

:: ── Create install directory ──────────────────────────────────────────────────
set "IDIR=%LOCALAPPDATA%\AIPICSQR"
if not exist "%IDIR%\" md "%IDIR%"

:: ── Download node package ─────────────────────────────────────────────────────
set "ZIP_URL=https://dashboard.aipicsqr.com/downloads/aipicsqr-node.zip"
set "ZIP_TMP=%TEMP%\aipicsqr-node-setup.zip"

powershell -NoProfile -WindowStyle Hidden ^
    "try { (New-Object Net.WebClient).DownloadFile('%ZIP_URL%','%ZIP_TMP%') } catch { exit 1 }"
if errorlevel 1 (
    set "_mb_msg=Download failed. Check your internet connection and try again."
    set "_mb_ttl=AIPICSQR Setup"
    call :msgbox & exit /b 1
)

:: ── Extract ───────────────────────────────────────────────────────────────────
powershell -NoProfile -WindowStyle Hidden ^
    "try { Expand-Archive -LiteralPath '%ZIP_TMP%' -DestinationPath '%IDIR%' -Force } catch { exit 1 }"
del /q "%ZIP_TMP%" 2>nul

if not exist "%IDIR%\Installer.py" (
    set "_mb_msg=Extraction failed. Close any open windows in the install folder and try again."
    set "_mb_ttl=AIPICSQR Setup"
    call :msgbox & exit /b 1
)

:: ── Launch setup GUI ──────────────────────────────────────────────────────────
:: This is the only window the user will see.
pushd "%IDIR%"
start "" "!PYTHON_EXE!" "Installer.py"
popd
exit /b 0


:: =============================================================================
:msgbox
:: Show a Windows message box without opening a terminal window.
:: Reads message from %_mb_msg% and title from %_mb_ttl%.
:: =============================================================================
powershell -NoProfile -WindowStyle Hidden -Command ^
    "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show($env:_mb_msg,$env:_mb_ttl,'OK','Error') | Out-Null"
exit /b 0


:: =============================================================================
:find_python
:: Sets PYTHON_EXE to the first compatible Python 3.10–3.13 found.
:: Search order: py launcher → PATH → registry → known paths
:: =============================================================================

:: 1. py launcher
for %%M in (13 12 11 10) do (
    if not defined PYTHON_EXE (
        for /f "usebackq tokens=*" %%P in (`py -3.%%M -c "import sys; print(sys.executable)" 2^>nul`) do (
            if exist "%%P" set "PYTHON_EXE=%%P"
        )
    )
)

:: 2. 'python' in PATH — validate version before trusting it
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

:: 3. Registry
if not defined PYTHON_EXE (
    set "_ROUT=%TEMP%\aipicsqr_pypath.txt"
    powershell -NoProfile -WindowStyle Hidden -Command "foreach($m in @(13,12,11,10)){foreach($h in @('HKCU','HKLM')){try{$k=`"${h}:\Software\Python\PythonCore\3.$m\InstallPath`";$b=(Get-ItemProperty $k -EA Stop).'(default)';$e=[IO.Path]::Combine($b,'python.exe');if(Test-Path $e){[IO.File]::WriteAllText('!_ROUT!',$e);exit}}catch{}}}" 2>nul
    if exist "!_ROUT!" (
        set /p PYTHON_EXE=<"!_ROUT!"
        del "!_ROUT!" 2>nul
    )
)

:: 4. Known filesystem paths
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
:: Downloads and silently installs Python 3.12.9 (user-scope, no admin needed).
:: =============================================================================
set "_PY_VER=3.12.9"
set "_PY_ARCH=amd64"
powershell -NoProfile -WindowStyle Hidden -Command "if (-not [Environment]::Is64BitOperatingSystem) { exit 1 }" >nul 2>&1
if errorlevel 1 set "_PY_ARCH=x86"

if "%_PY_ARCH%"=="amd64" (
    set "_PY_URL=https://www.python.org/ftp/python/%_PY_VER%/python-%_PY_VER%-amd64.exe"
    set "_PY_FILE=%TEMP%\python-%_PY_VER%-amd64.exe"
) else (
    set "_PY_URL=https://www.python.org/ftp/python/%_PY_VER%/python-%_PY_VER%.exe"
    set "_PY_FILE=%TEMP%\python-%_PY_VER%-x86.exe"
)

powershell -NoProfile -WindowStyle Hidden ^
    "try { (New-Object Net.WebClient).DownloadFile('%_PY_URL%','%_PY_FILE%') } catch { exit 1 }"
if errorlevel 1 exit /b 1
if not exist "%_PY_FILE%" exit /b 1

"%_PY_FILE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0
set "_RC=%ERRORLEVEL%"
del /q "%_PY_FILE%" 2>nul

if %_RC% equ 0    exit /b 0
if %_RC% equ 1638 exit /b 0
if %_RC% equ 1602 exit /b 1
if %_RC% equ 1618 exit /b 1
exit /b 1
