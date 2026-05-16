"""
Telemetry Service
=================
Sends a heartbeat pulse to the API every 60 seconds, reporting
node status, RAM usage, temperature, and current upload queue depth.
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger('AIPICSQR-node')


class TelemetryService:
    def __init__(self, config, api_client, resource_monitor, upload_queue=None):
        self.config = config
        self.api = api_client
        self.resource_monitor = resource_monitor
        self.upload_queue = upload_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Telemetry stopped")

    def _loop(self):
        self._pulse()
        while self._running:
            time.sleep(self.config.pulse_interval)
            if self._running:
                self._pulse()

    def _pulse(self):
        try:
            resource_status = self.resource_monitor.get_status()
            # Inject upload queue depth so server can compute load-aware score
            if self.upload_queue is not None:
                resource_status['upload_queue_depth'] = self.upload_queue.queue_depth()
            data = self.api.pulse(resource_status)

            if data.get('status') == 'registered' and data.get('node_id'):
                logger.info(f"  Registered as node: {data['node_id'][:8]}...")

        except Exception as e:
            logger.debug(f"  Pulse failed: {e}")
