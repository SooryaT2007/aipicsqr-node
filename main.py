"""
AIPICSQR Photographer Node
=========================
A local desktop application for event photographers that:
1. Watches folders for new photos
2. Compresses and uploads them to Cloudflare R2
3. Runs face detection (YuNet) + recognition (SFace) for vectorization
4. Sends heartbeat pulses to the cloud dashboard
5. Participates in the mesh network for distributed processing

Requirements:
    pip install -r requirements.txt

Usage:
    python main.py
"""

import sys
import signal
import time
import threading
import logging
import webbrowser
from pathlib import Path
from shutil import copyfile

from config import Config, EXAMPLE_ENV_PATH
from services.telemetry import TelemetryService
from services.watcher import FolderWatcher
from services.uploader import PhotoUploader
from services.vision_process import VisionProcessManager
from services.resource_monitor import ResourceMonitor
from services.mesh_worker import MeshWorker
from utils.logger import setup_logger
from utils.supabase_client import get_supabase_client

LOGIN_URL = 'https://aipicsqr.vercel.app/auth/login?next=/dashboard/nodes'

# Setup logging
logger = setup_logger('AIPICSQR-node')


def ensure_env_file() -> None:
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists() and EXAMPLE_ENV_PATH.exists():
        copyfile(EXAMPLE_ENV_PATH, env_path)
        logger.info("Created .env from .env.example for first-time node setup.")


def open_login_page() -> None:
    try:
        webbrowser.open(LOGIN_URL)
        logger.info(f"Open this URL in your browser to login: {LOGIN_URL}")
    except Exception:
        logger.info(f"Please open your browser and visit: {LOGIN_URL}")


def print_commands() -> None:
    logger.info("Node commands available: login, logout, status, help")
    logger.info("  login  - open the web dashboard login page")
    logger.info("  logout - clear pairing state and require re-login")
    logger.info("  status - show current node pairing state")
    logger.info("  help   - show this message")


def command_listener(config: Config, shutdown_event: threading.Event) -> None:
    while not shutdown_event.is_set():
        try:
            command = sys.stdin.readline().strip().lower()
        except Exception:
            break

        if not command:
            continue

        if command == 'logout':
            config.clear_pairing()
            logger.info('Node pairing cleared. Please login through the web dashboard again.')
            open_login_page()
        elif command == 'login':
            open_login_page()
        elif command == 'status':
            logger.info(f"Photographer ID: {config.photographer_id or '<not configured>'}")
            logger.info(f"Event ID: {config.event_id or '<not configured>'}")
            logger.info(f"Watching folders: {config.scan_folders}")
        elif command == 'help':
            print_commands()
        else:
            logger.info('Unknown command. Type help for available commands.')


def main():
    """Main entry point for the Photographer Node."""
    logger.info("=" * 60)
    logger.info("  AIPICSQR Photographer Node v1.0.0")
    logger.info("=" * 60)

    ensure_env_file()

    # Load configuration
    config = Config()
    if not config.validate():
        logger.error("Invalid configuration. Please check your .env file.")
        sys.exit(1)

    logger.info(f"Photographer ID: {config.photographer_id or '<not configured>'}")
    logger.info(f"Event ID: {config.event_id or '<not configured>'}")
    logger.info(f"Watching folders: {config.scan_folders}")

    if not config.photographer_id:
        logger.warning("Node is not paired yet. Login through the web dashboard and set PHOTOGRAPHER_ID.")
        open_login_page()
        print_commands()

    shutdown_event = threading.Event()
    command_thread = threading.Thread(
        target=command_listener,
        args=(config, shutdown_event),
        daemon=True,
    )
    command_thread.start()

    # Initialize Supabase client
    supabase = get_supabase_client(config)

    # Initialize resource monitor
    resource_monitor = ResourceMonitor(
        max_cpu_percent=config.max_cpu_percent,
        max_cpu_temp=config.max_cpu_temp,
        cooldown_period=config.cooldown_period,
    )
    resource_monitor.start()
    logger.info("âœ“ Resource Monitor started")

    # Initialize vision process (runs in separate process)
    vision_manager = VisionProcessManager(
        models_dir=config.models_dir,
        resource_monitor=resource_monitor,
    )
    vision_manager.start()
    logger.info("âœ“ Vision Service started (isolated process)")

    # Initialize photo uploader
    uploader = PhotoUploader(
        config=config,
        supabase=supabase,
        vision_manager=vision_manager,
    )
    logger.info("âœ“ Photo Uploader ready")

    # Initialize folder watcher
    watcher = FolderWatcher(
        folders=config.scan_folders,
        on_new_photo=uploader.process_photo,
    )
    watcher.start()
    logger.info(f"âœ“ Folder Watcher started ({len(config.scan_folders)} folders)")

    # Initialize telemetry (heartbeat)
    telemetry = TelemetryService(
        config=config,
        supabase=supabase,
        resource_monitor=resource_monitor,
    )
    telemetry.start()
    logger.info("âœ“ Telemetry Service started (60s pulse)")

    # Initialize mesh worker
    mesh_worker = MeshWorker(
        config=config,
        supabase=supabase,
        vision_manager=vision_manager,
        resource_monitor=resource_monitor,
    )
    mesh_worker.start()
    logger.info("âœ“ Mesh Worker started")

    logger.info("-" * 60)
    logger.info("  Node is running. Press Ctrl+C to stop.")
    logger.info("-" * 60)

    # Handle graceful shutdown
    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info("\nShutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    finally:
        logger.info("Stopping services...")
        watcher.stop()
        telemetry.stop()
        mesh_worker.stop()
        vision_manager.stop()
        resource_monitor.stop()
        logger.info("All services stopped. Goodbye!")


if __name__ == '__main__':
    main()
