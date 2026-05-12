"""
Telemetry Service
=================
Sends heartbeat pulses to the cloud dashboard every 60 seconds,
reporting the node's status, CPU usage, and temperature.

Also receives pending vectoring jobs from the server on each pulse.
"""

import logging
import threading
import time
import socket
import requests
from typing import Optional

logger = logging.getLogger('AIPICSQR-node')


class TelemetryService:
    """
    Sends periodic heartbeat pulses to the AIPICSQR API.
    """

    def __init__(self, config, supabase, resource_monitor):
        self.config = config
        self.supabase = supabase
        self.resource_monitor = resource_monitor
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._node_id = config.node_id or None

    def start(self):
        """Start the telemetry heartbeat loop."""
        self._running = True
        self._thread = threading.Thread(target=self._pulse_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the telemetry loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Telemetry stopped")

    def _pulse_loop(self):
        """Send pulse every N seconds."""
        # Send first pulse immediately
        self._send_pulse()

        while self._running:
            time.sleep(self.config.pulse_interval)
            if self._running:
                self._send_pulse()

    def _send_pulse(self):
        """Send a single heartbeat pulse to the API."""
        if not self.config.photographer_id:
            logger.info("Telemetry paused: photographer ID not configured.")
            return

        try:
            resource_status = self.resource_monitor.get_status()

            payload = {
                'node_id': self._node_id,
                'photographer_id': self.config.photographer_id,
                'hostname': socket.gethostname(),
                'ip_address': self._get_local_ip(),
                'cpu_percent': resource_status['cpu_percent'],
                'cpu_temp': resource_status['cpu_temp'],
                'status': 'busy' if resource_status['paused'] else 'online',
            }

            response = requests.post(
                f"{self.config.api_base_url}/api/nodes/pulse",
                json=payload,
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()

                # Save node_id from first registration
                if data.get('status') == 'registered' and data.get('node_id'):
                    self._node_id = data['node_id']
                    logger.info(f"  ðŸ“¡ Registered as node: {self._node_id[:8]}...")

                # Check for pending jobs
                pending = data.get('pending_jobs', [])
                if pending:
                    logger.info(f"  ðŸ“¡ {len(pending)} pending job(s) available")

            else:
                logger.warning(f"  Pulse failed: HTTP {response.status_code}")

        except requests.exceptions.ConnectionError:
            logger.debug("  Pulse failed: API unreachable")
        except Exception as e:
            logger.warning(f"  Pulse error: {e}")

    def _get_local_ip(self) -> str:
        """Get the local network IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
