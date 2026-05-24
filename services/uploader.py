"""
Photo Uploader Service
======================
Pipeline for processing a new photo:
1. Dedup: check SQLite state DB — skip if already uploaded this session or before
2. Compress the image + generate thumbnail (Pillow)
3. Get presigned R2 URLs from the API (main image + thumbnail)
4. PUT both files directly to R2 (bypasses our server — no Vercel bandwidth used)
5. Mark r2_done in SQLite (stores batch metadata for crash recovery)
6. Accumulate completions in a batch; flush every 10 photos or 5 seconds via the
   batch API endpoint (reduces Vercel invocations ~50x during bulk dumps)

The drive-sync cron on Vercel handles moving main photos from R2 to Google Drive
for GDrive-destination folders.  Thumbnails stay on R2 for 90 days.

Crash recovery: if the process exits after step 4 but before the batch
notification completes, recover_r2_done() re-sends those notifications on the
next startup without re-compressing or re-uploading anything.

Face detection: for trickle photos (priority=0, queue depth < 6) it runs
inline here and face vectors are sent with the batch notification.  For all
other photos (import-once, backlog) a vectoring_job is created and the mesh
node that picks it up runs detection independently.

folder_info dict: {id, event_id, path, pool_type, watch_until}
"""

import concurrent.futures
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# Photos arriving via watchdog with fewer than this many items pending in the
# upload queue are candidates for local (inline) vectorization — no mesh job
# created, no R2 re-download required.
_TRICKLE_THRESHOLD = 6

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from services.compressor import compress_image_with_thumbnail
from services.upload_state import UploadStateDB

_r2_session = requests.Session()
_r2_session.mount('https://', HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1.5,
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods=frozenset({'PUT'}),
    raise_on_status=False,
)))

logger = logging.getLogger('AIPICSQR-node')


