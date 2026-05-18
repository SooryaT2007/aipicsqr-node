"""
Persistent SQLite store for upload tracking.
Replaces the in-memory _processed_paths set in watcher.py so uploads survive
Runner restarts and duplicate files are skipped across sessions.
"""

import os
import sqlite3
import threading
import hashlib
import time
from typing import Optional


class UploadStateDB:
    def __init__(self, db_path: str):
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._create_schema()

    def _create_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS uploaded_files (
                file_path    TEXT PRIMARY KEY,
                file_hash    TEXT NOT NULL,
                photo_id     TEXT,
                status       TEXT DEFAULT 'pending',
                folder_id    TEXT,
                event_id     TEXT,
                attempted_at REAL,
                completed_at REAL
            );
        """)
        self._conn.commit()

    # ── File hash ────────────────────────────────────────────────────────────

    @staticmethod
    def compute_hash(file_path: str) -> Optional[str]:
        """MD5 of the first 64 KB — fast enough for dedup, not cryptographic."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read(65536)).hexdigest()
        except OSError:
            return None

    # ── Queries ──────────────────────────────────────────────────────────────

    def is_processed(self, file_path: str, file_hash: str) -> bool:
        """Return True if this exact file (path + hash) completed successfully."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM uploaded_files WHERE file_path=? AND file_hash=?",
                (file_path, file_hash),
            ).fetchone()
            return row is not None and row[0] == 'complete'

    def is_processed_path(self, file_path: str) -> bool:
        """Fast in-session check: True if this path completed in any session."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM uploaded_files WHERE file_path=?",
                (file_path,),
            ).fetchone()
            return row is not None and row[0] == 'complete'

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_pending(self, file_path: str, file_hash: str,
                     folder_id: Optional[str] = None, event_id: Optional[str] = None):
        with self._lock:
            self._conn.execute(
                """INSERT INTO uploaded_files
                       (file_path, file_hash, status, folder_id, event_id, attempted_at)
                   VALUES (?, ?, 'pending', ?, ?, ?)
                   ON CONFLICT(file_path) DO UPDATE
                       SET file_hash=excluded.file_hash,
                           status='pending',
                           folder_id=excluded.folder_id,
                           event_id=excluded.event_id,
                           attempted_at=excluded.attempted_at
                   WHERE uploaded_files.status != 'complete'""",
                (file_path, file_hash, folder_id, event_id, time.time()),
            )
            self._conn.commit()

    def mark_r2_done(self, file_path: str):
        with self._lock:
            self._conn.execute(
                "UPDATE uploaded_files SET status='r2_done' WHERE file_path=?",
                (file_path,),
            )
            self._conn.commit()

    def mark_complete(self, file_path: str, photo_id: Optional[str] = None):
        with self._lock:
            self._conn.execute(
                """UPDATE uploaded_files
                   SET status='complete', photo_id=?, completed_at=?
                   WHERE file_path=?""",
                (photo_id, time.time(), file_path),
            )
            self._conn.commit()

    def mark_failed(self, file_path: str):
        with self._lock:
            self._conn.execute(
                "UPDATE uploaded_files SET status='failed' WHERE file_path=?",
                (file_path,),
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
