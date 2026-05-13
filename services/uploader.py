"""
Photo Uploader Service
======================
Pipeline for processing a new photo:
1. Compress the image (Pillow)
2. Get a presigned R2 URL from the API (no Supabase credentials needed)
3. PUT the bytes directly to R2 (bypasses our server — no Vercel bandwidth used)
4. Run face detection + recognition locally — ONLY for private/premium pool
   (public pool photos skip face scan; they are for everyone to see)
5. POST results to the API — server writes to Supabase

folder_info dict: {id, event_id, path, pool_type, watch_until}
"""

import logging
from pathlib import Path

import requests

from services.compressor import compress_image

logger = logging.getLogger('AIPICSQR-node')


class PhotoUploader:
    def __init__(self, config, api_client, vision_manager):
        self.config = config
        self.api = api_client
        self.vision_manager = vision_manager

    def process_photo(self, file_path: str, folder_info: dict | None = None):
        path = Path(file_path)

        # Resolve event context from folder_info (API-driven) or legacy config
        event_id = (folder_info or {}).get('event_id') or self.config.event_id
        folder_id = (folder_info or {}).get('id')
        pool_type = (folder_info or {}).get('pool_type', 'public')
        is_public = pool_type == 'public'

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
            upload_url = presign['upload_url']
            photo_id = presign['photo_id']

            # STEP 3: PUT directly to R2
            logger.info(f'  Uploading to R2...')
            put_resp = requests.put(
                upload_url,
                data=compressed_bytes,
                headers={'Content-Type': 'image/jpeg'},
                timeout=60,
            )
            put_resp.raise_for_status()
            logger.info(f'  Uploaded OK')

            # STEP 4: Face detection — skipped for public pool
            if is_public:
                logger.info(f'  Public pool — skipping face scan for {path.name}')
                self.api.upload_complete(photo_id, file_size, width, height, [])
                logger.info(f'  Done: {path.name} (public, no face scan)')
                return

            if self.vision_manager.should_delegate():
                logger.info(f'  Resource limit — delegating {path.name} to mesh')
                self.api.upload_complete(photo_id, file_size, width, height, [], delegated=True)
                return

            logger.info(f'  Running face detection...')
            face_results = self.vision_manager.process_image(
                file_path,
                confidence_threshold=self.config.face_confidence_threshold,
            )

            # STEP 5: Send everything to the API
            self.api.upload_complete(photo_id, file_size, width, height, face_results or [])

            logger.info(
                f'  Done: {path.name} [{pool_type}] — '
                f'{len(face_results or [])} face(s), {file_size / 1024:.0f} KB'
            )

        except Exception as e:
            logger.error(f'  Failed to process {path.name}: {e}')
