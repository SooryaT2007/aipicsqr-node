"""
Folder Watcher Service
======================
Watches folders for new image files and triggers the upload pipeline.

Folders are managed dynamically: the main loop calls sync_folders() every
30 s with the current list from the API, adding/removing watchers as needed.

Each folder entry carries: {id, event_id, path, pool_type, watch_until}
The pool_type is forwarded to the uploader so it can decide whether to run
face recognition (public pool skips it).
"""

import time
import logging
import threading
from pathlib import Path
from typing import Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

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
            logger.debug(f'New file detected: {file_path.name}')

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
                        logger.info(f'📸 Processing: {p.name}')
                        self._on_new_file(path)
                    else:
                        logger.warning(f'File disappeared or empty: {p.name}')
                except Exception as e:
                    logger.error(f'Error processing {path}: {e}')


class FolderWatcher:
    """
    Dynamically watches folders for new image files.

    Usage:
        watcher = FolderWatcher(on_new_photo=uploader.process_photo)
        watcher.start()                          # starts the observer thread
        watcher.sync_folders(folders_from_api)  # call every 30 s
        watcher.stop()
    """

    def __init__(self, on_new_photo: Callable[[str, dict], None]):
        # on_new_photo(file_path: str, folder_info: dict) -> None
        self._on_new_photo = on_new_photo
        self._folder_info: dict[str, dict] = {}   # path -> folder entry dict
        self._watches: dict[str, object] = {}     # path -> watchdog Watch
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
        """
        Called every 30 s with the current folder list from the API.
        Adds any new paths and removes any that are no longer active.
        """
        active_paths = {f['path'] for f in active_folders}
        current_paths = set(self._folder_info.keys())

        for folder in active_folders:
            if folder['path'] not in current_paths:
                self._add_folder(folder)

        for path in current_paths - active_paths:
            self._remove_folder(path)

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
        logger.info(f'  👁 Watching [{folder_info["pool_type"]}]: {path}')

    def _remove_folder(self, path: str):
        watch = self._watches.pop(path, None)
        if watch:
            try:
                self._observer.unschedule(watch)
            except Exception:
                pass
        self._folder_info.pop(path, None)
        logger.info(f'  ✋ Stopped watching: {path}')

    def _dispatch(self, file_path: str):
        """Find which tracked folder owns this file and call the callback."""
        p = Path(file_path).resolve()
        for folder_path_str, info in self._folder_info.items():
            try:
                p.relative_to(Path(folder_path_str).resolve())
                self._on_new_photo(file_path, info)
                return
            except ValueError:
                continue
        # Fallback: unknown folder — still call with empty info
        logger.warning(f'File {p.name} not matched to any tracked folder')
        self._on_new_photo(file_path, {})
