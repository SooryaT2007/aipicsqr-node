"""
AIPICSQR Photographer Node
===========================
Watches folders for new photos, compresses them, uploads to Cloudflare R2
via the AIPICSQR API, and runs on-device face recognition.

No Supabase credentials are stored on this machine.
All backend writes go through the API using a node_token issued on registration.

Flow:
  1. First run: prompt for Photographer ID → register → store node_token
  2. Every 30 s: poll /api/node/folders for active watch folders
  3. For each new folder: start watching it
  4. For each new photo: compress → upload → face-scan (if private pool) → notify API

Usage:
    Run.bat          (recommended — double-click)
    python main.py   (manual)
"""

import sys
import signal
import threading
import webbrowser

from config import Config
from utils.api_client import APIClient
from utils.logger import setup_logger
from services.telemetry import TelemetryService
from services.watcher import FolderWatcher
from services.uploader import PhotoUploader
from services.vision_process import VisionProcessManager
from services.resource_monitor import ResourceMonitor
from services.mesh_worker import MeshWorker

DASHBOARD_URL = 'https://dashboard.aipicsqr.com/dashboard/nodes'

logger = setup_logger('AIPICSQR-node')


# ── First-run setup ───────────────────────────────────────────────────────────

def first_run_setup(config: Config, api: APIClient) -> bool:
    print()
    print('  Welcome to the AIPICSQR Photographer Node!')
    print('  ------------------------------------------')
    print(f'  To find your Photographer ID, open: {DASHBOARD_URL}')
    print()

    while True:
        photographer_id = input('  Enter your Photographer ID: ').strip()
        if not photographer_id:
            print('  Photographer ID cannot be empty.')
            continue

        print('  Registering node...')
        try:
            result = api.register(photographer_id)
            node_id = result.get('node_id')
            node_token = result.get('node_token')

            if not node_id or not node_token:
                print(f"  Registration failed: {result.get('error', 'unexpected response')}")
                continue

            config.apply_registration(photographer_id, node_id, node_token)
            print(f'  Registered! Node ID: {node_id[:8]}...')
            print()
            return True

        except Exception as e:
            print(f'  Could not reach API: {e}')
            retry = input('  Retry? (y/n): ').strip().lower()
            if retry != 'y':
                return False


# ── Folder polling loop ───────────────────────────────────────────────────────

def folder_poll_loop(api: APIClient, watcher: FolderWatcher, shutdown_event: threading.Event):
    """Poll /api/node/folders every 30 s and sync the watcher."""
    while not shutdown_event.is_set():
        try:
            folders = api.get_folders()
            watcher.sync_folders(folders)
            if folders:
                logger.info(f'Folder sync: {len(folders)} active folder(s)')
            else:
                logger.debug('Folder sync: no active folders (add them from the dashboard)')
        except Exception as e:
            logger.warning(f'Folder sync failed: {e}')
        shutdown_event.wait(30)


# ── CLI command listener ──────────────────────────────────────────────────────

def print_commands():
    logger.info('Commands: status | login | logout | help')


def command_listener(config: Config, shutdown_event: threading.Event):
    while not shutdown_event.is_set():
        try:
            raw = sys.stdin.readline()
        except Exception:
            break

        cmd = raw.strip().lower()
        if not cmd:
            continue

        parts = raw.strip().split(None, 1)
        verb = parts[0].lower()

        if verb == 'status':
            logger.info(f'Photographer ID : {config.photographer_id or "<not set>"}')
            logger.info(f'Node ID         : {config.node_id or "<not set>"}')

        elif verb == 'login':
            webbrowser.open(DASHBOARD_URL)
            logger.info(f'Opened: {DASHBOARD_URL}')

        elif verb == 'logout':
            config.clear_pairing()
            logger.info('Node unregistered. Restart to re-pair.')
            shutdown_event.set()

        elif verb == 'help':
            print_commands()

        else:
            logger.info(f"Unknown command '{verb}'. Type 'help'.")


# ── Shutdown helper ───────────────────────────────────────────────────────────

def wait_for_shutdown(shutdown_event: threading.Event):
    while not shutdown_event.is_set():
        shutdown_event.wait(timeout=1.0)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info('=' * 60)
    logger.info('  AIPICSQR Photographer Node v2.0.0')
    logger.info('=' * 60)

    config = Config()
    api = APIClient(config)

    if not config.is_registered():
        ok = first_run_setup(config, api)
        if not ok:
            logger.error('Registration cancelled. Exiting.')
            sys.exit(1)

    logger.info(f'Photographer ID : {config.photographer_id}')
    logger.info(f'Node ID         : {config.node_id}')
    logger.info('  Folders are managed from your AIPICSQR dashboard.')

    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info('Shutting down...')
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    threading.Thread(
        target=command_listener,
        args=(config, shutdown_event),
        daemon=True,
    ).start()

    # Start services
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

    uploader = PhotoUploader(config=config, api_client=api, vision_manager=vision_manager)
    logger.info('OK Photo Uploader ready')

    watcher = FolderWatcher(on_new_photo=uploader.process_photo)
    watcher.start()
    logger.info('OK Folder Watcher started')

    # Initial folder sync — then repeat every 30 s in background
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

    telemetry = TelemetryService(config=config, api_client=api, resource_monitor=resource_monitor)
    telemetry.start()
    logger.info('OK Telemetry started (60 s pulse)')

    mesh_worker = MeshWorker(
        config=config,
        api_client=api,
        vision_manager=vision_manager,
        resource_monitor=resource_monitor,
    )
    mesh_worker.start()
    logger.info('OK Mesh Worker started')

    logger.info('-' * 60)
    logger.info("  Node running. Go to your dashboard to add watch folders.")
    logger.info("  Type 'help' for commands. Ctrl+C to stop.")
    logger.info('-' * 60)

    try:
        wait_for_shutdown(shutdown_event)
    finally:
        logger.info('Stopping services...')
        watcher.stop()
        telemetry.stop()
        mesh_worker.stop()
        vision_manager.stop()
        resource_monitor.stop()
        logger.info('Goodbye!')


if __name__ == '__main__':
    main()
