"""
Telemetry Service
=================
Sends a heartbeat pulse to the API every 60 seconds, reporting node
status, RAM, temperature, and upload queue depth.

Startup benchmark
─────────────────
On the very first pulse the service runs a sample vectorization on a
synthetic 640×480 image and adds 3 000 ms (transfer overhead allowance).
This seeds avg_job_ms on the server so the load balancer has real data
from the first job assignment instead of waiting several jobs for warmup.
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger('AIPICSQR-node')


class TelemetryService:
    def __init__(self, config, api_client, resource_monitor, upload_queue=None, vision_manager=None):
        self.config = config
        self.api = api_client
        self.resource_monitor = resource_monitor
        self.upload_queue = upload_queue
        self.vision_manager = vision_manager
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._benchmark_ms: Optional[int] = None  # set once on first pulse

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info('Telemetry stopped')

    def _loop(self):
        # Run benchmark before first pulse so the timing is ready
        self._benchmark_ms = self._run_benchmark()
        self._pulse()
        while self._running:
            time.sleep(self.config.pulse_interval)
            if self._running:
                self._pulse()

    def _pulse(self):
        try:
            resource_status = self.resource_monitor.get_status()
            if self.upload_queue is not None:
                resource_status['upload_queue_depth'] = self.upload_queue.queue_depth()

            extra = {}
            if self._benchmark_ms is not None:
                extra['startup_benchmark_ms'] = self._benchmark_ms
                self._benchmark_ms = None  # send only once

            data = self.api.pulse(resource_status, **extra)

            # Update config's performance_score so MeshWorker can read it
            score = data.get('performance_score')
            if isinstance(score, int) and score > 0:
                self.config.performance_score = score

            if data.get('status') == 'registered' and data.get('node_id'):
                logger.info(f'  Registered as node: {data["node_id"][:8]}...')

        except Exception as e:
            logger.debug(f'  Pulse failed: {e}')

    def _run_benchmark(self) -> Optional[int]:
        """
        Vectorize a synthetic image and measure the full pipeline time.
        Returns elapsed_ms + 3000 (transfer overhead), or None on failure.
        Uses process_image (not process_selfie) so no misleading "no face" warning fires.
        """
        if self.vision_manager is None:
            return None
        import os
        import tempfile
        try:
            from PIL import Image
            img = Image.new('RGB', (640, 480), color=(100, 120, 140))
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                img.save(tmp, format='JPEG', quality=85)
                tmp_path = tmp.name

            try:
                t0 = time.time()
                self.vision_manager.process_image(tmp_path, timeout=30.0)
                elapsed_ms = int((time.time() - t0) * 1000)
            finally:
                os.unlink(tmp_path)

            benchmark = elapsed_ms + 3000  # +3 s transfer allowance per design
            logger.info(f'  Startup benchmark: {elapsed_ms} ms inference + 3000 ms overhead = {benchmark} ms')
            return benchmark
        except Exception as e:
            logger.debug(f'  Startup benchmark failed: {e}')
            return None
