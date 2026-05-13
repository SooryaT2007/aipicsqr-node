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

                # Only pause on thermal limit — CPU% check removed
                with self._lock:
                    if self._cpu_temp > self.max_cpu_temp:
                        if not self._paused:
                            logger.warning(
                                f"CPU temp at {self._cpu_temp:.1f}C "
                                f"(limit: {self.max_cpu_temp}C). "
                                f"Pausing vision processing."
                            )
                            self._paused = True
                            self._pause_until = time.time() + self.cooldown_period

                    elif self._paused and time.time() >= self._pause_until:
                        logger.info("Resources recovered. Resuming processing.")
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
        """Current runtime status for telemetry — includes live + static specs."""
        return {
            'cpu_percent': int(self._cpu_percent),
            'cpu_temp':    round(self._cpu_temp, 1),
            'paused':      self._paused,
            **self._get_system_specs(),
        }

    def _get_system_specs(self) -> dict:
        """
        Collect system hardware specs via psutil.

        RAM values change each pulse (usage fluctuates).
        Core count and frequency are stable but cheap to re-read.
        Returns an empty dict if psutil is unavailable.
        """
        if psutil is None:
            return {}
        try:
            mem  = psutil.virtual_memory()
            freq = psutil.cpu_freq()
            return {
                'total_ram_mb':     mem.total    // (1024 * 1024),
                'available_ram_mb': mem.available // (1024 * 1024),
                'cpu_cores':        psutil.cpu_count(logical=False) or 1,
                'cpu_threads':      psutil.cpu_count(logical=True)  or 1,
                'cpu_freq_mhz':     int(freq.current) if freq else 0,
            }
        except Exception as e:
            logger.debug(f'System specs collection failed: {e}')
            return {}
