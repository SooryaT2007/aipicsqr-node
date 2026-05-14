"""
AIPIXQR Node App
================
Two-tab GUI for managing the photographer node:
  Tab 1 — Activity Log  : live view of scans and uploads (mesh ops hidden)
  Tab 2 — Photographer IDs : link/unlink IDs; Quick Connect via 6-digit OTP

Run this to configure the node.  The headless worker is Run.bat / main.py.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

BASE_DIR    = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'node_config.json'
LOG_DIR     = BASE_DIR / 'logs'
MAIN_PY     = BASE_DIR / 'main.py'
VENV_PYTHONW = BASE_DIR / 'venv' / 'Scripts' / 'pythonw.exe'
API_BASE    = 'https://dashboard.aipicsqr.com'


# ── Config ────────────────────────────────────────────────────────────────────

class NodeConfig:
    def __init__(self):
        self.reload()

    def reload(self):
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text('utf-8'))
            except Exception:
                data = {}
        else:
            data = {}

        self.photographer_ids: list[str] = list(data.get('photographer_ids', []))
        # Legacy single-ID migration
        legacy = data.get('photographer_id', '')
        if legacy and legacy not in self.photographer_ids:
            self.photographer_ids.insert(0, legacy)

        self.active_id: str = data.get('active_photographer_id', '')
        if not self.active_id and self.photographer_ids:
            self.active_id = self.photographer_ids[0]

        self.node_identities: dict = data.get('node_identities', {})
        if legacy and data.get('node_id') and data.get('node_token'):
            if legacy not in self.node_identities:
                self.node_identities[legacy] = {
                    'node_id': data['node_id'],
                    'node_token': data['node_token'],
                }

    def save(self):
        data = {
            'photographer_ids': self.photographer_ids,
            'active_photographer_id': self.active_id,
            'node_identities': self.node_identities,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), 'utf-8')

    def add(self, pid: str):
        if pid not in self.photographer_ids:
            self.photographer_ids.append(pid)
        if not self.active_id:
            self.active_id = pid
        self.save()

    def remove(self, pid: str):
        if pid in self.photographer_ids:
            self.photographer_ids.remove(pid)
        self.node_identities.pop(pid, None)
        if self.active_id == pid:
            self.active_id = self.photographer_ids[0] if self.photographer_ids else ''
        self.save()

    def set_active(self, pid: str):
        if pid in self.photographer_ids:
            self.active_id = pid
            self.save()

    def store_identity(self, pid: str, node_id: str, node_token: str):
        self.node_identities[pid] = {'node_id': node_id, 'node_token': node_token}
        self.save()

    def logout_all(self):
        self.photographer_ids.clear()
        self.active_id = ''
        self.node_identities.clear()
        self.save()

    def label(self, pid: str) -> str:
        marker = '▶ ' if pid == self.active_id else '   '
        return f'{marker}{pid[:8]}…{pid[-4:]}'

    def has_identity(self, pid: str) -> bool:
        return bool(self.node_identities.get(pid, {}).get('node_id'))


# ── Process helpers ───────────────────────────────────────────────────────────

def _find_node_proc():
    if not HAS_PSUTIL:
        return None
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if any('main.py' in c for c in cmdline) and any('python' in c.lower() for c in cmdline):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def _start_node():
    pythonw = VENV_PYTHONW if VENV_PYTHONW.exists() else Path(sys.executable).parent / 'pythonw.exe'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.Popen([str(pythonw), str(MAIN_PY)], cwd=str(BASE_DIR), creationflags=flags)

def _stop_node():
    proc = _find_node_proc()
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass


# ── API helpers ───────────────────────────────────────────────────────────────

def _resolve_otp(otp_code: str) -> str:
    if not HAS_REQUESTS:
        raise RuntimeError('requests not installed — re-run Install.bat')
    resp = requests.post(f'{API_BASE}/api/nodes/resolve-otp', json={'otp_code': otp_code}, timeout=10)
    if resp.status_code == 404:
        raise ValueError('Code not found or expired. Refresh your dashboard for the latest code.')
    resp.raise_for_status()
    pid = resp.json().get('photographer_id', '')
    if not pid:
        raise ValueError('Unexpected server response')
    return pid

def _register_node(photographer_id: str) -> tuple[str, str]:
    if not HAS_REQUESTS:
        raise RuntimeError('requests not installed — re-run Install.bat')
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '127.0.0.1'

    resp = requests.post(
        f'{API_BASE}/api/node/register',
        json={'photographer_id': photographer_id, 'hostname': socket.gethostname(), 'ip_address': local_ip},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    node_id    = data.get('node_id', '')
    node_token = data.get('node_token', '')
    if not node_id or not node_token:
        raise ValueError(data.get('error', 'Registration failed — no token received'))
    return node_id, node_token


# ── Main App ──────────────────────────────────────────────────────────────────

class NodeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('AIPIXQR Node')
        self.root.geometry('780x560')
        self.root.minsize(660, 480)
        try:
            self.root.iconbitmap(str(BASE_DIR / 'icon.ico'))
        except Exception:
            pass

        self.cfg = NodeConfig()
        self._log_pos   = 0
        self._node_was_running = False

        self._apply_style()

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self._frame_log = ttk.Frame(notebook)
        self._frame_ids = ttk.Frame(notebook)
        notebook.add(self._frame_log, text='  Activity Log  ')
        notebook.add(self._frame_ids, text='  Photographer IDs  ')

        self._build_log_tab()
        self._build_ids_tab()
        self._build_status_bar()

        threading.Thread(target=self._bg_loop, daemon=True).start()

    def _apply_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TNotebook.Tab', padding=[14, 7], font=('Segoe UI', 10))
        style.configure('TFrame', background='#f5f5f4')

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg='#e7e5e4', height=28)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._dot_lbl = tk.Label(bar, text='●', fg='#a8a29e', bg='#e7e5e4', font=('Segoe UI', 12))
        self._dot_lbl.pack(side=tk.LEFT, padx=(8, 2))
        self._status_lbl = tk.Label(bar, text='Checking…', bg='#e7e5e4', font=('Segoe UI', 9), anchor='w')
        self._status_lbl.pack(side=tk.LEFT, pady=4)

    def _set_status(self, running: bool):
        if running:
            self._dot_lbl.configure(fg='#22c55e')
            self._status_lbl.configure(text='Node Running')
        else:
            self._dot_lbl.configure(fg='#ef4444')
            self._status_lbl.configure(text='Node Stopped')
        self._btn_start.configure(state=tk.DISABLED if running else tk.NORMAL)
        self._btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)

    # ── Log tab ───────────────────────────────────────────────────────────────

    def _build_log_tab(self):
        f = self._frame_log

        toolbar = tk.Frame(f, bg='#fafaf9', pady=6)
        toolbar.pack(fill=tk.X, padx=8)

        self._btn_start = self._mk_btn(toolbar, '▶  Start Node', self._start_node, '#15803d', 'white')
        self._btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self._btn_stop = self._mk_btn(toolbar, '■  Stop Node', self._stop_node, '#b91c1c', 'white')
        self._btn_stop.pack(side=tk.LEFT)
        self._btn_stop.configure(state=tk.DISABLED)

        self._mk_btn(toolbar, 'Clear', self._clear_log, '#e7e5e4', '#1c1917').pack(side=tk.RIGHT)

        self._auto_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text='Auto-scroll', variable=self._auto_scroll,
                       font=('Segoe UI', 9), bg='#fafaf9').pack(side=tk.RIGHT, padx=8)

        wrap = tk.Frame(f, bd=1, relief=tk.SUNKEN)
        wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 8))

        self._log_txt = tk.Text(
            wrap, wrap=tk.WORD,
            bg='#1c1917', fg='#d6d3d1',
            font=('Consolas', 9),
            state=tk.DISABLED,
            insertbackground='white',
            selectbackground='#374151',
            pady=4, padx=6,
        )
        vsb = tk.Scrollbar(wrap, command=self._log_txt.yview)
        self._log_txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_txt.pack(fill=tk.BOTH, expand=True)

        self._log_txt.tag_configure('warn',  foreground='#fbbf24')
        self._log_txt.tag_configure('error', foreground='#f87171')
        self._log_txt.tag_configure('ok',    foreground='#4ade80')
        self._log_txt.tag_configure('dim',   foreground='#78716c')

    def _mk_btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief=tk.FLAT, padx=10, pady=4,
                         font=('Segoe UI', 9, 'bold'), cursor='hand2',
                         activebackground=bg, activeforeground=fg)

    def _start_node(self):
        if not self.cfg.active_id:
            messagebox.showwarning('No Photographer',
                'Link a Photographer ID first (Photographer IDs tab).')
            return
        if not self.cfg.has_identity(self.cfg.active_id):
            messagebox.showwarning('Not Registered',
                'This photographer ID has not been registered yet.\n'
                'Use the Photographer IDs tab to link it properly.')
            return
        _start_node()
        self._log(f'[App] Node start requested')

    def _stop_node(self):
        _stop_node()
        self._log(f'[App] Node stop requested')

    def _clear_log(self):
        self._log_txt.configure(state=tk.NORMAL)
        self._log_txt.delete('1.0', tk.END)
        self._log_txt.configure(state=tk.DISABLED)
        self._log_pos = 0

    def _log(self, line: str, tag: str = 'dim'):
        self._log_txt.configure(state=tk.NORMAL)
        self._log_txt.insert(tk.END, line + '\n', tag)
        if self._auto_scroll.get():
            self._log_txt.see(tk.END)
        self._log_txt.configure(state=tk.DISABLED)

    def _append_log_line(self, line: str):
        # Skip DEBUG lines — only show INFO+
        if ' │ DEBUG  ' in line:
            return
        tag = 'dim'
        upper = line.upper()
        if ' │ WARNING' in line or ' │ WARN   ' in line:
            tag = 'warn'
        elif ' │ ERROR  ' in line or ' │ CRITICA' in line:
            tag = 'error'
        elif 'OK ' in upper or 'STARTED' in upper or 'COMPLETE' in upper or 'UPLOADED' in upper:
            tag = 'ok'
        self._log(line, tag)

    # ── IDs tab ───────────────────────────────────────────────────────────────

    def _build_ids_tab(self):
        f = self._frame_ids

        # Left: list panel
        left = tk.Frame(f, bg='#fafaf9')
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 4), pady=8)

        tk.Label(left, text='Linked Photographers', font=('Segoe UI', 11, 'bold'),
                 bg='#fafaf9').pack(anchor=tk.W)
        tk.Label(left, text='▶ = active  |  right-click for full ID',
                 font=('Segoe UI', 8), fg='#a8a29e', bg='#fafaf9').pack(anchor=tk.W, pady=(0, 6))

        list_wrap = tk.Frame(left, bd=1, relief=tk.SUNKEN)
        list_wrap.pack(fill=tk.BOTH, expand=True)

        self._lb = tk.Listbox(
            list_wrap, font=('Consolas', 10), activestyle='none',
            selectmode=tk.SINGLE, bg='#1c1917', fg='#d6d3d1',
            selectbackground='#374151', borderwidth=0, highlightthickness=0,
        )
        lsb = tk.Scrollbar(list_wrap, command=self._lb.yview)
        self._lb.configure(yscrollcommand=lsb.set)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb.pack(fill=tk.BOTH, expand=True)
        self._lb.bind('<Button-3>', self._show_full_id)

        btn_row = tk.Frame(left, bg='#fafaf9')
        btn_row.pack(fill=tk.X, pady=(6, 0))
        tk.Button(btn_row, text='Set Active', command=self._set_active,
                  relief=tk.FLAT, padx=8, pady=4, font=('Segoe UI', 9), cursor='hand2',
                  bg='#e7e5e4').pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text='Unlink', command=self._unlink,
                  relief=tk.FLAT, padx=8, pady=4, font=('Segoe UI', 9), cursor='hand2',
                  fg='#ef4444', bg='#fafaf9').pack(side=tk.LEFT)

        tk.Button(left, text='Logout All', command=self._logout_all,
                  relief=tk.FLAT, padx=8, pady=4, font=('Segoe UI', 9),
                  fg='#ef4444', bg='#fafaf9', cursor='hand2').pack(anchor=tk.W, pady=(12, 0))

        # Right: link forms
        right = tk.Frame(f, bg='#f5f5f4', bd=1, relief=tk.GROOVE)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 8), pady=8, ipadx=10, ipady=10)
        right.pack_propagate(False)
        right.configure(width=310)

        # ── Link via ID
        tk.Label(right, text='Link via Photographer ID', font=('Segoe UI', 10, 'bold'),
                 bg='#f5f5f4').pack(anchor=tk.W, padx=10, pady=(10, 2))
        tk.Label(right, text='Find it at: dashboard.aipicsqr.com → Nodes',
                 font=('Segoe UI', 8), fg='#a8a29e', bg='#f5f5f4').pack(anchor=tk.W, padx=10)

        self._id_entry = tk.Entry(right, font=('Consolas', 9))
        self._id_entry.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._id_entry.bind('<Return>', lambda _: self._link_via_id())

        self._mk_btn(right, 'Link Photographer ID', self._link_via_id, '#1d4ed8', 'white').pack(
            fill=tk.X, padx=10, pady=(0, 14))

        ttk.Separator(right, orient='horizontal').pack(fill=tk.X, padx=10, pady=(0, 12))

        # ── Quick Connect OTP
        tk.Label(right, text='Quick Connect  (6-digit code)', font=('Segoe UI', 10, 'bold'),
                 bg='#f5f5f4').pack(anchor=tk.W, padx=10, pady=(0, 2))
        tk.Label(right,
                 text='Open your dashboard → Nodes page\nand enter the Quick Connect code shown there.\nCode changes every 3 minutes.',
                 font=('Segoe UI', 8), fg='#a8a29e', bg='#f5f5f4', justify=tk.LEFT,
                 ).pack(anchor=tk.W, padx=10, pady=(0, 8))

        otp_row = tk.Frame(right, bg='#f5f5f4')
        otp_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        self._otp_var = tk.StringVar()
        self._otp_var.trace_add('write', self._on_otp_change)
        otp_entry = tk.Entry(otp_row, textvariable=self._otp_var,
                             font=('Consolas', 20), width=7, justify=tk.CENTER)
        otp_entry.pack(side=tk.LEFT)
        otp_entry.bind('<Return>', lambda _: self._link_via_otp())

        self._btn_otp = tk.Button(otp_row, text='Link', command=self._link_via_otp,
                                  relief=tk.FLAT, padx=12, pady=6,
                                  font=('Segoe UI', 9, 'bold'),
                                  bg='#059669', fg='white', cursor='hand2',
                                  activebackground='#047857', activeforeground='white',
                                  state=tk.DISABLED)
        self._btn_otp.pack(side=tk.LEFT, padx=(10, 0))

        self._refresh_list()

    def _on_otp_change(self, *_):
        raw = self._otp_var.get()
        clean = ''.join(c for c in raw if c.isdigit())[:6]
        if clean != raw:
            self._otp_var.set(clean)
        self._btn_otp.configure(state=tk.NORMAL if len(clean) == 6 else tk.DISABLED)

    def _refresh_list(self):
        self.cfg.reload()
        self._lb.delete(0, tk.END)
        for i, pid in enumerate(self.cfg.photographer_ids):
            self._lb.insert(tk.END, self.cfg.label(pid))
            color = '#4ade80' if pid == self.cfg.active_id else '#d6d3d1'
            self._lb.itemconfig(i, fg=color)

    def _show_full_id(self, event):
        idx = self._lb.nearest(event.y)
        if 0 <= idx < len(self.cfg.photographer_ids):
            pid = self.cfg.photographer_ids[idx]
            messagebox.showinfo('Full Photographer ID', pid)

    def _selected_pid(self) -> str | None:
        sel = self._lb.curselection()
        if not sel:
            return None
        idx = sel[0]
        if idx >= len(self.cfg.photographer_ids):
            return None
        return self.cfg.photographer_ids[idx]

    def _set_active(self):
        pid = self._selected_pid()
        if not pid:
            return
        self.cfg.set_active(pid)
        self._refresh_list()
        messagebox.showinfo('Active Set',
            f'Active photographer:\n{pid[:8]}…{pid[-4:]}\n\nRestart the node to apply.')

    def _unlink(self):
        pid = self._selected_pid()
        if not pid:
            return
        if not messagebox.askyesno('Unlink', f'Remove {pid[:8]}…{pid[-4:]}?'):
            return
        self.cfg.remove(pid)
        self._refresh_list()

    def _logout_all(self):
        if not self.cfg.photographer_ids:
            return
        if not messagebox.askyesno('Logout All', 'Remove all linked photographers and node credentials?'):
            return
        self.cfg.logout_all()
        self._refresh_list()

    def _link_via_id(self):
        pid = self._id_entry.get().strip()
        if not pid:
            messagebox.showwarning('Empty', 'Enter a Photographer ID.')
            return
        self._do_link(pid)

    def _link_via_otp(self):
        otp = self._otp_var.get().strip()
        if len(otp) != 6 or not otp.isdigit():
            return
        def task():
            try:
                pid = _resolve_otp(otp)
                self.root.after(0, lambda: self._do_link(pid, clear_otp=True))
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror('Invalid Code', str(e)))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror('Error', str(e)))
        threading.Thread(target=task, daemon=True).start()

    def _do_link(self, photographer_id: str, clear_otp: bool = False):
        def task():
            try:
                node_id, node_token = _register_node(photographer_id)
                def finish():
                    self.cfg.add(photographer_id)
                    self.cfg.store_identity(photographer_id, node_id, node_token)
                    self.cfg.set_active(photographer_id)
                    self._id_entry.delete(0, tk.END)
                    if clear_otp:
                        self._otp_var.set('')
                    self._refresh_list()
                    messagebox.showinfo('Linked!',
                        f'Node linked to:\n{photographer_id[:8]}…{photographer_id[-4:]}\n\n'
                        'Press "Start Node" to begin processing photos.')
                self.root.after(0, finish)
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror('Registration Failed', str(e)))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror('Connection Error', str(e)))
        threading.Thread(target=task, daemon=True).start()

    # ── Background refresh loop ───────────────────────────────────────────────

    def _bg_loop(self):
        while True:
            try:
                self._poll_status()
                self._poll_log()
            except Exception:
                pass
            time.sleep(1.5)

    def _poll_status(self):
        running = _find_node_proc() is not None
        if running != self._node_was_running:
            self._node_was_running = running
            self.root.after(0, lambda r=running: self._set_status(r))

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
        if not chunk:
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
