"""
Resource Monitor
================
Monitors system CPU usage and temperature to prevent
the vision service from overloading the photographer's laptop.

When limits are exceeded:
1. Pauses local processing
2. Signals the mesh network to pick up remaining jobs
3. Waits for cooldown before resuming
"""

import logging
import threading
import time
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger('AIPICSQR-node')


class ResourceMonitor:
    """
    Monitors CPU usage and temperature, signaling when resources
    should be conserved.
    
    Thresholds:
        - CPU > 90%: Pause processing
        - Temperature > 85Â°C: Pause processing  
        - Cooldown: 30 seconds before retry
    """

    def __init__(
        self,
        max_cpu_percent: int = 90,
        max_cpu_temp: float = 85.0,
        cooldown_period: int = 30,
        poll_interval: float = 3.0,
    ):
        self.max_cpu_percent = max_cpu_percent
        self.max_cpu_temp = max_cpu_temp
        self.cooldown_period = cooldown_period
        self.poll_interval = poll_interval

        self._cpu_percent: float = 0.0
        self._cpu_temp: float = 0.0
        self._paused = False
        self._pause_until: float = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Start monitoring in a background thread."""
        if psutil is None:
            logger.warning("psutil not installed. Resource monitoring disabled.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _monitor_loop(self):
        """Continuously poll system resources."""
        while self._running:
            try:
                # CPU usage (average over 1 second)
                self._cpu_percent = psutil.cpu_percent(interval=1)

                # CPU temperature
                self._cpu_temp = self._get_cpu_temp()

                # Check thresholds
                with self._lock:
                    if self._cpu_percent > self.max_cpu_percent:
                        if not self._paused:
                            logger.warning(
                                f"âš ï¸ CPU at {self._cpu_percent:.0f}% "
                                f"(limit: {self.max_cpu_percent}%). "
                                f"Pausing vision processing."
                            )
                            self._paused = True
                            self._pause_until = time.time() + self.cooldown_period

                    elif self._cpu_temp > self.max_cpu_temp:
                        if not self._paused:
                            logger.warning(
                                f"ðŸŒ¡ï¸ CPU temp at {self._cpu_temp:.1f}Â°C "
                                f"(limit: {self.max_cpu_temp}Â°C). "
                                f"Pausing vision processing."
                            )
                            self._paused = True
                            self._pause_until = time.time() + self.cooldown_period

                    elif self._paused and time.time() >= self._pause_until:
                        logger.info("âœ… Resources recovered. Resuming processing.")
                        self._paused = False

            except Exception as e:
                logger.debug(f"Resource monitor error: {e}")

            time.sleep(self.poll_interval)

    def _get_cpu_temp(self) -> float:
        """Get CPU temperature. Returns 0 if not available."""
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return 0.0

            # Try common sensor names
            for name in ['coretemp', 'cpu_thermal', 'k10temp', 'zenpower']:
                if name in temps:
                    readings = temps[name]
                    if readings:
                        return max(r.current for r in readings)

            # Fallback: return highest reading from any sensor
            all_temps = []
            for readings in temps.values():
                all_temps.extend(r.current for r in readings)
            return max(all_temps) if all_temps else 0.0

        except Exception:
            return 0.0

    def should_pause(self) -> bool:
        """Check if processing should be paused."""
        with self._lock:
            return self._paused

    @property
    def cpu_percent(self) -> float:
        """Current CPU usage percentage."""
        return self._cpu_percent

    @property
    def cpu_temp(self) -> float:
        """Current CPU temperature in Celsius."""
        return self._cpu_temp

    def get_status(self) -> dict:
        """Get current resource status for telemetry."""
        return {
            'cpu_percent': int(self._cpu_percent),
            'cpu_temp': round(self._cpu_temp, 1),
            'paused': self._paused,
        }
