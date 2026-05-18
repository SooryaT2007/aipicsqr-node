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

MAX_RETRIES = 5


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
        # Safe migrations for columns added after initial release
        for stmt in (
            'ALTER TABLE uploaded_files ADD COLUMN batch_data TEXT',
            'ALTER TABLE uploaded_files ADD COLUMN retry_count INTEGER DEFAULT 0',
        ):
            try:
                self._conn.execute(stmt)
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

    def is_processed(self, file_path: str, file_hash: str,
                     folder_id: Optional[str] = None) -> bool:
        """True if this file is complete, safely in R2, or has exhausted retries.

        When folder_id is supplied the record must belong to the same folder.
        A different folder_id means the user deleted and re-added the folder, so
        the file should be re-uploaded even if the content hash is identical.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT status, COALESCE(retry_count, 0), folder_id FROM uploaded_files "
                "WHERE file_path=? AND file_hash=?",
                (file_path, file_hash),
            ).fetchone()
            if row is None:
                return False
            status, retries, stored_folder_id = row
            if folder_id and stored_folder_id and folder_id != stored_folder_id:
                return False  # different folder → treat as new upload
            return status in ('complete', 'r2_done') or (status == 'failed' and retries >= MAX_RETRIES)

    def is_processed_path(self, file_path: str) -> bool:
        """True if this path is complete, in R2, or has exhausted retries."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, COALESCE(retry_count, 0) FROM uploaded_files WHERE file_path=?",
                (file_path,),
            ).fetchone()
            if row is None:
                return False
            status, retries = row
            return status in ('complete', 'r2_done') or (status == 'failed' and retries >= MAX_RETRIES)

    def get_processed_paths(self, folder_id: Optional[str] = None) -> set:
        """Return file paths that should be skipped during an initial scan.

        Scoped to folder_id when provided: a file is only skipped if it was
        already processed for THIS folder. When a folder is deleted and re-added
        a new folder_id UUID is issued, so its files are not in this set and will
        be re-uploaded — even if the same paths were processed for a prior folder.

        Falls back to unscoped (path-only) when folder_id is None so callers that
        don't have folder context still work correctly.
        """
        with self._lock:
            if folder_id:
                rows = self._conn.execute(
                    "SELECT file_path FROM uploaded_files "
                    "WHERE folder_id=? "
                    "  AND (status IN ('complete', 'r2_done') "
                    "       OR (status='failed' AND COALESCE(retry_count, 0) >= ?))",
                    (folder_id, MAX_RETRIES),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT file_path FROM uploaded_files "
                    "WHERE status IN ('complete', 'r2_done') "
                    "   OR (status='failed' AND COALESCE(retry_count, 0) >= ?)",
                    (MAX_RETRIES,),
                ).fetchall()
        return {row[0] for row in rows}

    def get_exhausted_paths(self) -> list:
        """Return file paths that failed more than MAX_RETRIES times — for startup logging."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path, COALESCE(retry_count, 0) FROM uploaded_files "
                "WHERE status='failed' AND COALESCE(retry_count, 0) >= ?",
                (MAX_RETRIES,),
            ).fetchall()
        return [{'file_path': r[0], 'retry_count': r[1]} for r in rows]

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
                           attempted_at=excluded.attempted_at,
                           retry_count=0
                   WHERE uploaded_files.status NOT IN ('complete', 'r2_done')
                      OR (excluded.folder_id IS NOT NULL
                          AND COALESCE(uploaded_files.folder_id, '') != excluded.folder_id)""",
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
                "UPDATE uploaded_files "
                "SET status='failed', retry_count=COALESCE(retry_count, 0) + 1 "
                "WHERE file_path=?",
                (file_path,),
            )
            self._conn.commit()

    def cleanup_old_records(self, days: int = 60) -> int:
        """Delete 'complete' records whose file no longer exists on disk and are
        older than `days` days. Keeps the DB from growing unbounded on large shoots.
        Returns the number of rows deleted."""
        cutoff = time.time() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path FROM uploaded_files "
                "WHERE status='complete' AND completed_at < ?",
                (cutoff,),
            ).fetchall()
        # Check disk existence outside the lock to avoid holding it during I/O
        stale = [r[0] for r in rows if not os.path.exists(r[0])]
        if not stale:
            return 0
        with self._lock:
            self._conn.executemany(
                "DELETE FROM uploaded_files WHERE file_path=?",
                [(p,) for p in stale],
            )
            self._conn.commit()
        return len(stale)

    def close(self):
        with self._lock:
            self._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')  # flush WAL before close
            self._conn.close()
