"""
Telemetry Service
=================
Sends a heartbeat pulse to the API every 60 seconds, reporting
node status, CPU usage, and temperature.
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger('AIPICSQR-node')


class TelemetryService:
    def __init__(self, config, api_client, resource_monitor):
        self.config = config
        self.api = api_client
        self.resource_monitor = resource_monitor
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
            data = self.api.pulse(resource_status)

            if data.get('status') == 'registered' and data.get('node_id'):
                logger.info(f"  Registered as node: {data['node_id'][:8]}...")

        except Exception as e:
            logger.debug(f"  Pulse failed: {e}")
