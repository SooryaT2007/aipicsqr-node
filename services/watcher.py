"""
Folder Watcher Service
======================
Watches folders for new image files and triggers the upload pipeline.

Folders are managed dynamically: the main loop calls sync_folders() every
30 s with the current list from the API, adding/removing watchers as needed.

Each folder entry carries: {id, event_id, path, pool_type, watch_until}

New files are submitted to UploadQueue at priority 0 (immediate).
Initial-scan backlog files are submitted at priority 1.
import_once one-time dumps are submitted at priority 2.
"""

import os
import time
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from services.upload_queue import UploadQueue, UploadTask
from services.upload_state import UploadStateDB

logger = logging.getLogger('AIPICSQR-node')

IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.tiff', '.tif',
    '.bmp', '.webp', '.heic', '.raw', '.cr2', '.nef', '.arw',
}
DEBOUNCE_SECONDS = 2.0


class PhotoHandler(FileSystemEventHandler):
    """Handles new photo file events with debouncing."""

    def __init__(self, on_new_file: Callable[[str], None]):
        super().__init__()
        self._on_new_file = on_new_file
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._debounce_thread = threading.Thread(target=self._debounce_loop, daemon=True)
        self._debounce_thread.start()

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        with self._lock:
            self._pending[str(file_path)] = time.time()

    def _debounce_loop(self):
        while True:
            time.sleep(0.5)
            now = time.time()
            ready = []
            with self._lock:
                for path, timestamp in list(self._pending.items()):
                    if now - timestamp >= DEBOUNCE_SECONDS:
                        ready.append(path)
                for path in ready:
                    del self._pending[path]
            for path in ready:
                try:
                    p = Path(path)
                    if p.exists() and p.stat().st_size > 0:
                        logger.info(f'New photo: {p.name}')
                        self._on_new_file(path)
                except Exception as e:
                    logger.error(f'Error dispatching {path}: {e}')


class FolderWatcher:
    """
    Dynamically watches folders for new image files.

    New photos detected by watchdog are submitted to UploadQueue at priority 0.
    Existing photos found during initial scan are submitted at priority 1.
    import_once() one-time dumps are submitted at priority 2.
    """

    def __init__(self,
                 on_new_photo: Callable[[str, dict], None],
                 upload_queue: Optional[UploadQueue] = None,
                 state_db: Optional[UploadStateDB] = None):
        self._on_new_photo = on_new_photo
        self._upload_queue = upload_queue
        self._state_db = state_db
        self._folder_info: dict[str, dict] = {}
        self._watches: dict[str, object] = {}
        self._observer = Observer()
        self._handler = PhotoHandler(self._dispatch)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._observer.start()
        logger.info('Folder Watcher ready (waiting for folders from API)')

    def stop(self):
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info('Folder Watcher stopped')

    # ── Dynamic sync ──────────────────────────────────────────────────────────

    def sync_folders(self, active_folders: list[dict]):
        active_paths = {f['path'] for f in active_folders}
        current_paths = set(self._folder_info.keys())

        for folder in active_folders:
            if folder['path'] not in current_paths:
                self._add_folder(folder)

        for path in current_paths - active_paths:
            self._remove_folder(path)

    # ── One-time import ───────────────────────────────────────────────────────

    def import_once(self, folder_path: str, event_id: str,
                    pool_type: str = 'public', folder_id: Optional[str] = None):
        """
        Scan a folder once and submit all images to the upload queue.
        Does not start watching. Used for the APP.py "Import Folder" feature.
        """
        folder_info = {
            'id':       folder_id,
            'event_id': event_id,
            'path':     folder_path,
            'pool_type': pool_type,
        }
        try:
            files = [
                f for f in Path(folder_path).rglob('*')
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            ]
        except Exception as e:
            logger.warning(f'import_once scan error for {folder_path}: {e}')
            return 0

        count = 0
        for f in files:
            fp = str(f.resolve())
            if self._state_db and self._state_db.is_processed_path(fp):
                continue
            self._submit(fp, folder_info, priority=2)
            count += 1

        logger.info(f'import_once: submitted {count} photo(s) from {Path(folder_path).name}')
        return count

    # ── Internal ──────────────────────────────────────────────────────────────

    def _add_folder(self, folder_info: dict):
        path = folder_info['path']
        folder_path = Path(path)
        if not folder_path.exists():
            try:
                folder_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f'Cannot create folder {path}: {e}')
                return

        watch = self._observer.schedule(self._handler, str(folder_path), recursive=True)
        self._watches[path] = watch
        self._folder_info[path] = folder_info
        logger.info(f'  Watching: {Path(path).name}')
        self._scan_existing(folder_info)

    def _remove_folder(self, path: str):
        watch = self._watches.pop(path, None)
        if watch:
            try:
                self._observer.unschedule(watch)
            except Exception:
                pass
        self._folder_info.pop(path, None)
        logger.info(f'  Stopped watching: {path}')

    def _dispatch(self, file_path: str):
        p = Path(file_path).resolve()
        fp = str(p)

        if self._state_db and self._state_db.is_processed_path(fp):
            return

        for folder_path_str, info in self._folder_info.items():
            try:
                p.relative_to(Path(folder_path_str).resolve())
                self._submit(fp, info, priority=0)
                return
            except ValueError:
                continue

        logger.warning(f'File {p.name} not matched to any tracked folder')
        self._submit(fp, {}, priority=0)

    def _scan_existing(self, folder_info: dict):
        path = folder_info['path']
        try:
            files = [
                f for f in Path(path).rglob('*')
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            ]
        except Exception as e:
            logger.warning(f'Initial scan error for {path}: {e}')
            return

        if not files:
            return

        logger.info(f'  Initial scan: {len(files)} existing photo(s) in {Path(path).name}')

        def _run():
            submitted = 0
            for f in files:
                fp = str(f.resolve())
                if self._state_db and self._state_db.is_processed_path(fp):
                    continue
                self._submit(fp, folder_info, priority=1)
                submitted += 1
            if submitted:
                logger.info(f'  Submitted {submitted} photo(s) to upload queue')

        threading.Thread(target=_run, daemon=True).start()

    def _submit(self, file_path: str, folder_info: dict, priority: int):
        if self._upload_queue:
            self._upload_queue.submit(UploadTask(
                priority=priority,
                file_path=file_path,
                folder_info=folder_info,
            ))
        else:
            # Fallback: direct call (no parallel queue)
            self._on_new_photo(file_path, folder_info)
