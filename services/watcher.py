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

import os
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
        self._processed_paths: set[str] = set()   # dedup within a session
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

        # Process any photos already in the folder
        self._scan_existing(folder_info)

    def _remove_folder(self, path: str):
        watch = self._watches.pop(path, None)
        if watch:
            try:
                self._observer.unschedule(watch)
            except Exception:
                pass
        self._folder_info.pop(path, None)
        # Clear processed-path entries for this folder so that re-adding the
        # folder later causes all existing files to be re-uploaded.
        prefix = str(Path(path).resolve()).rstrip(os.sep) + os.sep
        self._processed_paths = {fp for fp in self._processed_paths if not fp.startswith(prefix)}
        logger.info(f'  ✋ Stopped watching: {path}')

    def _dispatch(self, file_path: str):
        """Find which tracked folder owns this file and call the callback."""
        p = Path(file_path).resolve()
        fp = str(p)
        if fp in self._processed_paths:
            return  # already picked up by initial scan
        for folder_path_str, info in self._folder_info.items():
            try:
                p.relative_to(Path(folder_path_str).resolve())
                self._processed_paths.add(fp)
                self._on_new_photo(file_path, info)
                return
            except ValueError:
                continue
        logger.warning(f'File {p.name} not matched to any tracked folder')
        self._on_new_photo(file_path, {})

    def _scan_existing(self, folder_info: dict):
        """
        Process images already in the folder when it is first added.
        Runs in a single background thread (sequential) to avoid hammering
        the upload pipeline with hundreds of concurrent threads.
        """
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
            failed = []
            for f in files:
                fp = str(f.resolve())
                if fp in self._processed_paths:
                    continue
                self._processed_paths.add(fp)
                try:
                    self._on_new_photo(str(f), folder_info)
                except Exception as e:
                    logger.warning(f'  Scan failed for {f.name}: {e}')
                    failed.append(f)

            # Retry failed photos once after a short wait (handles startup DNS lag)
            if failed:
                logger.info(f'  Retrying {len(failed)} failed photo(s) in 15 s...')
                time.sleep(15)
                for f in failed:
                    try:
                        self._on_new_photo(str(f), folder_info)
                    except Exception as e:
                        logger.error(f'  Retry failed for {f.name}: {e}')

        threading.Thread(target=_run, daemon=True).start()
