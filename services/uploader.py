"""
Photo Uploader Service
======================
Pipeline for processing a new photo:
1. Compress the image (Pillow)
2. Get a presigned R2 URL from the API
3. PUT the bytes directly to R2 (bypasses our server — no Vercel bandwidth used)
4. Notify the API — server marks the photo done and queues face vectorization
   on the highest-score available mesh node (private pool only).
   Public pool photos are marked vectorized immediately; no face scan needed.

Face detection is NOT done here. It runs in MeshWorker on whichever node
the server assigns as best — completely independent of who uploaded the photo.

folder_info dict: {id, event_id, path, pool_type, watch_until}
"""

import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from services.compressor import compress_image

# Shared session for R2 PUT with retry on transient errors
_r2_session = requests.Session()
_r2_session.mount('https://', HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1.5,
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods=frozenset({'PUT'}),
    raise_on_status=False,
)))

logger = logging.getLogger('AIPICSQR-node')


class PhotoUploader:
    def __init__(self, config, api_client, vision_manager=None):
        self.config = config
        self.api = api_client
        # vision_manager is intentionally unused here — face vectorization is
        # handled by MeshWorker on whichever node the server assigns.

    def process_photo(self, file_path: str, folder_info: dict | None = None):
        path = Path(file_path)

        event_id = (folder_info or {}).get('event_id') or self.config.event_id
        folder_id = (folder_info or {}).get('id')
        pool_type = (folder_info or {}).get('pool_type', 'public')

        if not event_id:
            logger.warning(f'  No event set — skipping {path.name}')
            return

        try:
            # STEP 1: Compress
            logger.info(f'  Compressing {path.name}...')
            compressed_bytes, width, height = compress_image(
                file_path,
                target_size_mb=self.config.target_size_mb,
                quality_start=self.config.jpeg_quality_start,
                quality_min=self.config.jpeg_quality_min,
                max_dimension=self.config.max_dimension,
            )
            file_size = len(compressed_bytes)
            logger.info(f'  Compressed: {file_size / 1024:.0f} KB ({width}x{height})')

            # STEP 2: Get presigned URL (tiny JSON — no file bytes through Vercel)
            logger.info(f'  Requesting upload URL...')
            presign = self.api.get_upload_url(path.name, event_id=event_id, folder_id=folder_id)

            # Server detected this file was already uploaded in a previous session
            if presign.get('already_uploaded'):
                logger.info(f'  Skipping {path.name} — already uploaded (dedup)')
                return

            upload_url = presign['upload_url']
            photo_id = presign['photo_id']

            # STEP 3: PUT directly to R2
            logger.info(f'  Uploading to R2...')
            put_resp = _r2_session.put(
                upload_url,
                data=compressed_bytes,
                headers={'Content-Type': 'image/jpeg'},
                timeout=60,
            )
            put_resp.raise_for_status()
            logger.info(f'  Uploaded OK')

            # STEP 4: Notify API — server creates vectoring_job on best node
            result = self.api.upload_complete(photo_id, file_size, width, height)
            status = result.get('status', '?')
            if status == 'vectorized':
                logger.info(f'  Done: {path.name}')
            else:
                logger.info(f'  Done: {path.name} — queued for processing')

        except Exception as e:
            logger.error(f'  Failed to process {path.name}: {e}')
