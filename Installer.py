"""
AIPIXQR Node — Installer
=========================
Installs and uninstalls the Photographer Node.

  Setup tab     — step-by-step installer with live progress output.
  Uninstall tab — selective removal of components.

After a successful install, APP.py opens automatically so you can log in
and start the Runner.

Uses only Python standard library so it works before the venv exists.
Launch via AIPICSQR-Setup.bat or directly: python Installer.py
"""

import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
import tempfile
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk, messagebox

# ── Python auto-install target ────────────────────────────────────────────────

_PY_TARGET_VER  = '3.12.9'
_PY_URL_64      = f'https://www.python.org/ftp/python/{_PY_TARGET_VER}/python-{_PY_TARGET_VER}-amd64.exe'
_PY_URL_32      = f'https://www.python.org/ftp/python/{_PY_TARGET_VER}/python-{_PY_TARGET_VER}.exe'

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

# ── Colors — Windows light theme ─────────────────────────────────────────────

BG        = '#F3F3F3'   # window / dialog background
BG_CARD   = '#FFFFFF'   # white content cards
BG_LOG    = '#FAFAFA'   # log area background
BG_HEADER = '#0078D4'   # Windows accent blue header
FG        = '#1A1A1A'   # primary text
FG_HDR    = '#FFFFFF'   # text on blue header
FG_MUTED  = '#6B6B6B'   # secondary / hint text
FG_DIM    = '#C0C0C0'   # placeholder / pending icons
GREEN     = '#107C10'   # Windows success green
RED       = '#C42B1C'   # Windows error red
AMBER     = '#CA5010'   # Windows warning orange
BLUE      = '#0078D4'   # Windows accent blue
BLUE_D    = '#005A9E'   # darker blue (hover / active)
BORDER    = '#DEDEDE'   # subtle card / separator borders
BORDER_IN = '#E8E8E8'   # inner step-row separator


# ── Python auto-install helpers ───────────────────────────────────────────────

