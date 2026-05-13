"""
AIPICSQR Photographer Node
===========================
Watches folders for new photos, compresses them, uploads to Cloudflare R2
via the AIPICSQR API, and runs on-device face recognition.

No Supabase credentials are stored on this machine.
All backend writes go through the API using a node_token issued on registration.

Usage:
    Run AIPICSQR.bat  (recommended)
    python main.py    (manual)
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
    """
    Interactively ask the photographer for their ID, register the node,
    and persist the issued node_token. Returns True on success.
    """
    print()
    print("  Welcome to the AIPICSQR Photographer Node!")
    print("  ------------------------------------------")
    print(f"  To find your Photographer ID, open: {DASHBOARD_URL}")
    print()

    while True:
        photographer_id = input("  Enter your Photographer ID: ").strip()
        if not photographer_id:
            print("  Photographer ID cannot be empty.")
            continue

        print("  Registering node...")
        try:
            result = api.register(photographer_id)
            node_id = result.get('node_id')
            node_token = result.get('node_token')

            if not node_id or not node_token:
                print(f"  Registration failed: {result.get('error', 'unexpected response')}")
                continue

            config.apply_registration(photographer_id, node_id, node_token)
            print(f"  Registered! Node ID: {node_id[:8]}...")
            print()
            return True

        except Exception as e:
            print(f"  Could not reach API: {e}")
            retry = input("  Retry? (y/n): ").strip().lower()
            if retry != 'y':
                return False


# ── CLI command listener ──────────────────────────────────────────────────────

def print_commands():
    logger.info("Commands: status | setevent <id> | login | logout | help")


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
            logger.info(f"Photographer ID : {config.photographer_id or '<not set>'}")
            logger.info(f"Node ID         : {config.node_id or '<not set>'}")
            logger.info(f"Event ID        : {config.event_id or '<not set>'}")
            logger.info(f"Scan folders    : {config.scan_folders}")

        elif verb == 'setevent':
            if len(parts) < 2 or not parts[1].strip():
                logger.info("Usage: setevent <event_id>")
            else:
                config.event_id = parts[1].strip()
                config.save()
                logger.info(f"Event set: {config.event_id}")

        elif verb == 'login':
            webbrowser.open(DASHBOARD_URL)
            logger.info(f"Opened: {DASHBOARD_URL}")

        elif verb == 'logout':
            config.clear_pairing()
            logger.info("Node unregistered. Restart to re-pair.")
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
    logger.info("=" * 60)
    logger.info("  AIPICSQR Photographer Node v1.0.0")
    logger.info("=" * 60)

    config = Config()
    api = APIClient(config)

    # First-run: ask for Photographer ID and register
    if not config.is_registered():
        ok = first_run_setup(config, api)
        if not ok:
            logger.error("Registration cancelled. Exiting.")
            sys.exit(1)

    logger.info(f"Photographer ID : {config.photographer_id}")
    logger.info(f"Node ID         : {config.node_id}")
    logger.info(f"Event ID        : {config.event_id or '<none — use setevent <id>>'}")
    logger.info(f"Scan folders    : {config.scan_folders}")

    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info("Shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    threading.Thread(
        target=command_listener,
        args=(config, shutdown_event),
        daemon=True,
    ).start()

    if not config.event_id:
        logger.warning("No event set. Use 'setevent <event_id>' to start uploading.")
        logger.warning("Find your Event ID at: https://dashboard.aipicsqr.com/dashboard/events")

    # Start services
    resource_monitor = ResourceMonitor(
        max_cpu_percent=config.max_cpu_percent,
        max_cpu_temp=config.max_cpu_temp,
        cooldown_period=config.cooldown_period,
    )
    resource_monitor.start()
    logger.info("OK Resource Monitor started")

    vision_manager = VisionProcessManager(
        models_dir=config.models_dir,
        resource_monitor=resource_monitor,
    )
    vision_manager.start()
    logger.info("OK Vision Service started")

    uploader = PhotoUploader(config=config, api_client=api, vision_manager=vision_manager)
    logger.info("OK Photo Uploader ready")

    watcher = FolderWatcher(folders=config.scan_folders, on_new_photo=uploader.process_photo)
    watcher.start()
    logger.info(f"OK Folder Watcher started ({len(config.scan_folders)} folder(s))")

    telemetry = TelemetryService(config=config, api_client=api, resource_monitor=resource_monitor)
    telemetry.start()
    logger.info("OK Telemetry started (60 s pulse)")

    mesh_worker = MeshWorker(
        config=config,
        api_client=api,
        vision_manager=vision_manager,
        resource_monitor=resource_monitor,
    )
    mesh_worker.start()
    logger.info("OK Mesh Worker started")

    logger.info("-" * 60)
    logger.info("  Node running. Type 'help' for commands. Ctrl+C to stop.")
    logger.info("-" * 60)

    try:
        wait_for_shutdown(shutdown_event)
    finally:
        logger.info("Stopping services...")
        watcher.stop()
        telemetry.stop()
        mesh_worker.stop()
        vision_manager.stop()
        resource_monitor.stop()
        logger.info("Goodbye!")


if __name__ == '__main__':
    main()
