"""
AIPICSQR Photographer Node — Runner
=====================================
Headless background worker. Watches folders for new photos, compresses and
uploads them to Cloudflare R2, and processes face-recognition mesh jobs.

Configure via APP.py (log in with a Photographer ID there first).
Run headlessly via Runner.bat, or start from within APP.py.
"""

import sys
import signal
import threading
from pathlib import Path

# Run at BELOW_NORMAL priority on Windows so the vectorization workload yields
# gracefully to Lightroom, Chrome, and other apps the photographer has open.
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(-1, 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:
        pass

from config import Config
from utils.api_client import APIClient
from utils.logger import setup_logger
from services.telemetry import TelemetryService
from services.watcher import FolderWatcher
from services.uploader import PhotoUploader
from services.upload_state import UploadStateDB, MAX_RETRIES
from services.upload_queue import UploadQueue, UploadTask
from services.node_server import NodeServer
from services.vision_process import VisionProcessManager
from services.resource_monitor import ResourceMonitor
from services.mesh_worker import MeshWorker
from services.compression_worker import CompressionWorker
from services.auto_updater import AutoUpdater

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / 'logs'

logger = setup_logger('AIPICSQR-node', log_dir=str(LOG_DIR))


# ── Folder polling loop ───────────────────────────────────────────────────────

def folder_poll_loop(api: APIClient, watcher: FolderWatcher, shutdown_event: threading.Event):
    last_count = -1
    _heartbeat_every = 10  # log at INFO every 10 polls (~5 min) even when nothing changed
    _poll = 0
    while not shutdown_event.is_set():
        try:
            folders = api.get_folders()
            watcher.sync_folders(folders)
            count = len(folders)
            _poll += 1
            if count != last_count:
                logger.info(f'Folder sync: {count} active folder(s)')
                last_count = count
                _poll = 0
            elif _poll >= _heartbeat_every:
                logger.info(f'Runner alive — watching {count} folder(s)')
                _poll = 0
            else:
                logger.debug(f'Folder sync: {count} active folder(s)')
        except Exception as e:
            logger.warning(f'Folder sync failed: {e}')
            last_count = -1  # next success always logs at INFO so operator sees network recovery
        shutdown_event.wait(30)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Singleton guard: if the NodeServer port is already bound, another instance
    # is running. Exit silently rather than crashing with "address in use".
    import socket as _sock
    try:
        _s = _sock.create_connection(('127.0.0.1', 19432), timeout=0.5)
        _s.close()
        logger.info('Runner already active on port 19432 — exiting.')
        sys.exit(0)
    except OSError:
        pass  # Port not listening — safe to start

    logger.info('=' * 60)
    logger.info('  AIPICSQR Photographer Node v2.2.0')
    logger.info('=' * 60)

    config = Config()
    api = APIClient(config)

    if not config.is_registered():
        logger.error('Node not configured — open APP.py to log in with a Photographer ID.')
        sys.exit(1)

    logger.info(f'Photographer : {config.photographer_id}')
    logger.info(f'Node ID      : {config.node_id}')

    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info('Shutting down...')
        shutdown_event.set()

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ── Services ──────────────────────────────────────────────────────────────

    resource_monitor = ResourceMonitor(
        max_cpu_percent=config.max_cpu_percent,
        max_cpu_temp=config.max_cpu_temp,
        cooldown_period=config.cooldown_period,
    )
    resource_monitor.start()
    logger.info('OK Resource Monitor started')

    vision_manager = VisionProcessManager(
        models_dir=config.models_dir,
        resource_monitor=resource_monitor,
    )
    vision_manager.start()
    logger.info('OK Vision Service started')

    state_db = UploadStateDB(config.upload_state_db)
    logger.info('OK Upload State DB opened')

    # Prune stale records (complete files deleted from disk, older than 60 days)
    pruned = state_db.cleanup_old_records(days=60)
    if pruned:
        logger.info(f'   Pruned {pruned} stale record(s) from upload DB')

    # Warn about permanently-failed files so the photographer can investigate
    exhausted = state_db.get_exhausted_paths()
    if exhausted:
        logger.warning(
            f'   {len(exhausted)} file(s) failed >{MAX_RETRIES} times and will not be retried:'
        )
        for item in exhausted:
            logger.warning(f'     {Path(item["file_path"]).name} '
                           f'(tried {item["retry_count"]}x)')

    upload_queue = UploadQueue(
        max_workers=config.upload_workers,
        stats_path=config.upload_stats_file,
    )

    uploader = PhotoUploader(
        config=config,
        api_client=api,
        vision_manager=vision_manager,
        state_db=state_db,
        upload_queue=upload_queue,
        resource_monitor=resource_monitor,
    )
    upload_queue.worker_fn = uploader.process_photo
    upload_queue.start()
    logger.info(f'OK Upload Queue started ({config.upload_workers} workers)')

    watcher = FolderWatcher(
        on_new_photo=lambda fp, fi: upload_queue.submit(UploadTask(priority=0, file_path=fp, folder_info=fi)),
        upload_queue=upload_queue,
        state_db=state_db,
        api=api,
    )
    watcher.start()
    logger.info('OK Folder Watcher started')

    node_server = NodeServer(upload_queue=upload_queue, watcher=watcher)
    node_server.start()

    # Recover any files that made it to R2 but weren't confirmed before last shutdown.
    # Must run before the watcher scans folders so recovered files are already
    # marked complete and the scan skips them instead of re-compressing them.
    uploader.recover_r2_done()

    try:
        folders = api.get_folders()
        watcher.sync_folders(folders)
        logger.info(f'OK Folder sync: {len(folders)} active folder(s)')
    except Exception as e:
        logger.warning(f'Initial folder sync failed: {e}')

    threading.Thread(
        target=folder_poll_loop,
        args=(api, watcher, shutdown_event),
        daemon=True,
    ).start()

    telemetry = TelemetryService(
        config=config,
        api_client=api,
        resource_monitor=resource_monitor,
        upload_queue=upload_queue,
        vision_manager=vision_manager,  # used for startup benchmark
    )
    telemetry.start()
    logger.info('OK Telemetry started (60 s pulse + startup benchmark)')

    auto_updater = AutoUpdater()

    mesh_worker = MeshWorker(
        config=config,
        api_client=api,
        vision_manager=vision_manager,
        resource_monitor=resource_monitor,
        on_update_available=auto_updater.download_and_stage,
    )
    mesh_worker.start()
    logger.info('OK Mesh Worker started')

    compression_worker = CompressionWorker(config=config, api_client=api)
    compression_worker.start()

    logger.info('-' * 60)
    logger.info('  Runner active. Folders are managed from your dashboard.')
    logger.info('-' * 60)

    try:
        while not shutdown_event.is_set():
            # OPT-20: apply staged update when the upload queue drains
            if auto_updater.has_staged() and upload_queue.queue_depth() == 0 and upload_queue.active_count() == 0:
                logger.info('Upload queue idle — applying staged update now.')
                shutdown_event.set()
                break
            shutdown_event.wait(timeout=5.0)
    finally:
        logger.info('Stopping services...')
        api.go_offline()
        upload_queue.stop()       # wait for all active compressions/uploads to finish first
        uploader.stop()           # then flush + wait for batch sends to complete
        compression_worker.stop() # stop GDrive compression jobs
        node_server.stop()
        watcher.stop()
        telemetry.stop()
        mesh_worker.stop()
        vision_manager.stop()
        resource_monitor.stop()
        state_db.close()
        # OPT-20: launch the update batch script after all services have stopped
        if auto_updater.has_staged():
            auto_updater.run_staged()
        logger.info('Goodbye!')


if __name__ == '__main__':
    main()
