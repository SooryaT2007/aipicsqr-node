"""
Photo Uploader Service
======================
Complete pipeline for processing a new photo:
1. Compress the image (Pillow)
2. Upload to Cloudflare R2 (boto3 / S3 compatible)
3. Create photo record in Supabase
4. Run face detection + recognition (YuNet + SFace)
5. Upload face vectors to Supabase (pgvector)
"""

import uuid
import logging
import json
from pathlib import Path
from typing import Optional

import requests

from services.compressor import compress_image

logger = logging.getLogger('AIPICSQR-node')


class PhotoUploader:
    """
    Orchestrates the full photo processing pipeline:
    Capture â†’ Compress â†’ Upload (R2) â†’ Detect â†’ Vectorize â†’ Store (Supabase)
    """

    def __init__(self, config, supabase, vision_manager):
        self.config = config
        self.supabase = supabase
        self.vision_manager = vision_manager

        self.node_id = getattr(config, 'node_id', None)

    def process_photo(self, file_path: str):
        """
        Full pipeline for a single photo file.
        
        Args:
            file_path: Absolute path to the photo file
        """
        path = Path(file_path)
        photo_id = str(uuid.uuid4())

        if not self.config.event_id:
            logger.warning("  âš ï¸ No EVENT_ID configured. Skipping upload until EVENT_ID is set in .env.")
            return

        try:
            # â”€â”€ STEP 1: Compress â”€â”€
            logger.info(f"  ðŸ“¦ Compressing {path.name}...")
            compressed_bytes, width, height = compress_image(
                file_path,
                target_size_mb=self.config.target_size_mb,
                quality_start=self.config.jpeg_quality_start,
                quality_min=self.config.jpeg_quality_min,
                max_dimension=self.config.max_dimension,
            )
            file_size = len(compressed_bytes)
            logger.info(f"  ðŸ“¦ Compressed: {file_size / 1024:.0f}KB ({width}x{height})")

            # â”€â”€ STEP 2: Upload to R2 â”€â”€
            # STEP 2: Request Presigned URL from Web API
            logger.info(f"  ☁️  Requesting secure upload link...")
            api_url = f"{self.config.api_base_url.rstrip('/')}/api/upload/presign"
            presign_payload = {
                "event_id": self.config.event_id,
                "filename": path.name,
                "content_type": "image/jpeg",
                "node_id": self.node_id
            }
            
            resp = requests.post(api_url, json=presign_payload)
            resp.raise_for_status()
            presign_data = resp.json()
            
            upload_url = presign_data["upload_url"]
            photo_id = presign_data["photo_id"]

            # STEP 3: Upload to Presigned URL
            logger.info(f"  ☁️  Uploading compressed photo...")
            put_resp = requests.put(
                upload_url, 
                data=compressed_bytes, 
                headers={"Content-Type": "image/jpeg"}
            )
            put_resp.raise_for_status()
            logger.info(f"  ☁️  Uploaded successfully")

            # Update status to processing since we are about to process it locally
            self.supabase.table("photos").update({
                "file_size_bytes": file_size,
                "width": width,
                "height": height,
                "status": "processing",
            }).eq("id", photo_id).execute()
            logger.info(f"  💾 Photo record initialized: {photo_id[:8]}...")
            # â”€â”€ STEP 4: Face Detection + Recognition â”€â”€
            if self.vision_manager.should_delegate():
                # Resource limit hit â€” queue for mesh processing
                logger.info(f"  âš¡ Queuing for mesh: {path.name}")
                self._queue_for_mesh(photo_id)
                return

            logger.info(f"  ðŸ§  Running face detection + recognition...")
            face_results = self.vision_manager.process_image(
                file_path,
                confidence_threshold=self.config.face_confidence_threshold,
            )

            if not face_results:
                # No faces or delegated â€” might still be a valid photo
                if self.vision_manager.should_delegate():
                    self._queue_for_mesh(photo_id)
                else:
                    # No faces found, mark as vectorized (0 faces)
                    self.supabase.table('photos').update({
                        'status': 'vectorized',
                        'face_count': 0,
                        'vectorized_at': 'now()',
                    }).eq('id', photo_id).execute()
                return

            # â”€â”€ STEP 5: Upload face vectors to Supabase â”€â”€
            vectors_to_insert = []
            for face in face_results:
                vectors_to_insert.append({
                    'photo_id': photo_id,
                    'event_id': self.config.event_id,
                    'embedding': json.dumps(face['embedding']),
                    'bbox': face['bbox'],
                    'confidence': face['confidence'],
                })

            if vectors_to_insert:
                self.supabase.table('face_vectors').insert(vectors_to_insert).execute()

            # Update photo status
            self.supabase.table('photos').update({
                'status': 'vectorized',
                'face_count': len(face_results),
                'vectorized_at': 'now()',
            }).eq('id', photo_id).execute()

            logger.info(
                f"  âœ… Complete: {path.name} â†’ "
                f"{len(face_results)} face(s), "
                f"{file_size / 1024:.0f}KB"
            )

        except Exception as e:
            logger.error(f"  âŒ Failed to process {path.name}: {e}")
            # Try to mark as failed
            try:
                self.supabase.table('photos').update({
                    'status': 'failed',
                }).eq('id', photo_id).execute()
            except Exception:
                pass

    def _queue_for_mesh(self, photo_id: str):
        """Queue a photo for mesh network processing."""
        try:
            self.supabase.table('vectoring_jobs').insert({
                'photo_id': photo_id,
                'status': 'queued',
            }).execute()

            self.supabase.table('photos').update({
                'status': 'processing',
            }).eq('id', photo_id).execute()
        except Exception as e:
            logger.error(f"Failed to queue for mesh: {e}")
