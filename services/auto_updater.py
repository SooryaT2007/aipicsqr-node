"""
Auto-Updater Service
====================
When the server signals a newer node version via the job-pull response,
this module downloads the update zip and writes a restart batch script.

Flow:
  1. mesh_worker detects latest_version != NODE_VERSION in a job-pull response
  2. mesh_worker calls AutoUpdater.download_and_stage() in a background thread
  3. Once staged, AutoUpdater.has_staged() returns True
  4. Runner.py checks this in its shutdown sequence and calls AutoUpdater.run_staged()
  5. run_staged() spawns a detached .bat that: waits for Runner to exit → extracts
     zip → reinstalls pip deps → restarts Runner.py
  6. Runner.py exits normally

The update is deliberately NOT applied mid-batch. The Runner's shutdown_event
must be set by the caller (Runner.py) after detecting has_staged() == True and
the upload queue is idle — so no photos are lost in transit.
"""

import logging
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger('AIPICSQR-node')

BASE_DIR   = Path(__file__).parent.parent   # Node/
_ZIP_PATH  = BASE_DIR / '_pending_update.zip'
_BAT_PATH  = BASE_DIR / '_update.bat'


class AutoUpdater:
    def __init__(self):
        self._staged      = False
        self._staging     = False   # download in progress
        self._staged_lock = threading.Lock()

    # ── Public ────────────────────────────────────────────────────────────────

    def download_and_stage(self, download_url: str, new_version: str) -> None:
        """Start a background download. Non-blocking."""
        with self._staged_lock:
            if self._staged or self._staging:
                return     # already in progress
            self._staging = True
        t = threading.Thread(
            target=self._do_download,
            args=(download_url, new_version),
            daemon=True,
            name='auto-updater',
        )
        t.start()

    def has_staged(self) -> bool:
        with self._staged_lock:
            return self._staged

    def run_staged(self) -> bool:
        """
        Spawn the update batch script as a fully detached process, then return.
        Call this AFTER all services have stopped (Runner.py finally block).
        Returns True if the script was spawned successfully.
        """
        if not _BAT_PATH.exists():
            return False
        try:
            subprocess.Popen(
                ['cmd', '/c', str(_BAT_PATH)],
                creationflags=(
                    subprocess.DETACHED_PROCESS       # fully independent process
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(f'Update installer launched — Runner will restart automatically.')
            return True
        except Exception as e:
            logger.error(f'Failed to launch update script: {e}')
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_download(self, url: str, new_version: str):
        try:
            logger.info(f'Downloading update v{new_version} from {url}')
            urllib.request.urlretrieve(url, _ZIP_PATH)
            logger.info(f'Update downloaded ({_ZIP_PATH.stat().st_size // 1024} KB)')
            self._write_batch(new_version)
            with self._staged_lock:
                self._staged  = True
                self._staging = False
            logger.info(
                f'Update v{new_version} staged. '
                'Will apply after current uploads complete.'
            )
        except Exception as e:
            logger.error(f'Auto-update download failed: {e}')
            with self._staged_lock:
                self._staging = False

    def _write_batch(self, new_version: str):
        """Write the Windows batch script that applies the update after Runner exits."""
        python  = BASE_DIR / 'venv' / 'Scripts' / 'python.exe'
        pythonw = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'
        pip     = BASE_DIR / 'venv' / 'Scripts' / 'pip.exe'
        runner  = BASE_DIR / 'Runner.py'
        req     = BASE_DIR / 'requirements.txt'

        lines = [
            '@echo off',
            f'echo Applying AIPIXQR Node update v{new_version}...',
            'timeout /t 4 /nobreak >nul',          # wait for Runner.py to exit
            f'cd /d "{BASE_DIR}"',
            # Extract zip over existing files (overwrites .py files)
            f'"{python}" -m zipfile -e "{_ZIP_PATH}" .',
            # Reinstall dependencies in case requirements.txt changed
            f'if exist "{req}" "{pip}" install -q -r "{req}"',
            f'del /f "{_ZIP_PATH}" 2>nul',
            'del /f "%~f0"',                        # delete this batch file
            # Relaunch Runner.py in background (no window)
            f'start "" "{pythonw}" "{runner}"',
        ]
        _BAT_PATH.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8')
