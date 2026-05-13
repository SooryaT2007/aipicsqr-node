"""
Mesh Worker
============
Processes two job types dispatched by the server:

  1. vectoring_jobs   — face detection + recognition on uploaded photos.
     The server assigns each job to the highest-score online node, so the
     node that uploaded the photo and the node that vectorizes it are
     completely independent.

  2. face_search_jobs — selfie embedding for guest face-matching.
     Same assignment logic: the highest-score node processes the guest
     selfie regardless of which node is "nearby". This lets 100 guests
     scan simultaneously across all online nodes.

Rate limiting
─────────────
Each node self-limits to a hardware-derived max-jobs-per-minute so a
single laptop is never overloaded:

    max_rpm = clamp(cpu_threads × (ram_gb / 8) × 2, min=2, max=120)

Examples:
  4 threads / 8 GB  →   8 rpm
  8 threads / 16 GB →  32 rpm
  12 threads / 32 GB → 96 rpm

Higher-spec machines naturally claim more jobs, making the mesh
self-balancing without any central scheduler.
"""

import base64
import logging
import os
import tempfile
import threading
import time
from collections import deque
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
        self._poll_interval = 10

        # Sliding 60-second window of job completion timestamps
        self._job_window: deque[float] = deque()
        self._rate_limit: Optional[int] = None  # cached on first call

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info('Mesh Worker stopped')

    # ── Rate limiting ──────────────────────────────────────────────────────────

    def _max_jobs_per_minute(self) -> int:
        if self._rate_limit is not None:
            return self._rate_limit
        try:
            import psutil
            threads = psutil.cpu_count(logical=True) or 2
            ram_gb = psutil.virtual_memory().total / (1024 ** 3)
            rpm = int(threads * (ram_gb / 8) * 2)
            self._rate_limit = max(2, min(rpm, 120))
        except Exception:
            self._rate_limit = 4
        logger.info(f'  Mesh rate limit: {self._rate_limit} jobs/min')
        return self._rate_limit

    def _within_rate_limit(self) -> bool:
        now = time.time()
        cutoff = now - 60.0
        while self._job_window and self._job_window[0] < cutoff:
            self._job_window.popleft()
        return len(self._job_window) < self._max_jobs_per_minute()

    def _record_job_done(self):
        self._job_window.append(time.time())

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                if self.resource_monitor.should_pause():
                    time.sleep(self._poll_interval)
                    continue

                if not self._within_rate_limit():
                    logger.debug('Mesh: rate limit reached — pausing 5 s')
                    time.sleep(5)
                    continue

                # Priority 1: selfie jobs (guest is waiting live)
                selfie_jobs = self._safe_get_selfie_jobs()
                if selfie_jobs:
                    self._process_selfie_job(selfie_jobs[0])
                    continue

                # Priority 2: photo vectoring jobs
                jobs = self._safe_get_vectoring_jobs()
                if jobs:
                    job = jobs[0]
                    self._process_vectoring_job(job['id'], job['photo_id'], job.get('r2_url', ''))
                    continue

            except Exception as e:
                logger.error(f'  Mesh worker error: {e}')

            time.sleep(self._poll_interval)

    def _safe_get_vectoring_jobs(self) -> list:
        try:
            return self.api.get_jobs()
        except Exception as e:
            logger.debug(f'Mesh: get_jobs error: {e}')
            return []

    def _safe_get_selfie_jobs(self) -> list:
        try:
            return self.api.get_selfie_jobs()
        except Exception as e:
            logger.debug(f'Mesh: get_selfie_jobs error: {e}')
            return []

    # ── Vectoring job ──────────────────────────────────────────────────────────

    def _process_vectoring_job(self, job_id: str, photo_id: str, r2_url: str):
        logger.info(f'  Mesh: vectoring job {job_id[:8]}...')
        try:
            self.api.claim_job(job_id)

            if not r2_url:
                self.api.fail_job(job_id, 'No R2 URL')
                return

            img_resp = requests.get(r2_url, timeout=30)
            img_resp.raise_for_status()

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
            self._record_job_done()
            logger.info(f'  Mesh: vectoring {job_id[:8]} done — {len(face_results or [])} face(s)')

        except Exception as e:
            logger.error(f'  Mesh: vectoring {job_id[:8]} failed: {e}')
            self.api.fail_job(job_id, str(e))

    # ── Selfie job ─────────────────────────────────────────────────────────────

    def _process_selfie_job(self, job: dict):
        job_id = job['id']
        logger.info(f'  Mesh: selfie job {job_id[:8]}...')
        try:
            self.api.claim_selfie_job(job_id)

            selfie_b64 = job.get('selfie_base64', '')
            if not selfie_b64:
                self.api.fail_selfie_job(job_id, 'No selfie data')
                return

            # Strip data URI prefix if present (e.g. "data:image/jpeg;base64,...")
            if ',' in selfie_b64:
                selfie_b64 = selfie_b64.split(',', 1)[1]

            image_bytes = base64.b64decode(selfie_b64)
            embedding = self.vision_manager.process_selfie(image_bytes, timeout=15.0)

            if not embedding:
                self.api.fail_selfie_job(job_id, 'No face detected in selfie')
                return

            self.api.complete_selfie_job(job_id, embedding)
            self._record_job_done()
            logger.info(f'  Mesh: selfie {job_id[:8]} done')

        except Exception as e:
            logger.error(f'  Mesh: selfie {job_id[:8]} failed: {e}')
            self.api.fail_selfie_job(job_id, str(e))