class PhotoUploader:
    def __init__(self, config, api_client, vision_manager=None,
                 state_db: Optional[UploadStateDB] = None,
                 upload_queue=None, resource_monitor=None):
        self.config = config
        self.api = api_client
        self._state_db = state_db
        self._vision_manager = vision_manager
        self._resource_monitor = resource_monitor
        self._upload_queue = upload_queue

        self._batch: list[dict] = []
        self._batch_lock = threading.Lock()
        self._last_flush = time.time()
        self._flush_timer: Optional[threading.Timer] = None

        # Track in-flight batch-send threads so stop() can join them
        self._batch_threads: list[threading.Thread] = []
        self._batch_threads_lock = threading.Lock()

        self._start_flush_timer()

    # ── Startup recovery ──────────────────────────────────────────────────────

    def recover_r2_done(self):
        """Re-send batch notifications for files already in R2 that weren't confirmed
        before the last shutdown. Called once at startup, before the watcher runs."""
        if not self._state_db:
            return
        entries = self._state_db.get_r2_done_entries()
        if not entries:
            return
        logger.info(f'Recovery: {len(entries)} file(s) reached R2 but were not confirmed '
                    f'before last shutdown — re-sending notifications now...')
        for e in entries:
            e.pop('thumbnail_url', None)
        size = self.config.upload_batch_size
        for i in range(0, len(entries), size):
            self._send_batch(entries[i:i + size])  # synchronous — no thread
        logger.info('Recovery: complete.')

    # ── Public ────────────────────────────────────────────────────────────────

    def process_photo(self, file_path: str, folder_info: dict | None = None, priority: int = 0):
        path = Path(file_path)
        event_id = (folder_info or {}).get('event_id') or self.config.event_id
        folder_id = (folder_info or {}).get('id')

        if not event_id:
            logger.warning(f'  No event set — skipping {path.name}')
            return

        # Fast dedup: hash first 64 KB and check DB
        if self._state_db:
            file_hash = UploadStateDB.compute_hash(file_path)
            if file_hash and self._state_db.is_processed(file_path, file_hash, folder_id):
                logger.info(f'  Skipping {path.name} — already uploaded')
                return
        else:
            file_hash = None

        try:
            if self._state_db and file_hash:
                self._state_db.mark_pending(file_path, file_hash, folder_id, event_id)

            # STEP 1: Compress + thumbnail
            logger.info(f'  Compressing {path.name}...')
            compressed_bytes, thumbnail_bytes, width, height = compress_image_with_thumbnail(
                file_path,
                target_size_mb=self.config.target_size_mb,
                quality_start=self.config.jpeg_quality_start,
                quality_min=self.config.jpeg_quality_min,
                max_dimension=self.config.max_dimension,
            )
            file_size = len(compressed_bytes)
            logger.info(f'  Compressed: {file_size / 1024:.0f} KB ({width}x{height}), '
                        f'thumb: {len(thumbnail_bytes) / 1024:.0f} KB')

            # STEP 2: Get presigned URLs + trickle vectorization (OPT-13).
            # For trickle-mode photos (Keep-Watching watchdog, shallow queue) we run
            # presign and ONNX inference concurrently: the ~100–200 ms presign
            # round-trip hides behind inference time so the total wait is just
            # max(presign, inference) instead of the sum.
            # All other paths (import-once, initial-scan backlog, resource-limited)
            # keep priority > 0 or have a non-empty queue, so they always skip to
            # plain mesh-job vectorization.
            _is_trickle = (
                priority == 0
                and self._vision_manager is not None
                and self._upload_queue is not None
                and self._vision_manager.is_ready()
                and not (self._resource_monitor and self._resource_monitor.should_pause())
                and self._upload_queue.queue_depth() < _TRICKLE_THRESHOLD
            )
            local_faces = None
            if _is_trickle:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                    _presign_f = ex.submit(
                        self.api.get_upload_url, path.name,
                        event_id=event_id, folder_id=folder_id,
                    )
                    _vector_f = ex.submit(self._vectorize_trickle, compressed_bytes, path.name)
                presign    = _presign_f.result()
                local_faces = _vector_f.result()  # None → fallback to mesh job
            else:
                presign = self.api.get_upload_url(path.name, event_id=event_id, folder_id=folder_id)

            if presign.get('already_uploaded'):
                logger.info(f'  Skipping {path.name} — already uploaded (server dedup)')
                if self._state_db and file_hash:
                    self._state_db.mark_complete(file_path, presign.get('photo_id'))
                return

            upload_url       = presign.get('upload_url')
            photo_id         = presign.get('photo_id')
            if not upload_url or not photo_id:
                raise ValueError(f"Presign response missing fields (got: {list(presign.keys())})")
            thumbnail_url_r2 = presign.get('thumbnail_upload_url')
            thumb_key        = presign.get('thumbnail_key')

            # STEP 3–5: Upload main image to R2, thumbnail to R2, and optionally to
            # GDrive — all in parallel so network I/O overlaps.
            # Main R2 is critical (raises on failure). Thumbnail and GDrive are
            # best-effort and never block completion of the main upload.
            logger.info(f'  Uploading {path.name}...')

            def _r2_main():
                _r2_session.put(
                    upload_url, data=compressed_bytes,
                    headers={'Content-Type': 'image/jpeg'}, timeout=60,
                ).raise_for_status()

            def _r2_thumb():
                _r2_session.put(
                    thumbnail_url_r2, data=thumbnail_bytes,
                    headers={'Content-Type': 'image/webp'}, timeout=30,
                ).raise_for_status()

            _upload_tasks = {'main': _r2_main}
            if thumbnail_url_r2 and thumb_key:
                _upload_tasks['thumb'] = _r2_thumb

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(_upload_tasks)) as ex:
                _upload_futures = {name: ex.submit(fn) for name, fn in _upload_tasks.items()}
            # ThreadPoolExecutor.__exit__ waits for all futures — results are ready here.

            _upload_futures['main'].result()  # re-raises on R2 failure

            thumb_ok = False
            if 'thumb' in _upload_futures:
                try:
                    _upload_futures['thumb'].result()
                    thumb_ok = True
                except Exception as e:
                    logger.debug(f'  Thumbnail upload failed (non-fatal): {e}')
                    thumb_key = None

            # STEP 6: Persist r2_done + batch data BEFORE enqueueing.
            if self._state_db and file_hash:
                self._state_db.mark_r2_done(
                    file_path, photo_id=photo_id,
                    file_size_bytes=file_size, width=width, height=height,
                    thumbnail_key=thumb_key,
                    face_vectors=local_faces,
                )

            entry = {
                'photo_id':        photo_id,
                'file_size_bytes': file_size,
                'width':           width,
                'height':          height,
                'thumbnail_key':   thumb_key,
                '_file_path':      file_path,
            }
            # Include locally-detected face vectors so the server can write them
            # directly and skip creating a mesh vectoring_job for this photo.
            if local_faces is not None:
                entry['face_vectors'] = local_faces
            self._enqueue_batch(entry)
            logger.info(f'  Done: {path.name} (batched)')

        except Exception as e:
            logger.error(f'  Failed to process {path.name}: {e}')
            if self._state_db and file_hash:
                self._state_db.mark_failed(file_path)

    # ── Trickle vectorization ─────────────────────────────────────────────────

    def _vectorize_trickle(self, compressed_bytes: bytes, filename: str) -> Optional[list]:
        """Write compressed bytes to a temp file and run inline face detection.
        Returns list of face dicts (possibly empty) or None on timeout/failure
        (caller falls back to creating a mesh job instead)."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(compressed_bytes)
                tmp_path = tmp.name
            faces = self._vision_manager.process_image(
                tmp_path,
                confidence_threshold=self.config.face_confidence_threshold,
                timeout=30.0,
            )
            if faces is None:
                logger.warning(f'  Trickle: vision timeout for {filename} — mesh fallback')
            else:
                logger.info(f'  Trickle: {len(faces)} face(s) in {filename}')
            return faces
        except Exception as e:
            logger.warning(f'  Trickle vectorization error for {filename}: {e} — mesh fallback')
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ── Batch flush ───────────────────────────────────────────────────────────

    def _enqueue_batch(self, entry: dict):
        with self._batch_lock:
            self._batch.append(entry)
            if len(self._batch) >= self.config.upload_batch_size:
                self._flush_locked()

    def _flush_locked(self):
        """Must be called with _batch_lock held."""
        if not self._batch:
            return
        batch = self._batch[:]
        self._batch.clear()
        self._last_flush = time.time()
        t = threading.Thread(target=self._send_batch, args=(batch,), daemon=True)
        with self._batch_threads_lock:
            self._batch_threads.append(t)
        t.start()

    def _send_batch(self, batch: list[dict]):
        try:
            photos = [{k: v for k, v in e.items() if not k.startswith('_')} for e in batch]
            try:
                self.api.upload_complete_batch(photos)
                for entry in batch:
                    if self._state_db:
                        self._state_db.mark_complete(entry['_file_path'], entry['photo_id'])
            except Exception as e:
                logger.warning(f'  Batch complete failed ({len(batch)} photos): {e}')
                for entry in batch:
                    try:
                        if entry.get('face_vectors'):
                            logger.warning(
                                f"  Fallback upload_complete for {entry['photo_id']} "
                                f"drops face_vectors — mesh will compensate"
                            )
                        self.api.upload_complete(
                            entry['photo_id'], entry['file_size_bytes'],
                            entry['width'], entry['height'],
                            thumbnail_key=entry.get('thumbnail_key'),
                        )
                        if self._state_db:
                            self._state_db.mark_complete(entry['_file_path'], entry['photo_id'])
                    except Exception:
                        if self._state_db:
                            self._state_db.mark_failed(entry['_file_path'])
        finally:
            with self._batch_threads_lock:
                try:
                    self._batch_threads.remove(threading.current_thread())
                except ValueError:
                    pass

    def _timer_flush(self):
        with self._batch_lock:
            if self._batch:
                self._flush_locked()
        self._start_flush_timer()

    def _start_flush_timer(self):
        self._flush_timer = threading.Timer(self.config.upload_batch_timeout, self._timer_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def stop(self):
        if self._flush_timer:
            self._flush_timer.cancel()
        with self._batch_lock:
            self._flush_locked()
        with self._batch_threads_lock:
            threads = list(self._batch_threads)
        for t in threads:
            t.join(timeout=30)
        still_alive = [t for t in threads if t.is_alive()]
        if still_alive:
            logger.warning(
                f'{len(still_alive)} batch send(s) did not finish within 30 s. '
                'They will be recovered automatically on next startup.'
            )
