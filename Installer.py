"""
AIPIXQR Node — Installer
=========================
Installs and uninstalls the Photographer Node.

  Setup tab     — step-by-step installer with live progress output.
  Uninstall tab — selective removal of components.

After a successful install, APP.py opens automatically so you can log in
and start the Runner.

Uses only Python standard library so it works before the venv exists.
Launch via Installer.bat or directly: python Installer.py
"""

import os
import subprocess
import sys
import threading
import time
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

# ── Colors ────────────────────────────────────────────────────────────────────

BG        = '#1c1917'
BG_CARD   = '#292524'
BG_LOG    = '#0c0a09'
FG        = '#e7e5e4'
FG_MUTED  = '#78716c'
FG_DIM    = '#44403c'
GREEN     = '#22c55e'
RED       = '#ef4444'
AMBER     = '#f59e0b'
BLUE      = '#3b82f6'
BLUE_D    = '#1d4ed8'


# ── Venv health check ─────────────────────────────────────────────────────────

def _venv_ok() -> bool:
    """True only if the venv exists AND pip is functional inside it.

    A partial uninstall can leave pythonw.exe intact while pip's files are
    gone (deleted at reboot by MoveFileEx). This catches that state so the
    installer knows to wipe and recreate rather than blindly proceeding.
    """
    if not VENV_PYTHON.exists():
        return False
    return subprocess.run(
        [str(VENV_PYTHON), '-m', 'pip', '--version'],
        capture_output=True,
    ).returncode == 0


# ── Filesystem helpers ────────────────────────────────────────────────────────

def _rmtree_robust(path: str) -> list[str]:
    """Delete a directory tree on Windows, handling locked files gracefully."""
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
        self.root.title('AIPIXQR Node — Installer')
        self.root.geometry('700x540')
        self.root.minsize(620, 460)
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

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._tab_setup     = tk.Frame(nb, bg=BG)
        self._tab_uninstall = tk.Frame(nb, bg=BG)
        nb.add(self._tab_setup,     text='  Setup  ')
        nb.add(self._tab_uninstall, text='  Uninstall  ')

        self._build_setup_tab()
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

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(f, variable=self._progress_var,
                                              maximum=100, length=300,
                                              style='Horizontal.TProgressbar')

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
        def task():
            states = self._probe_steps()
            self.root.after(0, lambda: self._apply_states(states))
        threading.Thread(target=task, daemon=True).start()

    def _probe_steps(self) -> dict[str, tuple[str, str]]:
        results: dict[str, tuple[str, str]] = {}

        v = sys.version_info
        if v.major == 3 and 10 <= v.minor <= 13:
            results['python'] = ('done', f'Python {v.major}.{v.minor}.{v.micro}')
        else:
            results['python'] = ('error', f'{v.major}.{v.minor} not supported')

        if _venv_ok():
            results['venv'] = ('done', 'ready')
        elif VENV_PYTHON.exists():
            results['venv'] = ('error', 'broken — reinstall')
        else:
            results['venv'] = ('pending', '')

        if VENV_PYTHON.exists():
            ok = subprocess.run(
                [str(VENV_PYTHON), '-c', 'import cv2, PIL, watchdog, psutil, requests'],
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
            self._btn_install.configure(text='Repair / Reinstall', bg='#44403c')
            self._hdr_status.configure(text='● Ready', fg=GREEN)
        else:
            self._btn_install.configure(text='Install', bg=BLUE_D)
            self._hdr_status.configure(text='● Not installed', fg=AMBER)

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
        venv_dir = BASE_DIR / 'venv'
        if not _venv_ok():
            # Partial uninstalls can leave pythonw.exe intact while deleting pip.
            # Wipe whatever's left and start fresh.
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
            # Bootstrap pip in case the system Python was installed without it
            if not _venv_ok():
                subprocess.run(
                    [sys.executable, '-m', 'ensurepip', '--upgrade'],
                    capture_output=True,
                )
                # Upgrade pip inside the new venv regardless
                subprocess.run(
                    [str(VENV_PYTHON), '-m', 'ensurepip', '--upgrade'],
                    capture_output=True,
                )
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

            shortcut   = os.path.join(startup_dir, 'AIPIXQR Node.lnk')
            pythonw    = str(VENV_PYTHONW)
            runner_py  = str(BASE_DIR / 'Runner.py')
            work_dir   = str(BASE_DIR)

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
            self._btn_install.configure(text='Repair / Reinstall', bg='#44403c')
            self._hdr_status.configure(text='● Ready', fg=GREEN)
            self._slog('Installation complete!', GREEN)
            self.root.after(800, self._launch_app)
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

    # ── Uninstall tab ─────────────────────────────────────────────────────────

    def _make_check_row(self, parent: tk.Frame, var: tk.BooleanVar,
                        label: str, hint: str):
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
             'Terminates the background runner if currently running'),
            (self._un_creds,    'Clear photographer credentials',
             'Deletes node_config.json — all logged-in accounts will be removed'),
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
        def ui(fn): self.root.after(0, fn)
        def log(msg, c=FG_MUTED): ui(lambda: self._ulog(msg, c))

        runner_was_stopped = False
        if self._un_stop.get():
            log('Stopping node processes…')
            try:
                import psutil as _psutil
                _have_psutil = True
            except ImportError:
                _have_psutil = False

            def _kill_by_script(script_name: str) -> bool:
                """Terminate any python process whose cmdline contains script_name."""
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
                # Give Windows 2 s to release .pyd file handles after the runner terminates.
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
