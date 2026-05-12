"""
Folder Watcher Service
======================
Watches designated folders for new image files and triggers
the upload pipeline when new photos are detected.

Uses watchdog for efficient filesystem monitoring with debouncing
to handle rapid file writes (e.g., camera tethering via FTP).
"""

import time
import logging
import threading
from pathlib import Path
from typing import Callable, List
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

logger = logging.getLogger('AIPICSQR-node')

# Supported image extensions
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.webp', '.heic', '.raw', '.cr2', '.nef', '.arw'}

# Debounce period (seconds) - wait for file to finish writing
DEBOUNCE_SECONDS = 2.0


class PhotoHandler(FileSystemEventHandler):
    """Handles new photo file events with debouncing."""

    def __init__(self, on_new_photo: Callable[[str], None]):
        super().__init__()
        self.on_new_photo = on_new_photo
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._debounce_thread = threading.Thread(target=self._debounce_loop, daemon=True)
        self._debounce_thread.start()

    def on_created(self, event: FileCreatedEvent):
        """Called when a new file is created in the watched folder."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return

        # Add to pending with timestamp for debouncing
        with self._lock:
            self._pending[str(file_path)] = time.time()
            logger.debug(f"New file detected: {file_path.name}")

    def _debounce_loop(self):
        """Process files after debounce period to ensure they're fully written."""
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
                    # Verify file still exists and has non-zero size
                    p = Path(path)
                    if p.exists() and p.stat().st_size > 0:
                        logger.info(f"ðŸ“¸ Processing: {p.name}")
                        self.on_new_photo(path)
                    else:
                        logger.warning(f"File disappeared or empty: {p.name}")
                except Exception as e:
                    logger.error(f"Error processing {path}: {e}")


class FolderWatcher:
    """
    Watches multiple folders for new image files.
    
    Usage:
        watcher = FolderWatcher(
            folders=['/path/to/photos'],
            on_new_photo=my_callback
        )
        watcher.start()
    """

    def __init__(self, folders: List[str], on_new_photo: Callable[[str], None]):
        self.folders = folders
        self.on_new_photo = on_new_photo
        self._observer = Observer()
        self._handler = PhotoHandler(on_new_photo)

    def start(self):
        """Start watching all configured folders."""
        for folder in self.folders:
            folder_path = Path(folder)
            if not folder_path.exists():
                logger.warning(f"Watch folder does not exist, creating: {folder}")
                folder_path.mkdir(parents=True, exist_ok=True)

            self._observer.schedule(self._handler, str(folder_path), recursive=True)
            logger.info(f"  ðŸ‘ï¸ Watching: {folder}")

        self._observer.start()

    def stop(self):
        """Stop the folder watcher."""
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info("Folder Watcher stopped")
