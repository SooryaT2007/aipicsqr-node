"""
_setup.py — Internal post-install configuration wizard.
Called by Install.bat after venv and models are ready.
Handles: photographer ID / OTP collection, node registration, startup shortcut.
Not a user-facing entry point — use App.py or Install.bat.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR    = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'node_config.json'
API_BASE    = 'https://dashboard.aipicsqr.com'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _resolve_otp(otp_code: str) -> str | None:
    try:
        import requests
        resp = requests.post(
            f'{API_BASE}/api/nodes/resolve-otp',
            json={'otp_code': otp_code},
            timeout=10,
        )
        if resp.status_code == 404:
            print('  Code not found or expired — check your dashboard for the current code.')
            return None
        resp.raise_for_status()
        pid = resp.json().get('photographer_id', '')
        if not pid:
            print('  Unexpected server response.')
            return None
        return pid
    except Exception as e:
        print(f'  Could not reach server: {e}')
        return None


def _register(photographer_id: str) -> tuple[str, str] | tuple[None, None]:
    try:
        import requests
        resp = requests.post(
            f'{API_BASE}/api/node/register',
            json={
                'photographer_id': photographer_id,
                'hostname': socket.gethostname(),
                'ip_address': _local_ip(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        node_id    = data.get('node_id', '')
        node_token = data.get('node_token', '')
        if not node_id or not node_token:
            print(f'  Registration failed: {data.get("error", "unknown")}')
            return None, None
        return node_id, node_token
    except Exception as e:
        print(f'  Registration failed: {e}')
        return None, None


def _save_config(photographer_id: str, node_id: str = '', node_token: str = ''):
    # Merge with any existing config (preserve other IDs if present)
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text('utf-8'))
        except Exception:
            existing = {}
    else:
        existing = {}

    ids = list(existing.get('photographer_ids', []))
    if photographer_id not in ids:
        ids.insert(0, photographer_id)

    identities = dict(existing.get('node_identities', {}))
    if node_id and node_token:
        identities[photographer_id] = {'node_id': node_id, 'node_token': node_token}

    data = {
        'photographer_ids': ids,
        'active_photographer_id': photographer_id,
        'node_identities': identities,
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2), 'utf-8')


# ── Startup shortcut ──────────────────────────────────────────────────────────

def _create_startup_shortcut():
    """Create a hidden startup shortcut so the node launches on Windows login."""
    try:
        startup_dir = os.path.join(
            os.environ.get('APPDATA', ''),
            r'Microsoft\Windows\Start Menu\Programs\Startup',
        )
        if not os.path.isdir(startup_dir):
            print('  Startup folder not found — skipping shortcut.')
            return

        pythonw    = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'
        main_py    = BASE_DIR / 'main.py'
        shortcut   = os.path.join(startup_dir, 'AIPIXQR Node.lnk')

        # Write a VBScript, run it, then delete it
        vbs = BASE_DIR / '_shortcut_tmp.vbs'
        vbs.write_text(
            f'Set WshShell = CreateObject("WScript.Shell")\n'
            f'Set oShortcut = WshShell.CreateShortcut("{shortcut}")\n'
            f'oShortcut.TargetPath = "{pythonw}"\n'
            f'oShortcut.Arguments = Chr(34) & "{main_py}" & Chr(34)\n'
            f'oShortcut.WorkingDirectory = "{BASE_DIR}"\n'
            f'oShortcut.WindowStyle = 7\n'
            f'oShortcut.Description = "AIPIXQR Node background processor"\n'
            f'oShortcut.Save\n',
            encoding='utf-8',
        )
        result = subprocess.run(
            ['cscript', '//NoLogo', str(vbs)],
            capture_output=True, text=True,
        )
        vbs.unlink(missing_ok=True)

        if result.returncode == 0:
            print('  Startup shortcut created — node will start automatically on login.')
        else:
            print(f'  Could not create shortcut: {result.stderr.strip() or result.stdout.strip()}')
    except Exception as e:
        print(f'  Startup shortcut failed: {e}')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print('  ┌──────────────────────────────────────────┐')
    print('  │   AIPIXQR Node — Initial Configuration   │')
    print('  └──────────────────────────────────────────┘')
    print()
    print('  Link this node to your photographer account.')
    print()
    print('  Option A — Full ID:   dashboard.aipicsqr.com → Nodes → Copy ID')
    print('  Option B — 6-digit Quick Connect code from that same page')
    print()

    raw = input('  Photographer ID or 6-digit code (Enter to skip): ').strip()

    if not raw:
        print('  Skipped. Open App.py after install to link a photographer.')
        return

    # Resolve OTP if exactly 6 digits
    photographer_id = raw
    if raw.isdigit() and len(raw) == 6:
        print('  Resolving Quick Connect code…')
        photographer_id = _resolve_otp(raw) or ''
        if not photographer_id:
            print('  Could not resolve code. Link manually via App.py after install.')
            return
        print(f'  Resolved: {photographer_id[:8]}…{photographer_id[-4:]}')

    print('  Registering node…')
    node_id, node_token = _register(photographer_id)

    if node_id:
        _save_config(photographer_id, node_id, node_token)
        print(f'  Node registered! ID: {node_id[:8]}…')
    else:
        # Save ID only — user can re-register via App.py
        _save_config(photographer_id)
        print('  Saved photographer ID. Registration will retry on first run.')


if __name__ == '__main__':
    main()
    print()
    _create_startup_shortcut()
    print()
    print('  All done! Three ways to use the node:')
    print('  App.py   — manage IDs and view activity')
    print('  Run.bat  — start the node silently in the background')
    print('  It will also auto-start on Windows login.')
    print()
    input('  Press Enter to close…')
