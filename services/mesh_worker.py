"""
Mesh Worker
============
Picks up vectoring jobs queued by the server for photos that couldn't
be processed locally due to resource limits. Polls the API — no direct
Supabase access.
"""

import logging
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger('AIPICSQR-node')


class MeshWorker:
    def __init__(self, config, api_client, vision_manager, resource_monitor):
        self.config = config
        self.api = api_client
        self.vision_manager = vision_manager
        self.resource_monitor = resource_monitor
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = 15

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Mesh Worker stopped")

    def _loop(self):
        while self._running:
            try:
                if self.resource_monitor.should_pause():
                    time.sleep(self._poll_interval)
                    continue

                jobs = self.api.get_jobs()
                if not jobs:
                    time.sleep(self._poll_interval)
                    continue

                job = jobs[0]
                self._process_job(job['id'], job['photo_id'], job.get('r2_url', ''))

            except Exception as e:
                logger.error(f"  Mesh worker error: {e}")
                time.sleep(self._poll_interval)

    def _process_job(self, job_id: str, photo_id: str, r2_url: str):
        logger.info(f"  Mesh: picking up job {job_id[:8]}...")
        try:
            self.api.claim_job(job_id)

            # Download compressed photo from R2 for local processing
            if not r2_url:
                self.api.fail_job(job_id, 'No R2 URL')
                return

            img_resp = requests.get(r2_url, timeout=30)
            img_resp.raise_for_status()

            # Write to a temp file for the vision pipeline
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(img_resp.content)
                tmp_path = tmp.name

            try:
                face_results = self.vision_manager.process_image(
                    tmp_path,
                    confidence_threshold=self.config.face_confidence_threshold,
                )
            finally:
                os.unlink(tmp_path)

            self.api.complete_job(job_id, photo_id, face_results or [])
            logger.info(f"  Mesh: job {job_id[:8]} done — {len(face_results or [])} face(s)")

        except Exception as e:
            logger.error(f"  Mesh: job {job_id[:8]} failed: {e}")
            self.api.fail_job(job_id, str(e))