def _find_compatible_python() -> Optional[str]:
    for minor in (13, 12, 11, 10):
        try:
            r = subprocess.run(
                ['py', f'-3.{minor}', '-c', 'import sys; print(sys.executable)'],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0:
                exe = r.stdout.strip()
                if Path(exe).exists():
                    return exe
        except Exception:
            pass

    try:
        r = subprocess.run(['python', '--version'], capture_output=True, text=True, timeout=8)
        parts = r.stdout.strip().split()
        if len(parts) == 2:
            major, minor, *_ = parts[1].split('.')
            if int(major) == 3 and 10 <= int(minor) <= 13:
                r2 = subprocess.run(
                    ['python', '-c', 'import sys; print(sys.executable)'],
                    capture_output=True, text=True, timeout=8,
                )
                if r2.returncode == 0:
                    exe = r2.stdout.strip()
                    if Path(exe).exists():
                        return exe
    except Exception:
        pass

    if sys.platform == 'win32':
        try:
            import winreg
            for minor in (13, 12, 11, 10):
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        key = winreg.OpenKey(
                            hive,
                            rf'Software\Python\PythonCore\3.{minor}\InstallPath',
                        )
                        base, _ = winreg.QueryValueEx(key, '')
                        winreg.CloseKey(key)
                        exe = Path(base) / 'python.exe'
                        if exe.exists():
                            return str(exe)
                    except OSError:
                        pass
        except ImportError:
            pass

    localappdata = os.environ.get('LOCALAPPDATA', '')
    programfiles = os.environ.get('PROGRAMFILES', '')
    for minor in (313, 312, 311, 310):
        for base in [
            Path(localappdata) / f'Programs/Python/Python{minor}',
            Path(programfiles)  / f'Python{minor}',
            Path(f'C:/Python{minor}'),
        ]:
            exe = base / 'python.exe'
            if exe.exists():
                return str(exe)

    return None


def _auto_install_python(log_fn) -> Optional[str]:
    is_64 = platform.machine().endswith('64')
    url   = _PY_URL_64 if is_64 else _PY_URL_32
    arch  = 'amd64' if is_64 else 'x86'
    fname = f'python-{_PY_TARGET_VER}-{arch}.exe'
    tmp   = Path(tempfile.gettempdir()) / fname

    log_fn(f'Downloading {fname} (~25 MB)…')
    try:
        def _progress(count, block, total):
            if total > 0:
                pct = min(100, count * block * 100 // total)
                if pct % 20 == 0:
                    log_fn(f'  {pct}%…')
        urllib.request.urlretrieve(url, str(tmp), reporthook=_progress)
    except Exception as e:
        log_fn(f'Download error: {e}')
        return None

    if not tmp.exists() or tmp.stat().st_size < 1_000_000:
        log_fn('Download produced an incomplete file.')
        tmp.unlink(missing_ok=True)
        return None

    log_fn('Installing Python (please wait, 1-2 minutes)…')
    try:
        r = subprocess.run([
            str(tmp), '/quiet',
            'InstallAllUsers=0', 'PrependPath=1',
            'Include_launcher=1', 'Include_test=0', 'Include_doc=0',
        ], timeout=300)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        log_fn('Installation timed out after 5 minutes.')
        tmp.unlink(missing_ok=True)
        return None
    except Exception as e:
        log_fn(f'Installation error: {e}')
        tmp.unlink(missing_ok=True)
        return None
    finally:
        tmp.unlink(missing_ok=True)

    if rc == 1638:
        log_fn('Python already installed at this version — continuing.')
    elif rc != 0:
        log_fn(f'Installer exited with code {rc}.')
        return None

    time.sleep(2)
    return _find_compatible_python()


# ── Venv health check ─────────────────────────────────────────────────────────

def _venv_ok() -> bool:
    if not VENV_PYTHON.exists():
        return False
    return subprocess.run(
        [str(VENV_PYTHON), '-m', 'pip', '--version'],
        capture_output=True,
    ).returncode == 0


# ── Filesystem helpers ────────────────────────────────────────────────────────

def _rmtree_robust(path: str) -> list[str]:
    import shutil as _shutil
    import ctypes
    MOVEFILE_DELAY_UNTIL_REBOOT = 4
    pending: list[str] = []

    def _onerror(func, fpath: str, _exc_info):
        try:
            os.chmod(fpath, 0o777)
            func(fpath)
            return
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.MoveFileExW(fpath, None, MOVEFILE_DELAY_UNTIL_REBOOT)
        except Exception:
            pass
        pending.append(fpath)

    _shutil.rmtree(path, onerror=_onerror)
    return pending


# ── Step-row widget ───────────────────────────────────────────────────────────

class StepRow:
    _ICONS = {
        'pending': ('○', FG_DIM),
        'running': ('●', BLUE),
        'done':    ('✓', GREEN),
        'error':   ('✗', RED),
    }
    _SPINNER = ('◐', '◓', '◑', '◒')

    def __init__(self, parent: tk.Widget, label: str, last: bool = False):
        self._frame = tk.Frame(parent, bg=BG_CARD, padx=16, pady=10)
        self._frame.pack(fill=tk.X)
        if not last:
            tk.Frame(parent, bg=BORDER_IN, height=1).pack(fill=tk.X, padx=12)

        self._icon = tk.Label(
            self._frame, text='○', fg=FG_DIM, bg=BG_CARD,
            font=('Segoe UI', 12), width=2, anchor='w',
        )
        self._icon.pack(side=tk.LEFT)

        tk.Label(
            self._frame, text=label, bg=BG_CARD, fg=FG,
            font=('Segoe UI', 10), anchor='w',
        ).pack(side=tk.LEFT, padx=8)

        self._note = tk.Label(
            self._frame, text='', bg=BG_CARD, fg=FG_MUTED,
            font=('Segoe UI', 9, 'italic'),
        )
        self._note.pack(side=tk.RIGHT, padx=4)

    def set(self, state: str, note: str = ''):
        icon, color = self._ICONS.get(state, ('○', FG_DIM))
        self._icon.configure(text=icon, fg=color)
        note_color = RED if state == 'error' else FG_MUTED
        self._note.configure(text=note, fg=note_color)

    def spin(self, frame_idx: int):
        char = self._SPINNER[frame_idx % len(self._SPINNER)]
        self._icon.configure(text=char, fg=BLUE)


# ── Main app ──────────────────────────────────────────────────────────────────

class InstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('AIPIXQR Node Setup')
        self.root.geometry('680x560')
        self.root.minsize(600, 480)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(str(BASE_DIR / 'icon.ico'))
        except Exception:
            pass

        self._installing   = False
        self._spinner_idx  = 0
        self._spinner_step: str | None = None

        self._apply_style()
        self._build_header()
        self._build_body()
        self._build_footer()

        self._check_initial_state()
        self._spinner_tick()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style()
        try:
            s.theme_use('clam')
        except Exception:
            pass
        s.configure('TNotebook',
                    background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure('TNotebook.Tab',
                    background='#E5E5E5', foreground=FG_MUTED,
                    padding=[20, 7], font=('Segoe UI', 9),
                    borderwidth=0, focuscolor='')
        s.map('TNotebook.Tab',
              background=[('selected', BG_CARD), ('active', '#EFEFEF')],
              foreground=[('selected', FG)])
        s.configure('Horizontal.TProgressbar',
                    troughcolor='#E8E8E8', background=BLUE,
                    thickness=8, borderwidth=0, relief='flat')

    # ── Header (blue banner) ──────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=BG_HEADER, height=76)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        inner = tk.Frame(hdr, bg=BG_HEADER)
        inner.place(relx=0, rely=0.5, x=24, anchor='w')

        tk.Label(inner, text='AIPIXQR Node', bg=BG_HEADER, fg=FG_HDR,
                 font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        tk.Label(inner, text='Photographer upload node installer',
                 bg=BG_HEADER, fg='#BFD7F0',
                 font=('Segoe UI', 9)).pack(anchor='w')

        self._hdr_status = tk.Label(hdr, text='', bg=BG_HEADER, fg='#BFD7F0',
                                     font=('Segoe UI', 9))
        self._hdr_status.place(relx=1.0, rely=0.5, x=-20, anchor='e')

        # 1px separator under header
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill=tk.X)

    # ── Body (tabbed) ─────────────────────────────────────────────────────────

    def _build_body(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True)

        self._tab_setup     = tk.Frame(nb, bg=BG)
        self._tab_uninstall = tk.Frame(nb, bg=BG)
        nb.add(self._tab_setup,     text='  Setup  ')
        nb.add(self._tab_uninstall, text='  Uninstall  ')

        self._build_setup_tab()
        self._build_uninstall_tab()

    # ── Footer (status bar) ───────────────────────────────────────────────────

    def _build_footer(self):
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill=tk.X, side=tk.BOTTOM)
        foot = tk.Frame(self.root, bg='#EBEBEB', pady=5)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(foot, text='© 2025 AIPICSQR  ·  dashboard.aipicsqr.com',
                 bg='#EBEBEB', fg=FG_MUTED, font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=14)

    # ── Setup tab ─────────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        f = self._tab_setup

        # Steps card
        card = tk.Frame(f, bg=BG_CARD, bd=1, relief=tk.SOLID,
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill=tk.X, padx=16, pady=(14, 8))

        tk.Label(card, text='Installation Steps', bg=BG_CARD, fg=FG_MUTED,
                 font=('Segoe UI', 8, 'bold')).pack(anchor=tk.W, padx=16, pady=(10, 6))
        tk.Frame(card, bg=BORDER_IN, height=1).pack(fill=tk.X)

        self._steps: dict[str, StepRow] = {}
        step_defs = [
            ('python',  'Python 3.10 – 3.13'),
            ('venv',    'Virtual environment'),
            ('deps',    'Python packages'),
            ('models',  'AI models  (~40 MB)'),
            ('startup', 'Windows startup shortcut'),
        ]
        for i, (key, label) in enumerate(step_defs):
            last = (i == len(step_defs) - 1)
            self._steps[key] = StepRow(card, label, last=last)
        tk.Frame(card, bg=BG_CARD, height=4).pack()

        # Progress bar
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            f, variable=self._progress_var, maximum=100,
            style='Horizontal.TProgressbar',
        )

        # Log area
        log_outer = tk.Frame(f, bg=BORDER, bd=0)
        log_outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 4))

        log_inner = tk.Frame(log_outer, bg=BG_LOG)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._setup_log = tk.Text(
            log_inner, bg=BG_LOG, fg='#444444',
            font=('Consolas', 8), state=tk.DISABLED,
            height=6, wrap=tk.WORD, pady=6, padx=8,
            insertbackground=FG, relief=tk.FLAT, bd=0,
            selectbackground='#CCE4F7',
        )
        vsb = tk.Scrollbar(log_inner, command=self._setup_log.yview,
                           bd=0, width=12, relief=tk.FLAT)
        self._setup_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._setup_log.pack(fill=tk.BOTH, expand=True)

        # Install button
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(pady=(4, 10))
        self._btn_install = tk.Button(
            btn_row, text='Install', command=self._run_install,
            bg=BLUE, fg='white',
            activebackground=BLUE_D, activeforeground='white',
            relief=tk.FLAT, padx=36, pady=9,
            font=('Segoe UI', 10, 'bold'),
            cursor='hand2', bd=0,
        )
        self._btn_install.pack()

    def _slog(self, msg: str, color: str = '#444444'):
        self._setup_log.configure(state=tk.NORMAL)
        tag = f'c{abs(hash(color))}'
        self._setup_log.tag_configure(tag, foreground=color)
        self._setup_log.insert(tk.END, msg + '\n', tag)
        self._setup_log.see(tk.END)
        self._setup_log.configure(state=tk.DISABLED)

    def _set_progress(self, pct: float):
        self._progress_var.set(pct)
        if pct > 0 and not self._progress_bar.winfo_ismapped():
            self._progress_bar.pack(fill=tk.X, padx=16, pady=(0, 4))

    # ── Install flow ──────────────────────────────────────────────────────────

    def _check_initial_state(self):
        def task():
            states = self._probe_steps()
            self.root.after(0, lambda: self._apply_states(states))
        threading.Thread(target=task, daemon=True).start()

    def _probe_steps(self) -> dict[str, tuple[str, str]]:
        results: dict[str, tuple[str, str]] = {}

        v = sys.version_info
        if v.major == 3 and 10 <= v.minor <= 13:
            results['python'] = ('done', f'{v.major}.{v.minor}.{v.micro}')
        else:
            results['python'] = ('error', f'{v.major}.{v.minor} — will auto-install 3.12')

        if _venv_ok():
            results['venv'] = ('done', 'ready')
        elif VENV_PYTHON.exists():
            results['venv'] = ('error', 'broken — reinstall')
        else:
            results['venv'] = ('pending', '')

        if VENV_PYTHON.exists():
            ok = subprocess.run(
                [str(VENV_PYTHON), '-c',
                 'import cv2, PIL, watchdog, psutil, requests;'
                 'import certifi, os; assert os.path.isfile(certifi.where())'],
                capture_output=True,
            ).returncode == 0
            results['deps'] = ('done', 'installed') if ok else ('pending', '')
        else:
            results['deps'] = ('pending', '')

        if YUNET_MODEL.exists() and SFACE_MODEL.exists():
            results['models'] = ('done', 'ready')
        else:
            results['models'] = ('pending', '')

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
            self._btn_install.configure(text='Repair / Reinstall',
                                        bg='#5A5A5A', activebackground='#444444')
            self._hdr_status.configure(text='● Installed  ', fg='#A8D8A8')
        else:
            self._btn_install.configure(text='Install', bg=BLUE, activebackground=BLUE_D)
            self._hdr_status.configure(text='● Not installed  ', fg='#FFD080')

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
        def log(msg, c='#444444'): ui(lambda: self._slog(msg, c))
        def prog(pct): ui(lambda: self._set_progress(pct))

        # 1. Python
        self._spinner_step = 'python'
        step_state('python', 'running')
        v = sys.version_info
        if v.major == 3 and 10 <= v.minor <= 13:
            step_state('python', 'done', f'{v.major}.{v.minor}.{v.micro}')
            log(f'Python {v.major}.{v.minor}.{v.micro}', GREEN)
        else:
            log(f'Python {v.major}.{v.minor} is not compatible — installing {_PY_TARGET_VER}…', AMBER)
            step_state('python', 'running', f'installing {_PY_TARGET_VER}…')
            new_py = _auto_install_python(log)
            if new_py:
                step_state('python', 'done', f'{_PY_TARGET_VER} installed')
                log(f'Python {_PY_TARGET_VER} ready — relaunching installer…', GREEN)
                subprocess.Popen([new_py, str(BASE_DIR / 'Installer.py')], cwd=str(BASE_DIR))
                ui(lambda: self.root.after(1200, self.root.destroy))
                return
            else:
                step_state('python', 'error', 'install failed')
                log('Automatic install failed. Download Python 3.10-3.13 from python.org', RED)
                log('and run the setup again — it will detect the new version.', RED)
                ui(lambda: self._finish_install(False))
                return
        ok_count += 1
        prog((ok_count / n) * 100)

        # 2. Venv
        self._spinner_step = 'venv'
        step_state('venv', 'running')
        venv_dir = BASE_DIR / 'venv'
        if not _venv_ok():
            if venv_dir.exists():
                log('Broken virtual environment detected — removing…', AMBER)
                _rmtree_robust(str(venv_dir))
            log('Creating virtual environment…')
            r = subprocess.run([sys.executable, '-m', 'venv', str(venv_dir)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                step_state('venv', 'error')
                log(r.stderr or 'venv creation failed', RED)
                ui(lambda: self._finish_install(False))
                return
            if not _venv_ok():
                subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'],
                               capture_output=True)
                subprocess.run([str(VENV_PYTHON), '-m', 'ensurepip', '--upgrade'],
                               capture_output=True)
        step_state('venv', 'done', 'ready')
        log('Virtual environment ready', GREEN)
        ok_count += 1
        prog((ok_count / n) * 100)

        # 3. Dependencies
        self._spinner_step = 'deps'
        step_state('deps', 'running')
        deps_ok = subprocess.run(
            [str(VENV_PYTHON), '-c',
             'import cv2, PIL, watchdog, psutil, requests;'
             'import certifi, os; assert os.path.isfile(certifi.where())'],
            capture_output=True,
        ).returncode == 0

        if not deps_ok:
            log('Installing packages…')
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

        # 4. AI models
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

        # 5. Startup shortcut
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
            runner_py = str(BASE_DIR / 'Runner.py')
            work_dir  = str(BASE_DIR)

            vbs = BASE_DIR / '_shortcut_tmp.vbs'
            vbs.write_text(
                f'Set w = CreateObject("WScript.Shell")\n'
                f'Set s = w.CreateShortcut("{shortcut}")\n'
                f's.TargetPath = "{pythonw}"\n'
                f's.Arguments = Chr(34) & "{runner_py}" & Chr(34)\n'
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

    def _launch_app(self):
        try:
            app_py  = BASE_DIR / 'APP.py'
            pythonw = VENV_PYTHONW if VENV_PYTHONW.exists() else Path(sys.executable).parent / 'pythonw.exe'
            flags   = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            subprocess.Popen([str(pythonw), str(app_py)], cwd=str(BASE_DIR), creationflags=flags)
        except Exception:
            pass

    def _finish_install(self, success: bool):
        self._installing = False
        self._btn_install.configure(state=tk.NORMAL)
        if success:
            self._btn_install.configure(text='Repair / Reinstall',
                                        bg='#5A5A5A', activebackground='#444444')
            self._hdr_status.configure(text='● Installed  ', fg='#A8D8A8')
            self._slog('Installation complete!', GREEN)
            self.root.after(800, self._launch_app)
        else:
            self._btn_install.configure(text='Retry Install', bg=BLUE, activebackground=BLUE_D)
            self._hdr_status.configure(text='● Install failed  ', fg='#FFAAAA')

    # ── Spinner animation ─────────────────────────────────────────────────────

    def _spinner_tick(self):
        if self._spinner_step and self._spinner_step in self._steps:
            self._steps[self._spinner_step].spin(self._spinner_idx)
            self._spinner_idx += 1
        self.root.after(150, self._spinner_tick)

    # ── Uninstall tab ─────────────────────────────────────────────────────────

    def _make_check_row(self, parent: tk.Frame, var: tk.BooleanVar,
                        label: str, hint: str, last: bool = False):
        row = tk.Frame(parent, bg=BG_CARD, padx=16, pady=10, cursor='hand2')
        row.pack(fill=tk.X)
        if not last:
            tk.Frame(parent, bg=BORDER_IN, height=1).pack(fill=tk.X, padx=12)

        icon_lbl = tk.Label(row, bg=BG_CARD, font=('Segoe UI', 11), width=2, anchor='w')
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
                 font=('Segoe UI', 13, 'bold')).pack(anchor=tk.W, padx=18, pady=(16, 2))
        tk.Label(f, text='Select the components to remove, then click Uninstall.',
                 bg=BG, fg=FG_MUTED, font=('Segoe UI', 9)).pack(anchor=tk.W, padx=18, pady=(0, 10))

        card = tk.Frame(f, bg=BG_CARD, bd=1, relief=tk.SOLID,
                        highlightbackground=BORDER, highlightthickness=1)
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
             'Terminates the background runner if currently running'),
            (self._un_creds,    'Clear photographer credentials',
             'Deletes node_config.json — logged-in account will be removed'),
            (self._un_models,   'Delete AI models  (~40 MB)',
             'Removes the models/ directory'),
            (self._un_venv,     'Delete virtual environment  (~500 MB)',
             'Removes venv/ — packages will need reinstalling'),
        ]

        for i, (var, label, hint) in enumerate(opts):
            self._make_check_row(card, var, label, hint, last=(i == len(opts) - 1))
        tk.Frame(card, bg=BG_CARD, height=4).pack()

        self._btn_uninstall = tk.Button(
            f, text='Uninstall Selected',
            command=self._run_uninstall,
            bg='#B71C1C', fg='white',
            activebackground='#8B0000', activeforeground='white',
            relief=tk.FLAT, padx=28, pady=9,
            font=('Segoe UI', 10, 'bold'), cursor='hand2', bd=0,
        )
        self._btn_uninstall.pack(pady=(0, 8))

        log_outer = tk.Frame(f, bg=BORDER)
        log_outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        log_inner = tk.Frame(log_outer, bg=BG_LOG)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._un_log = tk.Text(
            log_inner, bg=BG_LOG, fg='#444444',
            font=('Consolas', 8), state=tk.DISABLED,
            wrap=tk.WORD, pady=6, padx=8, relief=tk.FLAT, bd=0,
            selectbackground='#CCE4F7',
        )
        vsb = tk.Scrollbar(log_inner, command=self._un_log.yview,
                           bd=0, width=12, relief=tk.FLAT)
        self._un_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._un_log.pack(fill=tk.BOTH, expand=True)

    def _ulog(self, msg: str, color: str = '#444444'):
        self._un_log.configure(state=tk.NORMAL)
        tag = f'c{abs(hash(color))}'
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
        def ui(fn): self.root.after(0, fn)
        def log(msg, c='#444444'): ui(lambda: self._ulog(msg, c))

        runner_was_stopped = False
        if self._un_stop.get():
            log('Stopping node processes…')
            try:
                import psutil as _psutil
                _have_psutil = True
            except ImportError:
                _have_psutil = False

            def _kill_by_script(script_name: str) -> bool:
                killed = False
                if _have_psutil:
                    for p in _psutil.process_iter(['pid', 'cmdline']):
                        try:
                            cmd = p.info.get('cmdline') or []
                            if (any(script_name in c for c in cmd) and
                                    any('python' in c.lower() for c in cmd)):
                                p.terminate()
                                killed = True
                        except Exception:
                            pass
                elif VENV_PYTHON.exists():
                    r = subprocess.run(
                        [str(VENV_PYTHON), '-c',
                         f'import psutil;'
                         f'procs=[p for p in psutil.process_iter(["pid","cmdline"])'
                         f' if any("{script_name}" in c for c in (p.info.get("cmdline") or []))'
                         f' and any("python" in c.lower() for c in (p.info.get("cmdline") or []))];'
                         f'[p.terminate() for p in procs];print(len(procs))'],
                        capture_output=True, text=True, timeout=10,
                    )
                    count = r.stdout.strip()
                    killed = count.isdigit() and int(count) > 0
                return killed

            try:
                runner_stopped = _kill_by_script('Runner.py')
                app_stopped    = _kill_by_script('APP.py')
                runner_was_stopped = runner_stopped or app_stopped
                if runner_stopped:
                    log('Runner stopped', GREEN)
                if app_stopped:
                    log('APP stopped', GREEN)
                if not runner_stopped and not app_stopped:
                    log('No node processes were running', FG_MUTED)
            except Exception as e:
                log(f'Could not stop processes: {e}', AMBER)

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

        if self._un_creds.get():
            if CONFIG_PATH.exists():
                try:
                    CONFIG_PATH.unlink()
                    log('Credentials cleared', GREEN)
                except Exception as e:
                    log(f'Could not clear credentials: {e}', RED)
            else:
                log('No credentials file found — skipping', FG_MUTED)

        if self._un_models.get():
            if MODELS_DIR.exists():
                pending = _rmtree_robust(str(MODELS_DIR))
                if pending:
                    log(f'Models mostly deleted — {len(pending)} locked file(s) queued for next reboot', AMBER)
                else:
                    log('AI models deleted', GREEN)
            else:
                log('Models directory not found — skipping', FG_MUTED)

        if self._un_venv.get():
            venv_dir = BASE_DIR / 'venv'
            if venv_dir.exists():
                if runner_was_stopped:
                    time.sleep(2)
                pending = _rmtree_robust(str(venv_dir))
                if pending:
                    log(f'venv mostly deleted — {len(pending)} locked file(s) queued for next reboot', AMBER)
                else:
                    log('Virtual environment deleted', GREEN)
            else:
                log('venv not found — skipping', FG_MUTED)

        log('Done. Closing in 5 seconds — then you can delete this folder.', GREEN)
        ui(lambda: self._btn_uninstall.configure(state=tk.DISABLED, text='Done'))
        ui(self._check_initial_state)
        time.sleep(5)
        ui(self.root.destroy)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
