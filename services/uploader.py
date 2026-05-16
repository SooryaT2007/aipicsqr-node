"""
Photo Uploader Service
======================
Pipeline for processing a new photo:
1. Dedup: check SQLite state DB — skip if already uploaded this session or before
2. Compress the image + generate thumbnail (Pillow)
3. Get presigned R2 URLs from the API (main image + thumbnail)
4. PUT both files directly to R2 (bypasses our server — no Vercel bandwidth used)
5. Accumulate completions in a batch; flush every 10 photos or 5 seconds via the
   batch API endpoint (reduces Vercel invocations ~50x during bulk dumps)

Face detection is NOT done here. It runs in MeshWorker on whichever node
the server assigns as best — completely independent of who uploaded the photo.

folder_info dict: {id, event_id, path, pool_type, watch_until}
"""

import logging
import threading
import time
from pathlib import Path
from typing import Optional

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
                 state_db: Optional[UploadStateDB] = None):
        self.config = config
        self.api = api_client
        self._state_db = state_db

        self._batch: list[dict] = []
        self._batch_lock = threading.Lock()
        self._last_flush = time.time()
        self._flush_timer: Optional[threading.Timer] = None
        self._start_flush_timer()

    # ── Public ────────────────────────────────────────────────────────────────

    def process_photo(self, file_path: str, folder_info: dict | None = None):
        path = Path(file_path)
        event_id = (folder_info or {}).get('event_id') or self.config.event_id
        folder_id = (folder_info or {}).get('id')

        if not event_id:
            logger.warning(f'  No event set — skipping {path.name}')
            return

        # Fast dedup: hash first 64 KB and check DB
        if self._state_db:
            file_hash = UploadStateDB.compute_hash(file_path)
            if file_hash and self._state_db.is_processed(file_path, file_hash):
                logger.debug(f'  Skipping {path.name} — already uploaded (SQLite dedup)')
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

            # STEP 2: Get presigned URLs (server dedup check)
            presign = self.api.get_upload_url(path.name, event_id=event_id, folder_id=folder_id)
            if presign.get('already_uploaded'):
                logger.info(f'  Skipping {path.name} — already uploaded (server dedup)')
                if self._state_db and file_hash:
                    self._state_db.mark_complete(file_path, presign.get('photo_id'))
                return

            upload_url      = presign['upload_url']
            thumbnail_url_r2 = presign.get('thumbnail_upload_url')
            photo_id        = presign['photo_id']
            r2_key          = presign.get('r2_key', '')
            thumb_key       = presign.get('thumbnail_key')

            # STEP 3: PUT main image to R2
            logger.info(f'  Uploading {path.name}...')
            put_resp = _r2_session.put(
                upload_url,
                data=compressed_bytes,
                headers={'Content-Type': 'image/jpeg'},
                timeout=60,
            )
            put_resp.raise_for_status()

            if self._state_db and file_hash:
                self._state_db.mark_r2_done(file_path)

            # STEP 4: PUT thumbnail to R2 (best-effort, non-blocking)
            thumb_public_url = None
            if thumbnail_url_r2 and thumb_key:
                try:
                    _r2_session.put(
                        thumbnail_url_r2,
                        data=thumbnail_bytes,
                        headers={'Content-Type': 'image/jpeg'},
                        timeout=30,
                    ).raise_for_status()
                    # Build the public URL for the thumbnail
                    base = self.config.api_base_url.replace('https://dashboard.', 'https://r2.')
                    thumb_public_url = f"{self.config.r2_public_base}/{thumb_key}" if hasattr(self.config, 'r2_public_base') else None
                except Exception as e:
                    logger.debug(f'  Thumbnail upload failed (non-fatal): {e}')
                    thumb_key = None

            # STEP 5: Add to pending batch
            entry = {
                'photo_id':        photo_id,
                'file_size_bytes': file_size,
                'width':           width,
                'height':          height,
                'thumbnail_key':   thumb_key,
                'thumbnail_url':   thumb_public_url,
                '_file_path':      file_path,
            }
            self._enqueue_batch(entry)
            logger.info(f'  Done: {path.name} (batched)')

        except Exception as e:
            logger.error(f'  Failed to process {path.name}: {e}')
            if self._state_db and file_hash:
                self._state_db.mark_failed(file_path)

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
        threading.Thread(target=self._send_batch, args=(batch,), daemon=True).start()

    def _send_batch(self, batch: list[dict]):
        photos = [{k: v for k, v in e.items() if not k.startswith('_')} for e in batch]
        try:
            self.api.upload_complete_batch(photos)
            for entry in batch:
                if self._state_db:
                    self._state_db.mark_complete(entry['_file_path'], entry['photo_id'])
        except Exception as e:
            logger.warning(f'  Batch complete failed ({len(batch)} photos): {e}')
            # Fallback: notify individually
            for entry in batch:
                try:
                    self.api.upload_complete(
                        entry['photo_id'], entry['file_size_bytes'],
                        entry['width'], entry['height'],
                    )
                    if self._state_db:
                        self._state_db.mark_complete(entry['_file_path'], entry['photo_id'])
                except Exception:
                    if self._state_db:
                        self._state_db.mark_failed(entry['_file_path'])

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
        # Final flush
        with self._batch_lock:
            self._flush_locked()
