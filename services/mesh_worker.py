"""
Mesh Worker v2
==============
Buffer-based vectoring job processor using the server's pull_vector_tasks
function (SKIP LOCKED). Replaces the old poll-then-claim pattern.

Key behaviours (per system design):

  Server-driven batching
  ───────────────────────
  The node does NOT calculate batch size. The server derives it from the
  stored performance_score and applies the fair-share cap. This keeps all
  distribution logic on the cloud so it can be updated without a node
  reinstall.

  Parallel prefetch
  ──────────────────
  While the CPU vectorizes image N, images N+1 and N+2 are already being
  downloaded from R2 in background threads. This hides the 1-2 MB network
  transfer behind the ONNX inference time, so the node stays at near-100%
  CPU utilization rather than stalling on downloads.

  Buffer-driven polling
  ──────────────────────
  pull_vector_tasks is called only when the local buffer is empty — not on
  a fixed timer. This avoids hammering the DB under load. When idle the
  node polls every 2 s so urgent (priority=0) jobs are picked up quickly
  without needing a separate Realtime connection.

  End-to-end timing
  ──────────────────
  Each job arrives with assigned_at (set atomically by pull_vector_tasks).
  The node sends completed_at when it finishes, giving the server the full
  pipeline time: download + vectorize + network send. The load balancer
  uses this to route future work to the fastest nodes.

  Selfie jobs (face_search_jobs)
  ────────────────────────────────
  Guest selfie matching runs on a dedicated thread that polls every 2 s
  independently of the vectoring loop. The vision lock in
  VisionProcessManager serialises the two threads so they never overlap
  inside the vision process. Worst-case selfie wait = one vectoring job
  in progress (~20 s max), not the entire queued buffer.
"""

import base64
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger('AIPICSQR-node')

_PREFETCH_AHEAD   = 2     # jobs to download ahead while processing
_IDLE_POLL_MIN    = 2     # starting poll interval when buffer is empty
_IDLE_POLL_MAX    = 30    # ceiling for vectoring backoff (seconds)
_SELFIE_IDLE_MAX  = 10    # ceiling for selfie backoff — kept low, guests wait for results
_DOWNLOAD_TIMEOUT = 60    # seconds for R2 download
_PREFETCH_WAIT    = 120   # max seconds to wait for an in-flight download

_PENDING = object()       # sentinel: download thread is in flight


