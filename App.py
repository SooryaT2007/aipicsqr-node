"""
AIPIXQR Node — Activity Logger
================================
Live log viewer for the running Photographer Node.
Shows scan and upload events; mesh operations are suppressed (DEBUG level).

To manage photographer accounts, use Install.py.
To run the node silently, use Run.bat.
"""

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

BASE_DIR     = Path(__file__).parent
CONFIG_PATH  = BASE_DIR / 'node_config.json'
LOG_DIR      = BASE_DIR / 'logs'
MAIN_PY      = BASE_DIR / 'main.py'
VENV_PYTHONW = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'

BG      = '#1c1917'
BG_LOG  = '#0c0a09'
BG_BAR  = '#292524'
FG      = '#e7e5e4'
FG_MUT  = '#78716c'
GREEN   = '#22c55e'
RED     = '#ef4444'
AMBER   = '#f59e0b'


# ── Process helpers ───────────────────────────────────────────────────────────

def _find_node():
    if not HAS_PSUTIL:
        return None
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = p.info.get('cmdline') or []
            if any('main.py' in c for c in cmd) and any('python' in c.lower() for c in cmd):
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def _start_node():
    pythonw = VENV_PYTHONW if VENV_PYTHONW.exists() else Path(sys.executable).parent / 'pythonw.exe'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.Popen([str(pythonw), str(MAIN_PY)], cwd=str(BASE_DIR), creationflags=flags)

def _stop_node():
    p = _find_node()
    if p:
        try:
            p.terminate()
        except Exception:
            pass

def _active_pid() -> str:
    try:
        d = json.loads(CONFIG_PATH.read_text('utf-8'))
        pid = d.get('active_photographer_id', '') or d.get('photographer_id', '')
        return f'{pid[:8]}…{pid[-4:]}' if len(pid) > 12 else pid
    except Exception:
        return ''


# ── App ───────────────────────────────────────────────────────────────────────

class LoggerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('AIPIXQR Node — Logger')
        self.root.geometry('740x500')
        self.root.minsize(600, 380)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(str(BASE_DIR / 'icon.ico'))
        except Exception:
            pass

        self._log_pos      = 0
        self._node_running = False

        self._build_toolbar()
        self._build_log()
        self._build_statusbar()

        threading.Thread(target=self._bg_loop, daemon=True).start()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=BG_BAR, pady=6)
        bar.pack(fill=tk.X)

        self._btn_start = self._btn(bar, '▶  Start Node', self._start, '#14532d', 'white')
        self._btn_start.pack(side=tk.LEFT, padx=(10, 4))

        self._btn_stop = self._btn(bar, '■  Stop Node', self._stop, '#7f1d1d', 'white')
        self._btn_stop.pack(side=tk.LEFT, padx=(0, 4))
        self._btn_stop.configure(state=tk.DISABLED)

        self._btn(bar, 'Clear', self._clear, BG_BAR, FG_MUT).pack(side=tk.RIGHT, padx=10)

        self._auto_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text='Auto-scroll', variable=self._auto_scroll,
                       bg=BG_BAR, fg=FG_MUT, selectcolor=BG_BAR,
                       activebackground=BG_BAR, activeforeground=FG,
                       font=('Segoe UI', 9)).pack(side=tk.RIGHT, padx=4)

    def _btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief=tk.FLAT, padx=10, pady=4,
                         font=('Segoe UI', 9, 'bold'), cursor='hand2',
                         activebackground=bg, activeforeground=fg)

    def _start(self):
        pid = _active_pid()
        if not pid:
            import tkinter.messagebox as mb
            mb.showwarning('No Account', 'No photographer linked. Open Install.py to set one up.')
            return
        _start_node()
        self._append(f'[App] Node start requested at {datetime.now().strftime("%H:%M:%S")}', FG_MUT)

    def _stop(self):
        _stop_node()
        self._append(f'[App] Node stop requested at {datetime.now().strftime("%H:%M:%S")}', FG_MUT)

    def _clear(self):
        self._log_txt.configure(state=tk.NORMAL)
        self._log_txt.delete('1.0', tk.END)
        self._log_txt.configure(state=tk.DISABLED)
        self._log_pos = 0

    # ── Log area ──────────────────────────────────────────────────────────────

    def _build_log(self):
        wrap = tk.Frame(self.root, bg=BG_LOG, bd=1, relief=tk.SUNKEN)
        wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 0))

        self._log_txt = tk.Text(
            wrap, bg=BG_LOG, fg=FG, font=('Consolas', 9),
            state=tk.DISABLED, wrap=tk.WORD,
            pady=6, padx=8, insertbackground=FG,
            selectbackground='#374151',
        )
        vsb = tk.Scrollbar(wrap, command=self._log_txt.yview)
        self._log_txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_txt.pack(fill=tk.BOTH, expand=True)

        self._log_txt.tag_configure('warn',  foreground=AMBER)
        self._log_txt.tag_configure('error', foreground=RED)
        self._log_txt.tag_configure('ok',    foreground=GREEN)
        self._log_txt.tag_configure('muted', foreground=FG_MUT)

    def _append(self, line: str, color: str | None = None):
        self._log_txt.configure(state=tk.NORMAL)
        tag = None
        if color == FG_MUT:
            tag = 'muted'
        elif color == AMBER:
            tag = 'warn'
        elif color == RED:
            tag = 'error'
        elif color == GREEN:
            tag = 'ok'
        if tag:
            self._log_txt.insert(tk.END, line + '\n', tag)
        else:
            self._log_txt.insert(tk.END, line + '\n')
        if self._auto_scroll.get():
            self._log_txt.see(tk.END)
        self._log_txt.configure(state=tk.DISABLED)

    def _append_log_line(self, line: str):
        if ' │ DEBUG  ' in line:   # suppress debug (mesh ops live here)
            return
        if ' │ WARNING' in line or ' │ WARN   ' in line:
            self._append(line, AMBER)
        elif ' │ ERROR  ' in line or ' │ CRITICA' in line:
            self._append(line, RED)
        elif 'OK ' in line or 'started' in line.lower() or 'complete' in line.lower():
            self._append(line, GREEN)
        else:
            self._append(line)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG_BAR, pady=4)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        self._dot = tk.Label(bar, text='●', fg=FG_MUT, bg=BG_BAR, font=('Segoe UI', 12))
        self._dot.pack(side=tk.LEFT, padx=(8, 2))

        self._status_lbl = tk.Label(bar, text='Checking…', bg=BG_BAR, fg=FG_MUT,
                                     font=('Segoe UI', 9))
        self._status_lbl.pack(side=tk.LEFT)

        self._pid_lbl = tk.Label(bar, text='', bg=BG_BAR, fg=FG_MUT, font=('Consolas', 8))
        self._pid_lbl.pack(side=tk.RIGHT, padx=10)

    def _update_status(self, running: bool):
        if running:
            self._dot.configure(fg=GREEN)
            self._status_lbl.configure(text='Node Running', fg=FG)
            self._btn_start.configure(state=tk.DISABLED)
            self._btn_stop.configure(state=tk.NORMAL)
        else:
            self._dot.configure(fg=RED)
            self._status_lbl.configure(text='Node Stopped', fg=FG_MUT)
            self._btn_start.configure(state=tk.NORMAL)
            self._btn_stop.configure(state=tk.DISABLED)
        pid = _active_pid()
        self._pid_lbl.configure(text=f'Photographer: {pid}' if pid else '')

    # ── Background refresh ────────────────────────────────────────────────────

    def _bg_loop(self):
        while True:
            try:
                running = _find_node() is not None
                if running != self._node_running:
                    self._node_running = running
                    self.root.after(0, lambda r=running: self._update_status(r))
                self._poll_log()
            except Exception:
                pass
            time.sleep(1.5)

    def _poll_log(self):
        today = datetime.now().strftime('%Y%m%d')
        log_file = LOG_DIR / f'node_{today}.log'
        if not log_file.exists():
            return
        try:
            with open(log_file, 'r', encoding='utf-8', errors='replace') as fh:
                fh.seek(self._log_pos)
                chunk = fh.read()
                self._log_pos = fh.tell()
        except Exception:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if line:
                self.root.after(0, lambda l=line: self._append_log_line(l))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    LoggerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
