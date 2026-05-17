@echo off
setlocal EnableExtensions DisableDelayedExpansion

title AIPICSQR Node ^— Setup
color 0F

echo.
echo  =========================================
echo    AIPICSQR Photographer Node -- Setup
echo  =========================================
echo.
echo  Install location: %LOCALAPPDATA%\AIPICSQR
echo.

:: ── Require PowerShell ────────────────────────────────────────────────────────
where powershell >nul 2>&1
if errorlevel 1 (
    echo  ERROR: PowerShell is required. Please update Windows ^(10 or later^).
    pause & exit /b 1
)

:: ── Find Python ───────────────────────────────────────────────────────────────
set "PY_CMD="
python --version >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    py --version >nul 2>&1 && set "PY_CMD=py"
)
if not defined PY_CMD (
    echo  Python 3.10 or later is required but was not found.
    echo.
    echo  Opening the Python download page in your browser...
    powershell -NoProfile -Command "Start-Process 'https://www.python.org/downloads/'"
    echo.
    echo  IMPORTANT: During Python installation, check the box that says
    echo             "Add Python to PATH", then re-run AIPICSQR-Setup.bat.
    echo.
    pause & exit /b 1
)
for /f "tokens=*" %%V in ('%PY_CMD% --version 2^>^&1') do echo  Python: %%V

:: ── Create install directory ──────────────────────────────────────────────────
set "IDIR=%LOCALAPPDATA%\AIPICSQR"
if not exist "%IDIR%\" (
    md "%IDIR%"
    echo  Created: %IDIR%
) else (
    echo  Updating existing installation at %IDIR%
)

:: ── Download package ──────────────────────────────────────────────────────────
set "ZIP_URL=https://dashboard.aipicsqr.com/downloads/aipicsqr-node.zip"
set "ZIP_TMP=%TEMP%\aipicsqr-node-setup.zip"

echo.
echo  Downloading node package...
powershell -NoProfile -Command "$wc=[Net.WebClient]::new(); try { $wc.DownloadFile('%ZIP_URL%','%ZIP_TMP%') } catch { Write-Host ('  ERROR: '+$_.Exception.Message) -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
    echo.
    echo  Download failed. Check your internet connection and try again.
    pause & exit /b 1
)
echo  Downloaded.

:: ── Extract ───────────────────────────────────────────────────────────────────
echo  Extracting files...
powershell -NoProfile -Command "try { Expand-Archive -LiteralPath '%ZIP_TMP%' -DestinationPath '%IDIR%' -Force } catch { Write-Host ('  ERROR: '+$_.Exception.Message) -ForegroundColor Red; exit 1 }"
del /q "%ZIP_TMP%" 2>nul
if errorlevel 1 (
    echo.
    echo  Extraction failed. Close any open Explorer windows in %IDIR% and retry.
    pause & exit /b 1
)
echo  Extracted.

:: ── Verify key file ───────────────────────────────────────────────────────────
if not exist "%IDIR%\Installer.py" (
    echo.
    echo  ERROR: Installer.py not found in %IDIR%.
    echo  The zip may have a different structure. Contact support.
    pause & exit /b 1
)

:: ── Launch setup wizard ───────────────────────────────────────────────────────
echo.
echo  Launching setup wizard...
echo  Follow the steps in the window that opens to:
echo    1. Install Python packages and AI models
echo    2. Log in with your Quick Connect code or Photographer ID
echo    3. Start processing photos automatically
echo.
pushd "%IDIR%"
start "" "%PY_CMD%" "Installer.py"
popd

echo  Setup wizard launched. You can close this window.
echo.
timeout /t 4 /nobreak >nul
exit /b 0
