"""
AIPIXQR Node — Setup & Account Manager
=======================================
Primary entry point for the Photographer Node.

  Setup tab   — step-by-step installer with live progress output.
  Account tab — link / unlink photographer IDs via full UUID or
                the 6-digit Quick Connect code shown on the dashboard.

Uses only Python standard library so it works before the venv exists.
Launch via Install.bat or directly: python Install.py
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent
CONFIG_PATH  = BASE_DIR / 'node_config.json'
VENV_PYTHON  = BASE_DIR / 'venv' / 'Scripts' / 'python.exe'
VENV_PYTHONW = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'
REQ_FILE     = BASE_DIR / 'requirements.txt'
DL_MODELS    = BASE_DIR / 'download_models.py'
MODELS_DIR   = BASE_DIR / 'models'
YUNET_MODEL  = MODELS_DIR / 'face_detection_yunet_2023mar.onnx'
SFACE_MODEL  = MODELS_DIR / 'face_recognition_sface_2021dec.onnx'
API_BASE     = 'https://dashboard.aipicsqr.com'

# ── Colors ────────────────────────────────────────────────────────────────────

BG        = '#1c1917'   # stone-900
BG_CARD   = '#292524'   # stone-800
BG_LOG    = '#0c0a09'   # stone-950
FG        = '#e7e5e4'   # stone-200
FG_MUTED  = '#78716c'   # stone-500
FG_DIM    = '#44403c'   # stone-700
GREEN     = '#22c55e'
RED       = '#ef4444'
AMBER     = '#f59e0b'
BLUE      = '#3b82f6'
BLUE_D    = '#1d4ed8'


# ── Config helpers ────────────────────────────────────────────────────────────

def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text('utf-8'))
    except Exception:
        return {}

def _write_config(data: dict):
    CONFIG_PATH.write_text(json.dumps(data, indent=2), 'utf-8')

def _get_ids() -> tuple[list[str], str, dict]:
    d = _read_config()
    ids = list(d.get('photographer_ids', []))
    legacy = d.get('photographer_id', '')
    if legacy and legacy not in ids:
        ids.insert(0, legacy)
    active = d.get('active_photographer_id', ids[0] if ids else '')
    identities = d.get('node_identities', {})
    if legacy and d.get('node_id') and legacy not in identities:
        identities[legacy] = {'node_id': d['node_id'], 'node_token': d['node_token']}
    return ids, active, identities

def _save_ids(ids: list[str], active: str, identities: dict):
    _write_config({'photographer_ids': ids, 'active_photographer_id': active,
                   'node_identities': identities})


# ── API (stdlib only — works before venv) ────────────────────────────────────

def _api_post(path: str, body: dict) -> dict:
    url = f'{API_BASE}{path}'
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return {'_status': e.code, **json.loads(raw)}
        except Exception:
            return {'_status': e.code, 'error': raw.decode('utf-8', 'replace')}
    except Exception as exc:
        raise ConnectionError(str(exc))

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def _resolve_otp(otp_code: str) -> str:
    res = _api_post('/api/nodes/resolve-otp', {'otp_code': otp_code})
    if res.get('_status') == 404 or not res.get('photographer_id'):
        raise ValueError(res.get('error', 'Code not found or expired'))
    return res['photographer_id']

def _register_node(photographer_id: str) -> tuple[str, str]:
    res = _api_post('/api/node/register', {
        'photographer_id': photographer_id,
        'hostname': socket.gethostname(),
        'ip_address': _local_ip(),
    })
    node_id    = res.get('node_id', '')
    node_token = res.get('node_token', '')
    if not node_id or not node_token:
        raise ValueError(res.get('error', 'Registration failed'))
    return node_id, node_token


# ── Step-row widget ───────────────────────────────────────────────────────────

class StepRow:
    _ICONS = {
        'pending': ('○', FG_DIM),
        'running': ('▸', AMBER),
        'done':    ('✓', GREEN),
        'error':   ('✗', RED),
    }

    def __init__(self, parent: tk.Widget, label: str):
        self._frame = tk.Frame(parent, bg=BG_CARD, padx=12, pady=7)
        self._frame.pack(fill=tk.X, pady=1)

        self._icon = tk.Label(self._frame, text='○', fg=FG_DIM, bg=BG_CARD,
                               font=('Segoe UI', 14), width=2, anchor='w')
        self._icon.pack(side=tk.LEFT)

        tk.Label(self._frame, text=label, bg=BG_CARD, fg=FG,
                 font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=10)

        self._note = tk.Label(self._frame, text='', bg=BG_CARD, fg=FG_MUTED,
                               font=('Segoe UI', 9))
        self._note.pack(side=tk.RIGHT)

    def set(self, state: str, note: str = ''):
        icon, color = self._ICONS.get(state, ('○', FG_DIM))
        self._icon.configure(text=icon, fg=color)
        self._note.configure(text=note)


# ── Main app ──────────────────────────────────────────────────────────────────

class InstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('AIPIXQR Node')
        self.root.geometry('700x580')
        self.root.minsize(620, 500)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(str(BASE_DIR / 'icon.ico'))
        except Exception:
            pass

        self._installing = False
        self._spinner_idx = 0
        self._spinner_step: str | None = None
        self._auto_installed = False

        self._apply_style()
        self._build_header()

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._tab_setup     = tk.Frame(nb, bg=BG)
        self._tab_account   = tk.Frame(nb, bg=BG)
        self._tab_uninstall = tk.Frame(nb, bg=BG)
        nb.add(self._tab_setup,     text='  Setup  ')
        nb.add(self._tab_account,   text='  Account  ')
        nb.add(self._tab_uninstall, text='  Uninstall  ')

        self._build_setup_tab()
        self._build_account_tab()
        self._build_uninstall_tab()

        self._check_initial_state()
        self._spinner_tick()

    # ── Style / Header ────────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style()
        try:
            s.theme_use('clam')
        except Exception:
            pass
        s.configure('TNotebook',          background=BG, borderwidth=0)
        s.configure('TNotebook.Tab',      background=BG_CARD, foreground=FG_MUTED,
                    padding=[16, 8], font=('Segoe UI', 10))
        s.map('TNotebook.Tab',            background=[('selected', BG)],
                                          foreground=[('selected', FG)])
        s.configure('Horizontal.TProgressbar', troughcolor=BG_CARD,
                    background=BLUE, thickness=6)

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=BG, pady=16)
        hdr.pack(fill=tk.X, padx=16)
        tk.Label(hdr, text='AIPIXQR Node', bg=BG, fg=FG,
                 font=('Segoe UI', 16, 'bold')).pack(side=tk.LEFT)
        self._hdr_status = tk.Label(hdr, text='', bg=BG, fg=FG_MUTED,
                                     font=('Segoe UI', 9))
        self._hdr_status.pack(side=tk.RIGHT)

    # ── Setup tab ─────────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        f = self._tab_setup

        # Steps card
        card = tk.Frame(f, bg=BG_CARD)
        card.pack(fill=tk.X, padx=12, pady=(12, 6))

        tk.Label(card, text='Installation', bg=BG_CARD, fg=FG_MUTED,
                 font=('Segoe UI', 8, 'bold')).pack(anchor=tk.W, padx=12, pady=(8, 4))

        self._steps: dict[str, StepRow] = {}
        step_defs = [
            ('python',  'Python 3.10 – 3.13'),
            ('venv',    'Virtual environment'),
            ('deps',    'Python packages'),
            ('models',  'AI models  (~40 MB)'),
            ('startup', 'Windows startup shortcut'),
        ]
        for key, label in step_defs:
            self._steps[key] = StepRow(card, label)
        tk.Frame(card, bg=BG_CARD, height=6).pack()

        # Progress bar (hidden until install runs)
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(f, variable=self._progress_var,
                                              maximum=100, length=300,
                                              style='Horizontal.TProgressbar')

        # Log output
        log_frame = tk.Frame(f, bg=BG_LOG, bd=1, relief=tk.SUNKEN)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self._setup_log = tk.Text(log_frame, bg=BG_LOG, fg=FG_MUTED,
                                   font=('Consolas', 8), state=tk.DISABLED,
                                   height=6, wrap=tk.WORD, pady=4, padx=6,
                                   insertbackground=FG)
        vsb = tk.Scrollbar(log_frame, command=self._setup_log.yview)
        self._setup_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._setup_log.pack(fill=tk.BOTH, expand=True)

        # Install button
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(pady=8)
        self._btn_install = tk.Button(
            btn_row, text='Install', command=self._run_install,
            bg=BLUE_D, fg='white', activebackground=BLUE, activeforeground='white',
            relief=tk.FLAT, padx=28, pady=8, font=('Segoe UI', 10, 'bold'),
            cursor='hand2', bd=0,
        )
        self._btn_install.pack()

    def _slog(self, msg: str, color: str = FG_MUTED):
        """Append a line to the setup log area."""
        self._setup_log.configure(state=tk.NORMAL)
        self._setup_log.insert(tk.END, msg + '\n', f'c{id(color)}')
        self._setup_log.tag_configure(f'c{id(color)}', foreground=color)
        self._setup_log.see(tk.END)
        self._setup_log.configure(state=tk.DISABLED)

    def _set_progress(self, pct: float):
        self._progress_var.set(pct)
        if pct > 0 and not self._progress_bar.winfo_ismapped():
            self._progress_bar.pack(fill=tk.X, padx=12, pady=2)

    # ── Install flow ──────────────────────────────────────────────────────────

    def _check_initial_state(self):
        """Silently check which steps are already done on startup."""
        def task():
            states = self._probe_steps()
            self.root.after(0, lambda: self._apply_states(states))
        threading.Thread(target=task, daemon=True).start()

    def _probe_steps(self) -> dict[str, tuple[str, str]]:
        """Returns {key: (state, note)} without running any installs."""
        results: dict[str, tuple[str, str]] = {}

        # Python
        v = sys.version_info
        if v.major == 3 and 10 <= v.minor <= 13:
            results['python'] = ('done', f'Python {v.major}.{v.minor}.{v.micro}')
        else:
            results['python'] = ('error', f'{v.major}.{v.minor} not supported')

        # Venv
        results['venv'] = ('done', 'ready') if VENV_PYTHON.exists() else ('pending', '')

        # Deps
        if VENV_PYTHON.exists():
            ok = subprocess.run(
                [str(VENV_PYTHON), '-c', 'import cv2, PIL, watchdog, psutil, requests'],
                capture_output=True,
            ).returncode == 0
            results['deps'] = ('done', 'installed') if ok else ('pending', '')
        else:
            results['deps'] = ('pending', '')

        # Models
        if YUNET_MODEL.exists() and SFACE_MODEL.exists():
            results['models'] = ('done', 'ready')
        else:
            results['models'] = ('pending', '')

        # Startup shortcut
        startup_dir = os.path.join(
            os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs\Startup'
        )
        shortcut = os.path.join(startup_dir, 'AIPIXQR Node.lnk')
        results['startup'] = ('done', 'created') if os.path.exists(shortcut) else ('pending', '')

        return results

    def _apply_states(self, states: dict[str, tuple[str, str]]):
        for key, (state, note) in states.items():
            if key in self._steps:
                self._steps[key].set(state, note)
        all_done = all(s == 'done' for s, _ in states.values())
        if all_done:
            self._btn_install.configure(text='Repair / Reinstall', bg='#44403c')
            self._hdr_status.configure(text='● Ready', fg=GREEN)
        else:
            self._btn_install.configure(text='Install', bg=BLUE_D)
            self._hdr_status.configure(text='● Not installed', fg=AMBER)
            if not self._auto_installed:
                self._auto_installed = True
                self.root.after(1200, self._run_install)

    def _run_install(self):
        if self._installing:
            return
        self._installing = True
        self._btn_install.configure(state=tk.DISABLED, text='Installing…')
        self._set_progress(2)
        threading.Thread(target=self._install_thread, daemon=True).start()

    def _install_thread(self):
        steps_order = ['python', 'venv', 'deps', 'models', 'startup']
        n = len(steps_order)
        ok_count = 0

        def ui(fn): self.root.after(0, fn)
        def step_state(k, s, note=''): ui(lambda: self._steps[k].set(s, note))
        def log(msg, c=FG_MUTED): ui(lambda: self._slog(msg, c))
        def prog(pct): ui(lambda: self._set_progress(pct))

        # ── 1. Python
        self._spinner_step = 'python'
        step_state('python', 'running')
        v = sys.version_info
        if v.major == 3 and 10 <= v.minor <= 13:
            step_state('python', 'done', f'{v.major}.{v.minor}.{v.micro}')
            log(f'Python {v.major}.{v.minor}.{v.micro}', GREEN)
        else:
            step_state('python', 'error', f'{v.major}.{v.minor} unsupported')
            log(f'Python {v.major}.{v.minor} is not supported (need 3.10–3.13)', RED)
            ui(lambda: self._finish_install(False))
            return
        ok_count += 1
        prog((ok_count / n) * 100)

        # ── 2. Venv
        self._spinner_step = 'venv'
        step_state('venv', 'running')
        if not VENV_PYTHON.exists():
            log('Creating virtual environment…')
            r = subprocess.run([sys.executable, '-m', 'venv', str(BASE_DIR / 'venv')],
                               capture_output=True, text=True)
            if r.returncode != 0:
                step_state('venv', 'error')
                log(r.stderr or 'venv creation failed', RED)
                ui(lambda: self._finish_install(False))
                return
        step_state('venv', 'done', 'ready')
        log('Virtual environment ready', GREEN)
        ok_count += 1
        prog((ok_count / n) * 100)

        # ── 3. Dependencies
        self._spinner_step = 'deps'
        step_state('deps', 'running')
        deps_ok = subprocess.run(
            [str(VENV_PYTHON), '-c', 'import cv2, PIL, watchdog, psutil, requests'],
            capture_output=True,
        ).returncode == 0

        if not deps_ok:
            log('Installing packages…')
            # Upgrade pip silently first
            subprocess.run([str(VENV_PYTHON), '-m', 'pip', 'install', '--quiet',
                            '--upgrade', 'pip'], capture_output=True)
            p = subprocess.Popen(
                [str(VENV_PYTHON), '-m', 'pip', 'install', '-r', str(REQ_FILE)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    log(line)
            p.wait()
            if p.returncode != 0:
                step_state('deps', 'error')
                log('Package installation failed', RED)
                ui(lambda: self._finish_install(False))
                return

        step_state('deps', 'done', 'installed')
        log('Packages installed', GREEN)
        ok_count += 1
        prog((ok_count / n) * 100)

        # ── 4. AI models
        self._spinner_step = 'models'
        step_state('models', 'running')
        if not (YUNET_MODEL.exists() and SFACE_MODEL.exists()):
            log('Downloading AI models…')
            p = subprocess.Popen(
                [str(VENV_PYTHON), str(DL_MODELS)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    log(line)
            p.wait()
            if p.returncode != 0:
                step_state('models', 'error')
                log('Model download failed — check internet connection', RED)
                ui(lambda: self._finish_install(False))
                return

        step_state('models', 'done', 'ready')
        log('AI models ready', GREEN)
        ok_count += 1
        prog((ok_count / n) * 100)

        # ── 5. Startup shortcut
        self._spinner_step = 'startup'
        step_state('startup', 'running')
        shortcut_ok = self._create_startup_shortcut(log)
        step_state('startup', 'done' if shortcut_ok else 'error',
                   'created' if shortcut_ok else 'skipped')
        ok_count += 1
        prog(100)

        self._spinner_step = None
        ui(lambda: self._finish_install(True))

    def _create_startup_shortcut(self, log_fn) -> bool:
        try:
            startup_dir = os.path.join(
                os.environ.get('APPDATA', ''),
                r'Microsoft\Windows\Start Menu\Programs\Startup',
            )
            if not os.path.isdir(startup_dir):
                log_fn('Startup folder not found — skipping shortcut')
                return False

            shortcut  = os.path.join(startup_dir, 'AIPIXQR Node.lnk')
            pythonw   = str(VENV_PYTHONW)
            main_py   = str(BASE_DIR / 'main.py')
            work_dir  = str(BASE_DIR)

            vbs = BASE_DIR / '_shortcut_tmp.vbs'
            vbs.write_text(
                f'Set w = CreateObject("WScript.Shell")\n'
                f'Set s = w.CreateShortcut("{shortcut}")\n'
                f's.TargetPath = "{pythonw}"\n'
                f's.Arguments = Chr(34) & "{main_py}" & Chr(34)\n'
                f's.WorkingDirectory = "{work_dir}"\n'
                f's.WindowStyle = 7\n'
                f's.Description = "AIPIXQR Node background processor"\n'
                f's.Save\n',
                encoding='utf-8',
            )
            result = subprocess.run(['cscript', '//NoLogo', str(vbs)],
                                    capture_output=True, text=True)
            vbs.unlink(missing_ok=True)
            if result.returncode == 0:
                log_fn('Startup shortcut created')
                return True
            log_fn(f'Shortcut failed: {result.stderr.strip()}')
            return False
        except Exception as e:
            log_fn(f'Shortcut error: {e}')
            return False

    def _finish_install(self, success: bool):
        self._installing = False
        self._btn_install.configure(state=tk.NORMAL)
        if success:
            self._btn_install.configure(text='Repair / Reinstall', bg='#44403c')
            self._hdr_status.configure(text='● Ready', fg=GREEN)
            self._slog('Installation complete!', GREEN)
        else:
            self._btn_install.configure(text='Retry Install', bg=BLUE_D)
            self._hdr_status.configure(text='● Install failed', fg=RED)

    # ── Spinner animation ─────────────────────────────────────────────────────

    def _spinner_tick(self):
        if self._spinner_step and self._spinner_step in self._steps:
            frames = ['▸ ', ' ▸', '  ']
            icon = frames[self._spinner_idx % len(frames)]
            self._steps[self._spinner_step]._icon.configure(text=icon[0], fg=AMBER)
            self._spinner_idx += 1
        self.root.after(400, self._spinner_tick)

    # ── Account tab ───────────────────────────────────────────────────────────

    def _build_account_tab(self):
        f = self._tab_account

        # Left: ID list
        left = tk.Frame(f, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 4), pady=12)

        tk.Label(left, text='Linked Photographers', bg=BG, fg=FG,
                 font=('Segoe UI', 11, 'bold')).pack(anchor=tk.W)
        tk.Label(left, text='▶ active  ·  right-click for full ID',
                 bg=BG, fg=FG_MUTED, font=('Segoe UI', 8)).pack(anchor=tk.W, pady=(2, 8))

        lb_wrap = tk.Frame(left, bg=BG_LOG, bd=1, relief=tk.SUNKEN)
        lb_wrap.pack(fill=tk.BOTH, expand=True)
        self._lb = tk.Listbox(lb_wrap, font=('Consolas', 10), activestyle='none',
                               selectmode=tk.SINGLE, bg=BG_LOG, fg=FG,
                               selectbackground='#374151', borderwidth=0,
                               highlightthickness=0)
        lsb = tk.Scrollbar(lb_wrap, command=self._lb.yview)
        self._lb.configure(yscrollcommand=lsb.set)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb.pack(fill=tk.BOTH, expand=True)
        self._lb.bind('<Button-3>', self._show_full_id)

        btn_row = tk.Frame(left, bg=BG)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self._mk_btn(btn_row, 'Set Active', self._set_active, bg=BG_CARD).pack(
            side=tk.LEFT, padx=(0, 4))
        self._mk_btn(btn_row, 'Unlink', self._unlink, fg=RED).pack(side=tk.LEFT)
        self._mk_btn(left, 'Logout All', self._logout_all, fg=RED).pack(
            anchor=tk.W, pady=(10, 0))

        # Right: link forms
        right = tk.Frame(f, bg=BG_CARD, bd=0)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 12), pady=12, ipadx=12, ipady=12)
        right.pack_propagate(False)
        right.configure(width=300)

        # Via full ID
        tk.Label(right, text='Link via Photographer ID', bg=BG_CARD, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W, padx=10, pady=(12, 2))
        tk.Label(right, text='dashboard.aipicsqr.com → Nodes → Copy ID',
                 bg=BG_CARD, fg=FG_MUTED, font=('Segoe UI', 8)).pack(anchor=tk.W, padx=10)

        self._id_entry = tk.Entry(right, font=('Consolas', 9),
                                   bg='#0c0a09', fg=FG, insertbackground=FG,
                                   relief=tk.FLAT, bd=1)
        self._id_entry.pack(fill=tk.X, padx=10, pady=(8, 4))
        self._id_entry.bind('<Return>', lambda _: self._link_via_id())
        self._mk_btn(right, 'Link Photographer ID', self._link_via_id,
                     bg=BLUE_D, fg='white').pack(fill=tk.X, padx=10, pady=(0, 16))

        tk.Frame(right, bg=FG_DIM, height=1).pack(fill=tk.X, padx=10, pady=(0, 12))

        # Via OTP
        tk.Label(right, text='Quick Connect  (6-digit code)', bg=BG_CARD, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W, padx=10, pady=(0, 2))
        tk.Label(right,
                 text='Open your dashboard → Nodes page and\n'
                      'enter the Quick Connect code shown there.\n'
                      'Code refreshes every 3 minutes.',
                 bg=BG_CARD, fg=FG_MUTED, font=('Segoe UI', 8),
                 justify=tk.LEFT).pack(anchor=tk.W, padx=10, pady=(0, 10))

        otp_row = tk.Frame(right, bg=BG_CARD)
        otp_row.pack(fill=tk.X, padx=10)

        self._otp_var = tk.StringVar()
        self._otp_var.trace_add('write', self._on_otp_type)
        otp_e = tk.Entry(otp_row, textvariable=self._otp_var,
                         font=('Consolas', 22), width=7, justify=tk.CENTER,
                         bg='#0c0a09', fg=FG, insertbackground=FG, relief=tk.FLAT, bd=1)
        otp_e.pack(side=tk.LEFT)
        otp_e.bind('<Return>', lambda _: self._link_via_otp())

        self._btn_otp = tk.Button(otp_row, text='Link', command=self._link_via_otp,
                                   bg='#14532d', fg='white',
                                   activebackground='#166534', activeforeground='white',
                                   relief=tk.FLAT, padx=14, pady=6,
                                   font=('Segoe UI', 9, 'bold'), cursor='hand2',
                                   state=tk.DISABLED)
        self._btn_otp.pack(side=tk.LEFT, padx=(10, 0))

        self._refresh_lb()

    def _mk_btn(self, parent, text, cmd, bg=BG_CARD, fg=FG):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief=tk.FLAT, padx=10, pady=5, font=('Segoe UI', 9),
                         cursor='hand2', activebackground=bg, activeforeground=fg)

    def _refresh_lb(self):
        ids, active, _ = _get_ids()
        self._lb.delete(0, tk.END)
        for i, pid in enumerate(ids):
            marker = '▶ ' if pid == active else '   '
            self._lb.insert(tk.END, f'{marker}{pid[:8]}…{pid[-4:]}')
            self._lb.itemconfig(i, fg=GREEN if pid == active else FG)

    def _show_full_id(self, event):
        ids, _, _ = _get_ids()
        idx = self._lb.nearest(event.y)
        if 0 <= idx < len(ids):
            messagebox.showinfo('Full Photographer ID', ids[idx])

    def _selected_pid(self) -> str | None:
        ids, _, _ = _get_ids()
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(ids):
            return None
        return ids[sel[0]]

    def _set_active(self):
        pid = self._selected_pid()
        if not pid:
            return
        ids, _, identities = _get_ids()
        _save_ids(ids, pid, identities)
        self._refresh_lb()
        messagebox.showinfo('Active Set',
            f'Active photographer:\n{pid[:8]}…{pid[-4:]}\n\nRestart the node to apply.')

    def _unlink(self):
        pid = self._selected_pid()
        if not pid:
            return
        if not messagebox.askyesno('Unlink', f'Remove {pid[:8]}…{pid[-4:]}?'):
            return
        ids, active, identities = _get_ids()
        ids = [i for i in ids if i != pid]
        identities.pop(pid, None)
        new_active = ids[0] if ids else ''
        if active == pid:
            active = new_active
        _save_ids(ids, active, identities)
        self._refresh_lb()

    def _logout_all(self):
        if not messagebox.askyesno('Logout All',
                'Remove all linked photographers and node credentials?'):
            return
        _save_ids([], '', {})
        self._refresh_lb()

    def _on_otp_type(self, *_):
        raw = self._otp_var.get()
        clean = ''.join(c for c in raw if c.isdigit())[:6]
        if clean != raw:
            self._otp_var.set(clean)
        self._btn_otp.configure(state=tk.NORMAL if len(clean) == 6 else tk.DISABLED)

    def _link_via_id(self):
        pid = self._id_entry.get().strip()
        if not pid:
            messagebox.showwarning('Empty', 'Enter a Photographer ID.')
            return
        self._do_link(pid)

    def _link_via_otp(self):
        otp = self._otp_var.get().strip()
        if len(otp) != 6:
            return
        def task():
            try:
                pid = _resolve_otp(otp)
                self.root.after(0, lambda: self._do_link(pid, clear_otp=True))
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror('Invalid Code', str(e)))
            except ConnectionError as e:
                self.root.after(0, lambda: messagebox.showerror('Connection Error', str(e)))
        threading.Thread(target=task, daemon=True).start()

    def _do_link(self, photographer_id: str, clear_otp: bool = False):
        def task():
            try:
                node_id, node_token = _register_node(photographer_id)
                def finish():
                    ids, active, identities = _get_ids()
                    if photographer_id not in ids:
                        ids.append(photographer_id)
                    identities[photographer_id] = {'node_id': node_id, 'node_token': node_token}
                    _save_ids(ids, photographer_id, identities)
                    self._id_entry.delete(0, tk.END)
                    if clear_otp:
                        self._otp_var.set('')
                    self._refresh_lb()
                    messagebox.showinfo('Linked!',
                        f'Linked to:\n{photographer_id[:8]}…{photographer_id[-4:]}\n\n'
                        'Start the node (Run.bat) to begin processing photos.')
                self.root.after(0, finish)
            except (ValueError, ConnectionError) as e:
                self.root.after(0, lambda: messagebox.showerror('Failed', str(e)))
        threading.Thread(target=task, daemon=True).start()

    # ── Uninstall tab ─────────────────────────────────────────────────────────

    def _make_check_row(self, parent: tk.Frame, var: tk.BooleanVar,
                        label: str, hint: str):
        """Custom dark-mode toggle row (avoids Checkbutton selectcolor rendering bug)."""
        row = tk.Frame(parent, bg=BG_CARD, padx=12, pady=7, cursor='hand2')
        row.pack(fill=tk.X)

        icon_lbl = tk.Label(row, bg=BG_CARD, font=('Segoe UI', 12), width=2, anchor='w')
        text_lbl = tk.Label(row, text=label, bg=BG_CARD, fg=FG,
                            font=('Segoe UI', 10), anchor='w', cursor='hand2')
        hint_lbl = tk.Label(row, text=hint, bg=BG_CARD, fg=FG_MUTED,
                            font=('Segoe UI', 8), cursor='hand2')

        def refresh(*_):
            if var.get():
                icon_lbl.configure(text='✓', fg=GREEN)
            else:
                icon_lbl.configure(text='○', fg=FG_DIM)

        var.trace_add('write', refresh)
        refresh()

        icon_lbl.pack(side=tk.LEFT)
        text_lbl.pack(side=tk.LEFT, padx=(4, 0))
        hint_lbl.pack(side=tk.LEFT, padx=(10, 0))

        def toggle(e=None):
            var.set(not var.get())

        for w in (row, icon_lbl, text_lbl, hint_lbl):
            w.bind('<Button-1>', toggle)

    def _build_uninstall_tab(self):
        f = self._tab_uninstall

        tk.Label(f, text='Uninstall', bg=BG, fg=FG,
                 font=('Segoe UI', 14, 'bold')).pack(anchor=tk.W, padx=16, pady=(16, 2))
        tk.Label(f, text='Select what to remove, then click Uninstall.',
                 bg=BG, fg=FG_MUTED, font=('Segoe UI', 9)).pack(anchor=tk.W, padx=16, pady=(0, 10))

        card = tk.Frame(f, bg=BG_CARD)
        card.pack(fill=tk.X, padx=16, pady=(0, 10))

        self._un_shortcut = tk.BooleanVar(value=True)
        self._un_stop     = tk.BooleanVar(value=True)
        self._un_creds    = tk.BooleanVar(value=True)
        self._un_models   = tk.BooleanVar(value=True)
        self._un_venv     = tk.BooleanVar(value=True)

        opts = [
            (self._un_shortcut, 'Remove auto-start shortcut',
             'Removes AIPIXQR Node from Windows Startup'),
            (self._un_stop,     'Stop running node process',
             'Terminates the background node if currently running'),
            (self._un_creds,    'Clear photographer credentials',
             'Deletes node_config.json — all linked IDs will be lost'),
            (self._un_models,   'Delete AI models  (~40 MB)',
             'Removes the models/ directory'),
            (self._un_venv,     'Delete virtual environment  (~500 MB)',
             'Removes venv/ — packages will need reinstalling'),
        ]

        for var, label, hint in opts:
            self._make_check_row(card, var, label, hint)

        tk.Frame(card, bg=BG_CARD, height=6).pack()

        self._btn_uninstall = tk.Button(
            f, text='Uninstall Selected',
            command=self._run_uninstall,
            bg='#7f1d1d', fg='white',
            activebackground=RED, activeforeground='white',
            relief=tk.FLAT, padx=24, pady=8,
            font=('Segoe UI', 10, 'bold'), cursor='hand2',
        )
        self._btn_uninstall.pack(pady=8)

        log_frame = tk.Frame(f, bg=BG_LOG, bd=1, relief=tk.SUNKEN)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))
        self._un_log = tk.Text(
            log_frame, bg=BG_LOG, fg=FG_MUTED,
            font=('Consolas', 8), state=tk.DISABLED,
            wrap=tk.WORD, pady=4, padx=6, insertbackground=FG,
        )
        vsb = tk.Scrollbar(log_frame, command=self._un_log.yview)
        self._un_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._un_log.pack(fill=tk.BOTH, expand=True)

    def _ulog(self, msg: str, color: str = FG_MUTED):
        self._un_log.configure(state=tk.NORMAL)
        tag = f'c{id(color)}'
        self._un_log.tag_configure(tag, foreground=color)
        self._un_log.insert(tk.END, msg + '\n', tag)
        self._un_log.see(tk.END)
        self._un_log.configure(state=tk.DISABLED)

    def _run_uninstall(self):
        if not (self._un_shortcut.get() or self._un_stop.get() or self._un_creds.get()
                or self._un_models.get() or self._un_venv.get()):
            messagebox.showwarning('Nothing Selected', 'Select at least one item to remove.')
            return
        if not messagebox.askyesno('Confirm Uninstall',
                'This will permanently remove the selected components.\n\nContinue?',
                icon='warning'):
            return
        self._btn_uninstall.configure(state=tk.DISABLED, text='Uninstalling…')
        threading.Thread(target=self._uninstall_thread, daemon=True).start()

    def _uninstall_thread(self):
        import shutil

        def ui(fn): self.root.after(0, fn)
        def log(msg, c=FG_MUTED): ui(lambda: self._ulog(msg, c))

        # ── Stop running node
        if self._un_stop.get():
            log('Stopping node process…')
            try:
                stopped = False
                if VENV_PYTHON.exists():
                    r = subprocess.run(
                        [str(VENV_PYTHON), '-c',
                         'import psutil;'
                         'procs=[p for p in psutil.process_iter(["pid","cmdline"])'
                         ' if any("main.py" in c for c in (p.info.get("cmdline") or []))'
                         ' and any("python" in c.lower() for c in (p.info.get("cmdline") or []))];'
                         '[p.terminate() for p in procs];print(len(procs))'],
                        capture_output=True, text=True, timeout=10,
                    )
                    count = r.stdout.strip()
                    stopped = count.isdigit() and int(count) > 0
                else:
                    import psutil
                    for p in psutil.process_iter(['pid', 'cmdline']):
                        try:
                            cmd = p.info.get('cmdline') or []
                            if any('main.py' in c for c in cmd) and any('python' in c.lower() for c in cmd):
                                p.terminate()
                                stopped = True
                        except Exception:
                            pass
                log('Node stopped' if stopped else 'Node was not running',
                    GREEN if stopped else FG_MUTED)
            except Exception as e:
                log(f'Could not stop node: {e}', AMBER)

        # ── Remove startup shortcut
        if self._un_shortcut.get():
            startup_dir = os.path.join(
                os.environ.get('APPDATA', ''),
                r'Microsoft\Windows\Start Menu\Programs\Startup',
            )
            shortcut = os.path.join(startup_dir, 'AIPIXQR Node.lnk')
            if os.path.exists(shortcut):
                try:
                    os.remove(shortcut)
                    log('Startup shortcut removed', GREEN)
                except Exception as e:
                    log(f'Could not remove shortcut: {e}', RED)
            else:
                log('Startup shortcut not found — skipping', FG_MUTED)

        # ── Clear credentials
        if self._un_creds.get():
            if CONFIG_PATH.exists():
                try:
                    CONFIG_PATH.unlink()
                    log('Credentials cleared', GREEN)
                except Exception as e:
                    log(f'Could not clear credentials: {e}', RED)
            else:
                log('No credentials file found — skipping', FG_MUTED)

        # ── Delete AI models
        if self._un_models.get():
            if MODELS_DIR.exists():
                try:
                    shutil.rmtree(str(MODELS_DIR))
                    log('AI models deleted', GREEN)
                except Exception as e:
                    log(f'Could not delete models: {e}', RED)
            else:
                log('Models directory not found — skipping', FG_MUTED)

        # ── Delete venv
        if self._un_venv.get():
            venv_dir = BASE_DIR / 'venv'
            if venv_dir.exists():
                try:
                    shutil.rmtree(str(venv_dir))
                    log('Virtual environment deleted', GREEN)
                except Exception as e:
                    log(f'Could not delete venv: {e}', RED)
            else:
                log('venv not found — skipping', FG_MUTED)

        log('Done.', GREEN)
        ui(lambda: self._btn_uninstall.configure(state=tk.NORMAL, text='Uninstall Selected'))
        ui(self._check_initial_state)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
