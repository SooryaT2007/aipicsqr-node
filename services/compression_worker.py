"""
Compression Worker
==================
Polls for compression_jobs created when a GDrive-source folder is scanned.
For each job the worker:

  1. Downloads the original photo from Google Drive (public link — folder must
     be shared "Anyone with link can view").
  2. Compresses to 2 MB JPEG + WebP thumbnail using the shared compressor.
  3. PUTs thumbnail to R2 via the presigned URL the server provided.
  4. Uploads the main photo to:
       - R2  (storage_type='r2')  : PUTs via the presigned URL from the server.
       - GDrive (storage_type='gdrive') : Uses GDriveUploader with the cached
           access token for the event (same path as local→GDrive uploads).
  5. Calls api.complete_compression_job() so the server updates the photo
     record and creates a vectoring_job for face detection.

The server's pull endpoint also resets stale jobs (stuck > 5 min) so crashes
are automatically recovered without node-side tracking.

Backoff: starts at 5 s when idle, backs off to 60 s. Snaps back to 5 s the
moment a job is found — same adaptive pattern as the GDrive scan loop.
"""

import logging
import tempfile
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from services.compressor import compress_image_with_thumbnail
from services.gdrive_uploader import GDriveUploader

logger = logging.getLogger('AIPICSQR-node')

_r2_session = requests.Session()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
_r2_session.mount('https://', HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1.5,
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods=frozenset({'PUT'}),
    raise_on_status=False,
)))

_IDLE_POLL_MIN = 5
_IDLE_POLL_MAX = 60
_DOWNLOAD_TIMEOUT = 90  # GDrive originals can be large


class CompressionWorker:
    """
    Background thread that pulls and processes compression_jobs one at a time.
    Designed to run concurrently with MeshWorker but does not use the vision
    lock — compression is CPU-light compared to ONNX inference.
    """

    def __init__(self, config, api_client):
        self._config  = config
        self._api     = api_client
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._backoff = _IDLE_POLL_MIN

        # GDrive token cache: event_id → {access_token, folder_id, expires_at}
        self._gdrive_cache: dict[str, dict] = {}
        self._gdrive_cache_lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name='compression-worker',
        )
        self._thread.start()
        logger.info('OK Compression Worker started')

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info('Compression Worker stopped')

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                job = self._api.pull_compression_job()
                if not job:
                    time.sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, _IDLE_POLL_MAX)
                    continue

                self._backoff = _IDLE_POLL_MIN
                self._process(job)
            except Exception as e:
                logger.error(f'Compression worker loop error: {e}')
                time.sleep(10)

    # ── Job processing ────────────────────────────────────────────────────────

    def _process(self, job: dict):
        job_id   = job['job_id']
        photo_id = job['photo_id']
        event_id = job['event_id']
        filename = job.get('original_filename', 'photo.jpg')
        storage  = job.get('storage_type', 'r2')

        logger.info(f'  Compression job {job_id[:8]}: {filename} → {storage}')

        tmp_path = None
        try:
            # STEP 1: Download original from GDrive
            download_url = job['download_url']
            logger.info(f'  Downloading {filename}...')
            resp = requests.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            original_bytes = resp.content
            logger.info(f'  Downloaded: {len(original_bytes) / 1024:.0f} KB')

            # Write to a temp file so Pillow can open it
            suffix = '.' + (filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg')
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(original_bytes)
                tmp_path = tmp.name

            # STEP 2: Compress
            compressed_bytes, thumbnail_bytes, width, height = compress_image_with_thumbnail(
                tmp_path,
                target_size_mb=self._config.target_size_mb,
                quality_start=self._config.jpeg_quality_start,
                quality_min=self._config.jpeg_quality_min,
                max_dimension=self._config.max_dimension,
            )
            file_size = len(compressed_bytes)
            logger.info(
                f'  Compressed: {file_size / 1024:.0f} KB ({width}x{height}), '
                f'thumb: {len(thumbnail_bytes) / 1024:.0f} KB'
            )

            # STEP 3: PUT thumbnail to R2
            thumb_key    = job['thumbnail_key']
            thumb_url_r2 = job['thumbnail_upload_url']
            thumb_public = job.get('thumbnail_public_url')
            _r2_session.put(
                thumb_url_r2,
                data=thumbnail_bytes,
                headers={'Content-Type': 'image/webp'},
                timeout=30,
            ).raise_for_status()

            # STEP 4: Upload main photo
            gdrive_file_id = None
            if storage == 'gdrive':
                gdrive_file_id = self._upload_to_gdrive(event_id, filename, compressed_bytes)
            elif storage == 'r2':
                main_url = job.get('main_upload_url')
                if main_url:
                    _r2_session.put(
                        main_url,
                        data=compressed_bytes,
                        headers={'Content-Type': 'image/jpeg'},
                        timeout=60,
                    ).raise_for_status()

            # STEP 5: Notify server — updates photo, creates vectoring_job
            self._api.complete_compression_job(
                job_id=job_id,
                thumbnail_key=thumb_key,
                thumbnail_url=thumb_public,
                width=width,
                height=height,
                file_size_bytes=file_size,
                gdrive_file_id=gdrive_file_id,
            )

            suffix_str = f' → GDrive {gdrive_file_id[:8]}' if gdrive_file_id else ''
            logger.info(f'  Compression job {job_id[:8]} done{suffix_str}')

        except Exception as e:
            logger.error(f'  Compression job {job_id[:8]} failed: {e}')
            try:
                self._api.fail_compression_job(job_id, str(e))
            except Exception:
                pass
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ── GDrive upload (reuses uploader cache pattern) ─────────────────────────

    def _upload_to_gdrive(self, event_id: str, filename: str, data: bytes) -> Optional[str]:
        try:
            cfg = self._get_gdrive_config(event_id)
            if not cfg:
                return None
            uploader = GDriveUploader(cfg['access_token'], cfg['folder_id'])
            file_id = uploader.upload(filename, data)
            logger.debug(f'  GDrive: {filename} → {file_id[:8]}')
            return file_id
        except Exception as e:
            logger.warning(f'  GDrive upload failed (compression job): {e}')
            return None

    def _get_gdrive_config(self, event_id: str) -> Optional[dict]:
        now = datetime.now(timezone.utc)
        with self._gdrive_cache_lock:
            cfg = self._gdrive_cache.get(event_id)
            if cfg:
                try:
                    from datetime import timezone as _tz
                    expiry = datetime.fromisoformat(cfg['expires_at'].replace('Z', '+00:00'))
                    if now < expiry:
                        return cfg
                except Exception:
                    pass
        try:
            fresh = self._api.get_gdrive_upload_config(event_id)
            with self._gdrive_cache_lock:
                self._gdrive_cache[event_id] = fresh
            return fresh
        except Exception as e:
            logger.warning(f'  Could not get GDrive config for compression: {e}')
            return None