class MeshWorker:
    def __init__(self, config, api_client, vision_manager, resource_monitor):
        self.config = config
        self.api = api_client
        self.vision_manager = vision_manager
        self.resource_monitor = resource_monitor
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._selfie_thread: Optional[threading.Thread] = None

        # Claimed jobs waiting to be processed
        self._buffer: deque[dict] = deque()
        self._buffer_lock = threading.Lock()

        # Download cache: job_id → bytes | _PENDING | b'' (failed download)
        self._cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self._organize_thread: Optional[threading.Thread] = None

        # Backoff state — reset to min the moment work is found
        self._idle_backoff   = _IDLE_POLL_MIN
        self._selfie_backoff = _IDLE_POLL_MIN

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='mesh-vectoring')
        self._thread.start()
        self._selfie_thread = threading.Thread(target=self._selfie_loop, daemon=True, name='mesh-selfie')
        self._selfie_thread.start()
        self._organize_thread = threading.Thread(target=self._organize_loop, daemon=True, name='mesh-organize')
        self._organize_thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        if self._selfie_thread:
            self._selfie_thread.join(timeout=10)
        if self._organize_thread:
            self._organize_thread.join(timeout=10)
        logger.info('Mesh Worker stopped')

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                if self.resource_monitor.should_pause():
                    time.sleep(10)
                    continue

                # Fill buffer if empty
                with self._buffer_lock:
                    is_empty = len(self._buffer) == 0

                if is_empty:
                    batch = self._fetch_batch()
                    if not batch:
                        time.sleep(self._idle_backoff)
                        self._idle_backoff = min(self._idle_backoff * 2, _IDLE_POLL_MAX)
                        continue
                    self._idle_backoff = _IDLE_POLL_MIN  # work found — snap back to fast polling
                    with self._buffer_lock:
                        self._buffer.extend(batch)
                    # Kick off prefetch for the first N+1 jobs in buffer
                    with self._buffer_lock:
                        ahead = list(self._buffer)[:_PREFETCH_AHEAD + 1]
                    self._prefetch(ahead)

                with self._buffer_lock:
                    if not self._buffer:
                        continue
                    job = self._buffer.popleft()
                    # Prefetch next jobs while this one is being processed
                    ahead = list(self._buffer)[:_PREFETCH_AHEAD]

                if ahead:
                    self._prefetch(ahead)

                self._process_job(job)

            except Exception as e:
                logger.error(f'Mesh worker loop error: {e}')
                time.sleep(5)

    # ── Job fetch ──────────────────────────────────────────────────────────────

    def _fetch_batch(self) -> list:
        try:
            return self.api.pull_jobs()
        except Exception as e:
            logger.debug(f'Mesh: pull_jobs error: {e}')
            return []

    # ── Prefetch (parallel download) ───────────────────────────────────────────

    def _prefetch(self, jobs: list):
        for job in jobs:
            jid = job['job_id']
            with self._cache_lock:
                if jid in self._cache:
                    continue
                self._cache[jid] = _PENDING
            t = threading.Thread(target=self._download_to_cache, args=(job,), daemon=True)
            t.start()

    def _download_to_cache(self, job: dict):
        jid = job['job_id']
        url = job.get('r2_url', '')
        try:
            resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            data = resp.content
            logger.debug(f'Prefetch {jid[:8]}: {len(data):,} bytes')
        except Exception as e:
            logger.debug(f'Prefetch {jid[:8]}: download failed: {e}')
            data = b''
        with self._cache_lock:
            self._cache[jid] = data

    def _get_image(self, job: dict) -> Optional[bytes]:
        """Return image bytes; downloads synchronously if not already prefetched."""
        jid = job['job_id']

        with self._cache_lock:
            val = self._cache.get(jid)

        # Not started yet — download synchronously
        if val is None:
            self._download_to_cache(job)
            with self._cache_lock:
                val = self._cache.get(jid)
            return val if val else None

        # Wait for an in-flight prefetch
        if val is _PENDING:
            deadline = time.time() + _PREFETCH_WAIT
            while time.time() < deadline:
                with self._cache_lock:
                    val = self._cache.get(jid)
                if val is not _PENDING:
                    return val if val else None
                time.sleep(0.05)
            logger.warning(f'Prefetch {jid[:8]}: timeout after {_PREFETCH_WAIT}s')
            return None

        return val if val else None

    # ── Vectoring job ──────────────────────────────────────────────────────────

    def _process_job(self, job: dict):
        jid = job['job_id']
        photo_id = job['photo_id']
        urgent = job.get('is_urgent', False)

        logger.debug(f'Mesh: vectoring {jid[:8]} (urgent={urgent})')

        try:
            img = self._get_image(job)
            if not img:
                logger.error(f'Mesh: {jid[:8]} — download produced no data')
                self.api.fail_job(jid, 'Download failed')
                return

            # Record start time after download — this is the true per-image
            # processing start. claimed_at is shared across the whole batch so
            # using it would inflate avg_job_ms for every job after the first.
            started_at = datetime.now(timezone.utc).isoformat()

            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(img)
                tmp_path = tmp.name

            try:
                face_results = self.vision_manager.process_image(
                    tmp_path,
                    confidence_threshold=self.config.face_confidence_threshold,
                )
            finally:
                os.unlink(tmp_path)

            completed_at = datetime.now(timezone.utc).isoformat()
            self.api.complete_job(jid, photo_id, face_results or [],
                                  started_at=started_at, completed_at=completed_at)

            face_count = len(face_results or [])
            logger.debug(f'Mesh: {jid[:8]} done — {face_count} face(s)')

        except Exception as e:
            logger.error(f'Mesh: {jid[:8]} failed: {e}')
            self.api.fail_job(jid, str(e))

        finally:
            with self._cache_lock:
                self._cache.pop(jid, None)

    # ── Selfie thread (dedicated, higher priority than vectoring) ─────────────

    def _selfie_loop(self):
        """Polls for selfie jobs independently of the vectoring loop.

        Contends for the vision lock in VisionProcessManager, so it runs as
        soon as the current vectoring job (if any) finishes — never waits for
        the whole buffer to drain.
        """
        while self._running:
            try:
                jobs = self._safe_get_selfie_jobs()
                if jobs:
                    self._selfie_backoff = _IDLE_POLL_MIN  # work found — snap back to fast polling
                    self._process_selfie_job(jobs[0])
                else:
                    time.sleep(self._selfie_backoff)
                    self._selfie_backoff = min(self._selfie_backoff * 2, _SELFIE_IDLE_MAX)
            except Exception as e:
                logger.error(f'Selfie loop error: {e}')
                time.sleep(5)

    def _safe_get_selfie_jobs(self) -> list:
        try:
            return self.api.get_selfie_jobs()
        except Exception as e:
            logger.debug(f'Mesh: get_selfie_jobs error: {e}')
            return []

    def _process_selfie_job(self, job: dict):
        job_id = job['id']
        logger.debug(f'Mesh: selfie {job_id[:8]}')
        try:
            self.api.claim_selfie_job(job_id)

            selfie_b64 = job.get('selfie_base64', '')
            if not selfie_b64:
                self.api.fail_selfie_job(job_id, 'No selfie data')
                return

            if ',' in selfie_b64:
                _, selfie_b64 = selfie_b64.split(',', 1)

            try:
                image_bytes = base64.b64decode(selfie_b64)
            except Exception as e:
                self.api.fail_selfie_job(job_id, f'base64 decode error: {e}')
                return

            embedding = self.vision_manager.process_selfie(image_bytes, timeout=15.0)
            if not embedding:
                self.api.fail_selfie_job(job_id, 'No face detected in selfie')
                return

            self.api.complete_selfie_job(job_id, embedding)
            logger.debug(f'Mesh: selfie {job_id[:8]} done')

        except Exception as e:
            logger.error(f'Mesh: selfie {job_id[:8]} failed: {e}', exc_info=True)
            self.api.fail_selfie_job(job_id, str(e))

    # ── File organise loop ────────────────────────────────────────────────────

    def _organize_loop(self):
        """Polls for file-organise tasks every 10 s (these are rare, low priority)."""
        while self._running:
            try:
                task = self.api.pull_organize_task()
                if task:
                    self._process_organize_task(task)
                else:
                    time.sleep(10)
            except Exception as e:
                logger.error(f'Organize loop error: {e}')
                time.sleep(10)

    def _process_organize_task(self, task: dict):
        task_id   = task['id']
        task_data = task.get('task_data', {})
        logger.info(f'Organise task {task_id[:8]} started')
        try:
            group_names: list[str] = task_data.get('group_names', [])
            folders: list[dict]    = task_data.get('folders', [])
            total_copied = 0

            for folder in folders:
                folder_path: str       = folder.get('folder_path', '')
                assignments: list[dict] = folder.get('assignments', [])
                unselected: list[str]  = folder.get('unselected', [])

                if not os.path.isdir(folder_path):
                    logger.warning(f'Organise: folder not found: {folder_path}')
                    continue

                # Create group subdirectories
                for gname in group_names:
                    safe = _safe_dirname(gname)
                    os.makedirs(os.path.join(folder_path, safe), exist_ok=True)
                os.makedirs(os.path.join(folder_path, 'Unselected'), exist_ok=True)

                # Copy assigned photos
                for a in assignments:
                    src  = os.path.join(folder_path, a['filename'])
                    dest = os.path.join(folder_path, _safe_dirname(a['group_name']), a['filename'])
                    if os.path.isfile(src) and not os.path.exists(dest):
                        shutil.copy2(src, dest)
                        total_copied += 1

                # Copy unselected photos
                for fname in unselected:
                    src  = os.path.join(folder_path, fname)
                    dest = os.path.join(folder_path, 'Unselected', fname)
                    if os.path.isfile(src) and not os.path.exists(dest):
                        shutil.copy2(src, dest)
                        total_copied += 1

            logger.info(f'Organise task {task_id[:8]} done — {total_copied} file(s) copied')
            self.api.complete_organize_task(task_id)

        except Exception as e:
            logger.error(f'Organise task {task_id[:8]} failed: {e}', exc_info=True)
            self.api.fail_organize_task(task_id, str(e))


def _safe_dirname(name: str) -> str:
    """Strip characters that are invalid in folder names on Windows/macOS/Linux."""
    invalid = r'\/:*?"<>|'
    safe = ''.join(c if c not in invalid else '_' for c in name)
    return safe.strip() or 'Group'
