"""
Persistent SQLite store for upload tracking.
Replaces the in-memory _processed_paths set in watcher.py so uploads survive
Runner restarts and duplicate files are skipped across sessions.
"""

import json
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
        self._conn.execute('PRAGMA synchronous=NORMAL')  # safe with WAL, faster than FULL
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
        # Migration: add batch_data column for r2_done recovery (safe on existing DBs)
        try:
            self._conn.execute('ALTER TABLE uploaded_files ADD COLUMN batch_data TEXT')
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

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
        """True if this file is complete or safely in R2 (will be recovered on startup)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM uploaded_files WHERE file_path=? AND file_hash=?",
                (file_path, file_hash),
            ).fetchone()
            return row is not None and row[0] in ('complete', 'r2_done')

    def is_processed_path(self, file_path: str) -> bool:
        """True if this path is complete or in R2 — prevents re-queuing on restart."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM uploaded_files WHERE file_path=?",
                (file_path,),
            ).fetchone()
            return row is not None and row[0] in ('complete', 'r2_done')

    def get_r2_done_entries(self) -> list[dict]:
        """Return all r2_done files with stored batch data for notification recovery."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path, batch_data FROM uploaded_files "
                "WHERE status='r2_done' AND batch_data IS NOT NULL"
            ).fetchall()
        result = []
        for file_path, batch_data_str in rows:
            try:
                d = json.loads(batch_data_str)
                d['_file_path'] = file_path
                result.append(d)
            except Exception:
                pass
        return result

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
                   WHERE uploaded_files.status NOT IN ('complete', 'r2_done')""",
                (file_path, file_hash, folder_id, event_id, time.time()),
            )
            self._conn.commit()

    def mark_r2_done(self, file_path: str, photo_id: Optional[str] = None,
                     file_size_bytes: int = 0, width: int = 0, height: int = 0,
                     thumbnail_key: Optional[str] = None):
        """Mark file as uploaded to R2. Stores batch data so it can be recovered if
        the process exits before the batch notification API call completes."""
        batch_data = json.dumps({
            'photo_id':        photo_id,
            'file_size_bytes': file_size_bytes,
            'width':           width,
            'height':          height,
            'thumbnail_key':   thumbnail_key,
        }) if photo_id else None
        with self._lock:
            self._conn.execute(
                "UPDATE uploaded_files SET status='r2_done', photo_id=?, batch_data=? WHERE file_path=?",
                (photo_id, batch_data, file_path),
            )
            self._conn.commit()

    def mark_complete(self, file_path: str, photo_id: Optional[str] = None):
        with self._lock:
            self._conn.execute(
                """UPDATE uploaded_files
                   SET status='complete', photo_id=?, batch_data=NULL, completed_at=?
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
            self._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')  # flush WAL before close
            self._conn.close()
