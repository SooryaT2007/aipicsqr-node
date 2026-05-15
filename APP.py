"""
AIPIXQR Node — APP
==================
Account manager and activity logger for the Photographer Node.

  Account tab — log in / log out photographer accounts.
  Logs tab    — live log viewer; start and stop the Runner from here.

Run the headless worker via Runner.bat or the Start Runner button in the Logs tab.
"""

import json
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

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

BASE_DIR     = Path(__file__).parent
CONFIG_PATH  = BASE_DIR / 'node_config.json'
LOG_DIR      = BASE_DIR / 'logs'
RUNNER_PY    = BASE_DIR / 'Runner.py'
VENV_PYTHONW = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'
API_BASE     = 'https://dashboard.aipicsqr.com'

BG       = '#1c1917'
BG_CARD  = '#292524'
BG_LOG   = '#0c0a09'
FG       = '#e7e5e4'
FG_MUT   = '#78716c'
FG_DIM   = '#44403c'
GREEN    = '#22c55e'
RED      = '#ef4444'
AMBER    = '#f59e0b'
BLUE     = '#3b82f6'
BLUE_D   = '#1d4ed8'


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

def _active_pid() -> str:
    try:
        d = _read_config()
        pid = d.get('active_photographer_id', '') or d.get('photographer_id', '')
        return f'{pid[:8]}…{pid[-4:]}' if len(pid) > 12 else pid
    except Exception:
        return ''


# ── API helpers (stdlib only) ─────────────────────────────────────────────────

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


# ── Runner process helpers ────────────────────────────────────────────────────

def _find_runner():
    if not HAS_PSUTIL:
        return None
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = p.info.get('cmdline') or []
            if any('Runner.py' in c for c in cmd) and any('python' in c.lower() for c in cmd):
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def _launch_runner():
    pythonw = VENV_PYTHONW if VENV_PYTHONW.exists() else Path(sys.executable).parent / 'pythonw.exe'
    if not Path(pythonw).exists():
        raise FileNotFoundError(f'pythonw not found: {pythonw}\nRun Installer.bat to set up the venv.')
    if not RUNNER_PY.exists():
        raise FileNotFoundError(f'Runner.py not found: {RUNNER_PY}')
    # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP makes the child independent.
    # DEVNULL for stdio avoids inheriting APP.py's null handles (pythonw has no console)
    # which could cause the runner's logger StreamHandler to fail on startup.
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | 0x00000200  # CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(pythonw), str(RUNNER_PY)],
        cwd=str(BASE_DIR),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def _kill_runner():
    p = _find_runner()
    if p:
        try:
            p.terminate()
        except Exception:
            pass


# ── App ───────────────────────────────────────────────────────────────────────

class NodeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('AIPIXQR Node')
        self.root.geometry('740x520')
        self.root.minsize(620, 420)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(str(BASE_DIR / 'icon.ico'))
        except Exception:
            pass

        self._log_pos        = 0
        self._runner_running = False

        self._apply_style()
        self._build_header()

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._tab_account = tk.Frame(nb, bg=BG)
        self._tab_logs    = tk.Frame(nb, bg=BG)
        nb.add(self._tab_account, text='  Account  ')
        nb.add(self._tab_logs,    text='  Logs  ')

        self._build_account_tab()
        self._build_logs_tab()

        threading.Thread(target=self._bg_loop, daemon=True).start()

    # ── Style / Header ────────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style()
        try:
            s.theme_use('clam')
        except Exception:
            pass
        s.configure('TNotebook',     background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', background=BG_CARD, foreground=FG_MUT,
                    padding=[16, 8], font=('Segoe UI', 10))
        s.map('TNotebook.Tab',       background=[('selected', BG)],
                                     foreground=[('selected', FG)])

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=BG, pady=14)
        hdr.pack(fill=tk.X, padx=16)
        tk.Label(hdr, text='AIPIXQR Node', bg=BG, fg=FG,
                 font=('Segoe UI', 16, 'bold')).pack(side=tk.LEFT)
        self._hdr_status = tk.Label(hdr, text='', bg=BG, fg=FG_MUT,
                                     font=('Segoe UI', 9))
        self._hdr_status.pack(side=tk.RIGHT)

    # ── Account tab ───────────────────────────────────────────────────────────

    def _build_account_tab(self):
        f = self._tab_account

        left = tk.Frame(f, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 4), pady=12)

        tk.Label(left, text='Logged-in Photographers', bg=BG, fg=FG,
                 font=('Segoe UI', 11, 'bold')).pack(anchor=tk.W)
        tk.Label(left, text='▶ active  ·  right-click for full ID',
                 bg=BG, fg=FG_MUT, font=('Segoe UI', 8)).pack(anchor=tk.W, pady=(2, 8))

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
        self._mk_btn(btn_row, 'Logout', self._logout_one, fg=RED).pack(side=tk.LEFT)
        self._mk_btn(left, 'Logout All', self._logout_all, fg=RED).pack(
            anchor=tk.W, pady=(10, 0))

        right = tk.Frame(f, bg=BG_CARD, bd=0)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 12), pady=12, ipadx=12, ipady=12)
        right.pack_propagate(False)
        right.configure(width=300)

        tk.Label(right, text='Login via Photographer ID', bg=BG_CARD, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W, padx=10, pady=(12, 2))
        tk.Label(right, text='dashboard.aipicsqr.com → Nodes → Copy ID',
                 bg=BG_CARD, fg=FG_MUT, font=('Segoe UI', 8)).pack(anchor=tk.W, padx=10)

        self._id_entry = tk.Entry(right, font=('Consolas', 9),
                                   bg='#0c0a09', fg=FG, insertbackground=FG,
                                   relief=tk.FLAT, bd=1)
        self._id_entry.pack(fill=tk.X, padx=10, pady=(8, 4))
        self._id_entry.bind('<Return>', lambda _: self._login_via_id())
        self._mk_btn(right, 'Login with Photographer ID', self._login_via_id,
                     bg=BLUE_D, fg='white').pack(fill=tk.X, padx=10, pady=(0, 16))

        tk.Frame(right, bg=FG_DIM, height=1).pack(fill=tk.X, padx=10, pady=(0, 12))

        tk.Label(right, text='Quick Connect  (6-digit code)', bg=BG_CARD, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W, padx=10, pady=(0, 2))
        tk.Label(right,
                 text='Open your dashboard → Nodes page and\n'
                      'enter the Quick Connect code shown there.\n'
                      'Code refreshes every 3 minutes.',
                 bg=BG_CARD, fg=FG_MUT, font=('Segoe UI', 8),
                 justify=tk.LEFT).pack(anchor=tk.W, padx=10, pady=(0, 10))

        otp_row = tk.Frame(right, bg=BG_CARD)
        otp_row.pack(fill=tk.X, padx=10)

        self._otp_var = tk.StringVar()
        self._otp_var.trace_add('write', self._on_otp_type)
        otp_e = tk.Entry(otp_row, textvariable=self._otp_var,
                         font=('Consolas', 22), width=7, justify=tk.CENTER,
                         bg='#0c0a09', fg=FG, insertbackground=FG, relief=tk.FLAT, bd=1)
        otp_e.pack(side=tk.LEFT)
        otp_e.bind('<Return>', lambda _: self._login_via_otp())

        self._btn_otp = tk.Button(otp_row, text='Login', command=self._login_via_otp,
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
            f'Active photographer:\n{pid[:8]}…{pid[-4:]}\n\nRestart the Runner to apply.')

    def _logout_one(self):
        pid = self._selected_pid()
        if not pid:
            return
        if not messagebox.askyesno('Logout', f'Log out {pid[:8]}…{pid[-4:]}?'):
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
                'Remove all logged-in photographers and node credentials?'):
            return
        _save_ids([], '', {})
        self._refresh_lb()

    def _on_otp_type(self, *_):
        raw = self._otp_var.get()
        clean = ''.join(c for c in raw if c.isdigit())[:6]
        if clean != raw:
            self._otp_var.set(clean)
        self._btn_otp.configure(state=tk.NORMAL if len(clean) == 6 else tk.DISABLED)

    def _login_via_id(self):
        pid = self._id_entry.get().strip()
        if not pid:
            messagebox.showwarning('Empty', 'Enter a Photographer ID.')
            return
        self._do_login(pid)

    def _login_via_otp(self):
        otp = self._otp_var.get().strip()
        if len(otp) != 6:
            return
        def task():
            try:
                pid = _resolve_otp(otp)
                self.root.after(0, lambda: self._do_login(pid, clear_otp=True))
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror('Invalid Code', str(e)))
            except ConnectionError as e:
                self.root.after(0, lambda: messagebox.showerror('Connection Error', str(e)))
        threading.Thread(target=task, daemon=True).start()

    def _do_login(self, photographer_id: str, clear_otp: bool = False):
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
                    messagebox.showinfo('Logged in!',
                        f'Logged in as:\n{photographer_id[:8]}…{photographer_id[-4:]}\n\n'
                        'Start the Runner (Runner.bat) to begin processing photos.')
                self.root.after(0, finish)
            except (ValueError, ConnectionError) as e:
                self.root.after(0, lambda: messagebox.showerror('Failed', str(e)))
        threading.Thread(target=task, daemon=True).start()

    # ── Logs tab ──────────────────────────────────────────────────────────────

    def _build_logs_tab(self):
        f = self._tab_logs

        bar = tk.Frame(f, bg=BG_CARD, pady=6)
        bar.pack(fill=tk.X)

        self._btn_start = self._log_btn(bar, '▶  Start Runner', self._start_runner,
                                         '#14532d', 'white')
        self._btn_start.pack(side=tk.LEFT, padx=(10, 4))

        self._btn_stop = self._log_btn(bar, '■  Stop Runner', self._stop_runner,
                                        '#7f1d1d', 'white')
        self._btn_stop.pack(side=tk.LEFT, padx=(0, 4))
        self._btn_stop.configure(state=tk.DISABLED)

        self._log_btn(bar, 'Clear', self._clear_log, BG_CARD, FG_MUT).pack(
            side=tk.RIGHT, padx=10)

        self._auto_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text='Auto-scroll', variable=self._auto_scroll,
                       bg=BG_CARD, fg=FG_MUT, selectcolor=BG_CARD,
                       activebackground=BG_CARD, activeforeground=FG,
                       font=('Segoe UI', 9)).pack(side=tk.RIGHT, padx=4)

        wrap = tk.Frame(f, bg=BG_LOG, bd=1, relief=tk.SUNKEN)
        wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 4))

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

    def _log_btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief=tk.FLAT, padx=10, pady=4,
                         font=('Segoe UI', 9, 'bold'), cursor='hand2',
                         activebackground=bg, activeforeground=fg)

    def _start_runner(self):
        pid = _active_pid()
        if not pid:
            messagebox.showwarning('No Account',
                'No photographer logged in. Use the Account tab to log in first.')
            return
        try:
            _launch_runner()
            self._append_log(
                f'[APP] Runner start requested at {datetime.now().strftime("%H:%M:%S")}',
                FG_MUT)
        except Exception as e:
            messagebox.showerror('Failed to start Runner', str(e))

    def _stop_runner(self):
        _kill_runner()
        self._append_log(f'[APP] Runner stop requested at {datetime.now().strftime("%H:%M:%S")}',
                         FG_MUT)

    def _clear_log(self):
        self._log_txt.configure(state=tk.NORMAL)
        self._log_txt.delete('1.0', tk.END)
        self._log_txt.configure(state=tk.DISABLED)
        self._log_pos = 0

    def _append_log(self, line: str, color: str | None = None):
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
        if ' │ DEBUG  ' in line:
            return
        if ' │ WARNING' in line or ' │ WARN   ' in line:
            self._append_log(line, AMBER)
        elif ' │ ERROR  ' in line or ' │ CRITICA' in line:
            self._append_log(line, RED)
        elif 'OK ' in line or 'started' in line.lower() or 'complete' in line.lower():
            self._append_log(line, GREEN)
        else:
            self._append_log(line)

    # ── Background loop ───────────────────────────────────────────────────────

    def _bg_loop(self):
        while True:
            try:
                running = _find_runner() is not None
                if running != self._runner_running:
                    self._runner_running = running
                    self.root.after(0, lambda r=running: self._update_status(r))
                self._poll_log()
            except Exception:
                pass
            time.sleep(1.5)

    def _update_status(self, running: bool):
        if running:
            self._hdr_status.configure(text='● Runner Running', fg=GREEN)
            self._btn_start.configure(state=tk.DISABLED)
            self._btn_stop.configure(state=tk.NORMAL)
        else:
            pid = _active_pid()
            label = f'● Runner Stopped  ·  {pid}' if pid else '● Runner Stopped'
            self._hdr_status.configure(text=label, fg=FG_MUT)
            self._btn_start.configure(state=tk.NORMAL)
            self._btn_stop.configure(state=tk.DISABLED)

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
    NodeApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
