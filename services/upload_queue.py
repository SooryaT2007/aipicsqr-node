"""
Parallel upload engine with priority queue.

Priority levels (lower = higher priority):
  0 — watchdog new-file event (process immediately)
  1 — initial scan backlog
  2 — import-once one-time dump
"""

import queue
import threading
import time
import json
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional, Any


@dataclass(order=True)
class UploadTask:
    priority: int
    enqueued_at: float = field(compare=False, default_factory=time.time)
    file_path: str = field(compare=False, default='')
    folder_info: Any = field(compare=False, default=None)


class UploadQueue:
    def __init__(self, worker_fn: Optional[Callable] = None,
                 max_workers: int = 6, stats_path: Optional[str] = None):
        self.worker_fn = worker_fn
        self.max_workers = max_workers
        self.stats_path = stats_path

        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._active_count = 0
        self._active_lock = threading.Lock()

        self._completed_today = 0
        self._failed_today = 0
        self._recent: deque = deque(maxlen=200)
        self._speed_window: deque = deque()  # (timestamp, bytes)
        self._stats_lock = threading.Lock()

        self._running = False
        self._stats_thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(self, task: UploadTask) -> None:
        self._q.put(task)

    def queue_depth(self) -> int:
        return self._q.qsize()

    def active_count(self) -> int:
        with self._active_lock:
            return self._active_count

    def get_stats(self) -> dict:
        with self._stats_lock:
            speed = self._compute_speed()
            depth = self.queue_depth()
            eta = int(depth / (speed / 60)) if speed > 0 else None
            return {
                'queue_depth':      depth,
                'active':           self.active_count(),
                'completed_today':  self._completed_today,
                'failed_today':     self._failed_today,
                'speed_per_min':    speed,
                'eta_seconds':      eta,
                'recent':           list(self._recent),
            }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers,
                                            thread_name_prefix='upload-worker')
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name='upload-dispatcher')
        self._dispatch_thread.start()

        if self.stats_path:
            self._stats_thread = threading.Thread(
                target=self._stats_writer_loop, daemon=True, name='upload-stats-writer')
            self._stats_thread.start()

    def stop(self) -> None:
        self._running = False
        # unblock the dispatch loop
        self._q.put(UploadTask(priority=-1, file_path='__STOP__'))
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _dispatch_loop(self) -> None:
        while self._running:
            try:
                task = self._q.get(timeout=1)
            except queue.Empty:
                continue

            if task.file_path == '__STOP__':
                break

            with self._active_lock:
                self._active_count += 1

            self._executor.submit(self._run_task, task)

    def _run_task(self, task: UploadTask) -> None:
        try:
            if self.worker_fn:
                self.worker_fn(task.file_path, task.folder_info)
            self._record_completion(task, success=True)
        except Exception:
            self._record_completion(task, success=False)
        finally:
            with self._active_lock:
                self._active_count -= 1
            self._q.task_done()

    def _record_completion(self, task: UploadTask, success: bool) -> None:
        name = os.path.basename(task.file_path)
        size_kb = 0
        try:
            size_kb = os.path.getsize(task.file_path) // 1024
        except OSError:
            pass

        with self._stats_lock:
            if success:
                self._completed_today += 1
                self._speed_window.append((time.time(), size_kb))
                self._recent.appendleft({
                    'name': name, 'status': 'complete', 'size_kb': size_kb})
            else:
                self._failed_today += 1
                self._recent.appendleft({
                    'name': name, 'status': 'failed', 'size_kb': size_kb})

    def _compute_speed(self) -> int:
        """Photos completed per minute over the last 60 seconds."""
        cutoff = time.time() - 60
        while self._speed_window and self._speed_window[0][0] < cutoff:
            self._speed_window.popleft()
        return len(self._speed_window)

    def _stats_writer_loop(self) -> None:
        while self._running:
            try:
                stats = self.get_stats()
                tmp = self.stats_path + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump(stats, f)
                os.replace(tmp, self.stats_path)
            except Exception:
                pass
            time.sleep(2)
