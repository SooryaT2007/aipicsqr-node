"""
Vision Process Manager
======================
Isolates the Vision Service (YuNet + SFace) into a separate
Python process using multiprocessing, so it doesn't compete
with YT/LED live streaming for CPU resources.

Communication between main process and vision process happens
via multiprocessing.Queue.
"""

import multiprocessing
import logging
import time
from typing import List, Optional
from multiprocessing import Process, Queue
from pathlib import Path

logger = logging.getLogger('AIPICSQR-node')

# Message types
MSG_PROCESS_IMAGE = 'process_image'
MSG_PROCESS_SELFIE = 'process_selfie'
MSG_SHUTDOWN = 'shutdown'
MSG_RESULT = 'result'
MSG_ERROR = 'error'


def _vision_worker(models_dir: str, task_queue: Queue, result_queue: Queue):
    """
    Worker function that runs in a separate process.
    Loads vision models and processes images from the task queue.
    """
    import os
    
    # Set process priority to below normal (Windows)
    try:
        import psutil
        p = psutil.Process(os.getpid())
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    from services.vision import VisionService

    vision = VisionService(models_dir)
    if not vision.initialize():
        result_queue.put({
            'type': MSG_ERROR,
            'error': 'Failed to initialize vision models',
        })
        return

    result_queue.put({'type': MSG_RESULT, 'status': 'ready'})

    while True:
        try:
            task = task_queue.get(timeout=1.0)
        except Exception:
            continue

        if task['type'] == MSG_SHUTDOWN:
            break

        try:
            if task['type'] == MSG_PROCESS_IMAGE:
                results = vision.process_image(
                    task['image_path'],
                    task.get('confidence_threshold', 0.7),
                )
                result_queue.put({
                    'type': MSG_RESULT,
                    'task_id': task.get('task_id'),
                    'results': results,
                })

            elif task['type'] == MSG_PROCESS_SELFIE:
                embedding = vision.process_selfie(task['image_data'])
                result_queue.put({
                    'type': MSG_RESULT,
                    'task_id': task.get('task_id'),
                    'embedding': embedding,
                })

        except Exception as e:
            result_queue.put({
                'type': MSG_ERROR,
                'task_id': task.get('task_id'),
                'error': str(e),
            })


class VisionProcessManager:
    """
    Manages the vision service running in an isolated process.
    
    Usage:
        manager = VisionProcessManager(models_dir='./models')
        manager.start()
        results = manager.process_image('/path/to/photo.jpg')
        manager.stop()
    """

    def __init__(self, models_dir: str, resource_monitor=None):
        self.models_dir = str(models_dir)
        self.resource_monitor = resource_monitor
        self._process: Optional[Process] = None
        self._task_queue: Optional[Queue] = None
        self._result_queue: Optional[Queue] = None
        self._ready = False
        self._task_counter = 0

    def start(self):
        """Start the vision worker process."""
        self._task_queue = Queue()
        self._result_queue = Queue()

        self._process = Process(
            target=_vision_worker,
            args=(self.models_dir, self._task_queue, self._result_queue),
            daemon=True,
            name='AIPICSQR-vision',
        )
        self._process.start()

        # Wait for initialization
        try:
            result = self._result_queue.get(timeout=30)
            if result.get('status') == 'ready':
                self._ready = True
                logger.info("  Vision process initialized successfully")
            else:
                logger.error(f"  Vision process error: {result.get('error')}")
        except Exception:
            logger.error("  Vision process timed out during initialization")

    def stop(self):
        """Stop the vision worker process."""
        if self._task_queue and self._process and self._process.is_alive():
            self._task_queue.put({'type': MSG_SHUTDOWN})
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.terminate()
        logger.info("Vision process stopped")

    def is_ready(self) -> bool:
        """Check if the vision process is ready to accept tasks."""
        return self._ready and self._process is not None and self._process.is_alive()

    def should_delegate(self) -> bool:
        """
        Check if processing should be delegated to mesh network
        based on resource constraints.
        """
        if self.resource_monitor:
            return self.resource_monitor.should_pause()
        return False

    def process_image(self, image_path: str, confidence_threshold: float = 0.7, timeout: float = 30.0) -> List[dict]:
        """
        Send an image to the vision process for face detection + recognition.
        
        Args:
            image_path: Path to the image file
            confidence_threshold: Minimum face detection confidence
            timeout: Maximum time to wait for results
        
        Returns:
            List of face results with embeddings
        """
        if not self.is_ready():
            logger.warning("Vision process not ready")
            return []

        if self.should_delegate():
            logger.info(f"  âš¡ Delegating {Path(image_path).name} to mesh (resource limit hit)")
            return []  # Caller should re-queue for mesh processing

        self._task_counter += 1
        task_id = self._task_counter

        self._task_queue.put({
            'type': MSG_PROCESS_IMAGE,
            'task_id': task_id,
            'image_path': image_path,
            'confidence_threshold': confidence_threshold,
        })

        # Wait for result
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = self._result_queue.get(timeout=1.0)
                if result.get('task_id') == task_id:
                    if result['type'] == MSG_RESULT:
                        return result.get('results', [])
                    elif result['type'] == MSG_ERROR:
                        logger.error(f"Vision error: {result.get('error')}")
                        return []
            except Exception:
                continue

        logger.warning(f"Vision process timed out for {image_path}")
        return []

    def process_selfie(self, image_data: bytes, timeout: float = 10.0) -> Optional[List[float]]:
        """
        Process a selfie and return the 512-dim embedding.
        
        Args:
            image_data: Raw image bytes
            timeout: Maximum wait time
        
        Returns:
            512-dim embedding list or None
        """
        if not self.is_ready():
            return None

        self._task_counter += 1
        task_id = self._task_counter

        self._task_queue.put({
            'type': MSG_PROCESS_SELFIE,
            'task_id': task_id,
            'image_data': image_data,
        })

        start = time.time()
        while time.time() - start < timeout:
            try:
                result = self._result_queue.get(timeout=1.0)
                if result.get('task_id') == task_id:
                    return result.get('embedding')
            except Exception:
                continue

        return None
