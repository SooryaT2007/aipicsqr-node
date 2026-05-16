"""
Local HTTP IPC server — Runner.py listens on 127.0.0.1:19432.
APP.py calls it via urllib.request (stdlib, no extra deps).

Endpoints:
  GET  /stats        — current upload stats JSON
  POST /import-once  — trigger a one-time folder import
                       body: {folder_path, event_id, pool_type, folder_id}
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.upload_queue import UploadQueue
    from services.watcher import FolderWatcher

logger = logging.getLogger('AIPICSQR-node')

PORT = 19432


class _Handler(BaseHTTPRequestHandler):
    upload_queue = None
    watcher = None

    def log_message(self, *_):
        pass  # suppress access log spam

    def do_GET(self):
        if self.path == '/stats':
            stats = self.upload_queue.get_stats() if self.upload_queue else {}
            self._json(200, stats)
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/import-once':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                self._json(400, {'error': 'invalid JSON'})
                return

            folder_path = data.get('folder_path', '')
            event_id    = data.get('event_id', '')
            pool_type   = data.get('pool_type', 'public')
            folder_id   = data.get('folder_id')

            if not folder_path or not event_id:
                self._json(400, {'error': 'folder_path and event_id required'})
                return

            count = 0
            if self.watcher:
                count = self.watcher.import_once(folder_path, event_id, pool_type, folder_id)
            self._json(200, {'queued': count})
        else:
            self._json(404, {'error': 'not found'})

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NodeServer:
    def __init__(self, upload_queue, watcher):
        _Handler.upload_queue = upload_queue
        _Handler.watcher = watcher
        self._server = HTTPServer(('127.0.0.1', PORT), _Handler)
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name='node-ipc-server')
        self._thread.start()
        logger.info(f'OK Node IPC server on 127.0.0.1:{PORT}')

    def stop(self):
        self._server.shutdown()
