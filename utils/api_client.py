"""
API client for all communication with the AIPICSQR backend.
The node never touches Supabase directly — everything goes through this client.
Authentication is a node_token issued on registration (stored in node_config.json).
"""

import socket
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger('AIPICSQR-node')

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES    = 3
_BACKOFF_FACTOR = 1.5   # sleeps: 0 s, 1.5 s, 3 s
_CIRCUIT_TRIP   = 3     # consecutive failures before suppressing retry noise


class APIClient:
    def __init__(self, config):
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({'Content-Type': 'application/json'})

        # Retry on HTTP 5xx / rate-limit responses automatically
        _adapter = HTTPAdapter(max_retries=Retry(
            total=_MAX_RETRIES,
            backoff_factor=_BACKOFF_FACTOR,
            status_forcelist=_RETRY_STATUSES,
            allowed_methods=frozenset({'GET', 'POST', 'PUT'}),
            raise_on_status=False,
        ))
        self._session.mount('https://', _adapter)
        self._session.mount('http://',  _adapter)

        # Circuit breaker: after _CIRCUIT_TRIP consecutive connection failures,
        # stop retrying and suppress per-call logs (single attempt, silent failure)
        # until the next request succeeds, at which point we log "reconnected" once.
        self._fail_streak   = 0
        self._circuit_open  = False

        # Verify certifi CA bundle at startup — a missing cacert.pem (common after
        # a partial uninstall) silently breaks ALL HTTPS requests.  Fail fast here
        # so the log shows one clear message rather than hundreds of SSL errors.
        try:
            import certifi as _certifi
            import os as _os
            _ca = _certifi.where()
            if not _os.path.isfile(_ca):
                logger.critical(
                    f'TLS CA bundle missing: {_ca} — '
                    'all HTTPS requests will fail. '
                    'Open Installer.py and click "Repair / Reinstall" to fix.'
                )
        except ImportError:
            pass  # certifi not installed yet; installer will handle it

    # ── Internal: retry on DNS / connection errors ────────────────────────────

    def _post(self, url: str, **kwargs) -> requests.Response:
        return self._req('POST', url, **kwargs)

    def _put(self, url: str, **kwargs) -> requests.Response:
        return self._req('PUT', url, **kwargs)

    def _req(self, method: str, url: str, **kwargs) -> requests.Response:
        """Wraps session.request with retries on transient connection/timeout errors.

        Circuit breaker: after _CIRCUIT_TRIP consecutive failures the circuit
        opens — calls fast-fail with a single attempt and no log output.  The
        first successful request closes the circuit and logs 'reconnected' once.
        """
        last_exc: Exception = RuntimeError('no attempts made')
        attempts = 1 if self._circuit_open else _MAX_RETRIES
        for attempt in range(attempts):
            try:
                resp = self._session.request(method, url, **kwargs)
                if self._circuit_open:
                    logger.info('Network reconnected')
                self._fail_streak  = 0
                self._circuit_open = False
                return resp
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                if not self._circuit_open and attempt < attempts - 1:
                    wait = _BACKOFF_FACTOR * (2 ** attempt)
                    logger.debug(f'Network error (attempt {attempt + 1}), retrying in {wait:.0f}s: {exc}')
                    time.sleep(wait)

        self._fail_streak += 1
        if self._fail_streak == _CIRCUIT_TRIP:
            self._circuit_open = True
            logger.warning('Network offline — retry noise suppressed until reconnected')
        raise last_exc

    # ── Auth header ───────────────────────────────────────────────────────────

    def _auth(self) -> dict:
        return {'node_token': self._config.node_token}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, photographer_id: str) -> dict:
        """
        Register this node. Returns {'node_id': str, 'node_token': str}.
        Called once on first run; result is persisted in node_config.json.
        """
        payload = {
            'photographer_id': photographer_id,
            'hostname': socket.gethostname(),
            'ip_address': self._local_ip(),
        }
        resp = self._post(
            f'{self._config.api_base_url}/api/node/register',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def pulse(self, resource_status: dict, startup_benchmark_ms: int | None = None) -> dict:
        payload = {
            **self._auth(),
            'node_id':           self._config.node_id,
            'photographer_id':   self._config.photographer_id,
            'hostname':          socket.gethostname(),
            'ip_address':        self._local_ip(),
            'cpu_temp':          resource_status.get('cpu_temp', 0),
            'status':            'busy' if resource_status.get('paused') else 'online',
            # Hardware specs used for server-side performance scoring
            'total_ram_mb':      resource_status.get('total_ram_mb'),
            'available_ram_mb':  resource_status.get('available_ram_mb'),
            'cpu_cores':         resource_status.get('cpu_cores'),
            'cpu_threads':       resource_status.get('cpu_threads'),
            'cpu_freq_mhz':      resource_status.get('cpu_freq_mhz'),
            # Upload load signal — server applies upload penalty to score
            'upload_queue_depth': resource_status.get('upload_queue_depth', 0),
        }
        # Startup benchmark — sent once; primes avg_job_ms before any real jobs run
        if startup_benchmark_ms is not None:
            payload['startup_benchmark_ms'] = startup_benchmark_ms
        resp = self._post(
            f'{self._config.api_base_url}/api/nodes/pulse',
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Folder polling ────────────────────────────────────────────────────────

    def get_events(self) -> list:
        """Returns active events for this photographer — used by addfolder command."""
        resp = self._post(
            f'{self._config.api_base_url}/api/node/events',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('events', [])

    def add_folder(self, event_id: str, path: str, pool_type: str, watch_hours: int) -> dict:
        """Register a new watch folder via the node addfolder command."""
        payload = {
            **self._auth(),
            'event_id': event_id,
            'path': path,
            'pool_type': pool_type,
            'watch_hours': watch_hours,
        }
        resp = self._post(
            f'{self._config.api_base_url}/api/node/folders/add',
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_folders(self) -> list:
        """
        Fetch all active watch folders for this photographer's events.
        Each entry: {id, event_id, path, pool_type, watch_until}
        Called every 30 s; node dynamically adjusts which paths it watches.
        """
        resp = self._post(
            f'{self._config.api_base_url}/api/node/folders',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('folders', [])

    # ── Upload ────────────────────────────────────────────────────────────────

    def get_upload_url(self, filename: str, event_id: str, folder_id: str | None = None) -> dict:
        """
        Request a presigned R2 URL. Returns {'upload_url': str, 'photo_id': str}.
        The node then PUTs the photo bytes directly to upload_url — no file bytes
        touch our server, so Vercel bandwidth is not consumed.
        """
        payload = {
            **self._auth(),
            'event_id': event_id,
            'filename': filename,
            'content_type': 'image/jpeg',
        }
        if folder_id:
            payload['folder_id'] = folder_id
        resp = self._post(
            f'{self._config.api_base_url}/api/upload/presign',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_complete(
        self,
        photo_id: str,
        file_size: int,
        width: int,
        height: int,
        thumbnail_key: str | None = None,
        thumbnail_url: str | None = None,
    ) -> dict:
        """
        Notify the server that a single photo has been uploaded to R2.
        For bulk uploads prefer upload_complete_batch() to reduce Vercel invocations.
        """
        payload = {
            **self._auth(),
            'photo_id':        photo_id,
            'file_size_bytes': file_size,
            'width':           width,
            'height':          height,
            'thumbnail_key':   thumbnail_key,
            'thumbnail_url':   thumbnail_url,
        }
        resp = self._post(
            f'{self._config.api_base_url}/api/node/upload/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_complete_batch(self, photos: list[dict]) -> dict:
        """
        Notify the server that multiple photos have been uploaded to R2.
        photos: list of dicts with photo_id, file_size_bytes, width, height,
                thumbnail_key (optional), thumbnail_url (optional).
        Max 50 per call.
        """
        payload = {**self._auth(), 'photos': photos}
        resp = self._post(
            f'{self._config.api_base_url}/api/node/upload/complete-batch',
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Mesh jobs ─────────────────────────────────────────────────────────────

    def pull_jobs(self) -> list:
        """
        Atomically claim vectoring jobs via pull_vector_tasks (SKIP LOCKED).
        Batch size is determined entirely by the server from the stored
        performance_score — the node does not calculate or send one.
        Returns a list of pre-claimed job dicts: job_id, photo_id, r2_url,
        assigned_at, is_urgent.
        """
        payload = self._auth()
        resp = self._post(
            f'{self._config.api_base_url}/api/node/jobs',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get('jobs', [])

    def complete_job(
        self,
        job_id: str,
        photo_id: str,
        face_results: list,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> dict:
        """
        Report a completed vectoring job.
        started_at  — ISO timestamp captured just before ONNX inference begins,
                      after the image is downloaded. Used by the server to compute
                      true per-image job_ms (not inflated by batch queue wait).
        completed_at — ISO timestamp captured immediately after inference finishes.
        """
        face_vectors = [
            {
                'embedding':  f['embedding'],
                'bbox':       f['bbox'],
                'confidence': f['confidence'],
            }
            for f in face_results
        ]
        payload = {
            **self._auth(),
            'photo_id':     photo_id,
            'face_vectors': face_vectors,
            'started_at':   started_at,
            'completed_at': completed_at,
        }
        resp = self._post(
            f'{self._config.api_base_url}/api/node/jobs/{job_id}/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def fail_job(self, job_id: str, error: str) -> None:
        try:
            self._post(
                f'{self._config.api_base_url}/api/node/jobs/{job_id}/fail',
                json={**self._auth(), 'error': error},
                timeout=10,
            )
        except Exception:
            pass

    # ── Selfie jobs (guest face matching) ────────────────────────────────────

    def get_selfie_jobs(self) -> list:
        """Fetch face_search_jobs assigned to this node (guest selfies awaiting embedding)."""
        resp = self._post(
            f'{self._config.api_base_url}/api/node/selfie-jobs',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('jobs', [])

    def claim_selfie_job(self, job_id: str) -> dict:
        resp = self._post(
            f'{self._config.api_base_url}/api/node/selfie-jobs/{job_id}/claim',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def complete_selfie_job(self, job_id: str, embedding: list) -> dict:
        """Send the 128-dim selfie embedding; server runs pgvector match and stores results."""
        payload = {**self._auth(), 'embedding': embedding}
        resp = self._post(
            f'{self._config.api_base_url}/api/node/selfie-jobs/{job_id}/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def fail_selfie_job(self, job_id: str, error: str) -> None:
        try:
            self._post(
                f'{self._config.api_base_url}/api/node/selfie-jobs/{job_id}/fail',
                json={**self._auth(), 'error': error},
                timeout=10,
            )
        except Exception:
            pass

    def complete_folder(self, folder_id: str) -> None:
        """Mark an import-once folder as done (scan submitted, no more watching)."""
        self._post(
            f'{self._config.api_base_url}/api/node/folders/complete',
            json={**self._auth(), 'folder_id': folder_id},
            timeout=10,
        ).raise_for_status()

    def report_scan_total(self, folder_id: str, total: int) -> None:
        """Report the total number of image files found during the initial scan.
        Used as the denominator for upload/processing progress bars in the dashboard."""
        self._post(
            f'{self._config.api_base_url}/api/node/folders/scan-total',
            json={**self._auth(), 'folder_id': folder_id, 'scan_total': total},
            timeout=10,
        ).raise_for_status()

    # ── Graceful shutdown ─────────────────────────────────────────────────────

    def go_offline(self) -> None:
        """Mark this node offline immediately on graceful shutdown.
        Best-effort: if the network is down the pulse timeout cleans up naturally."""
        try:
            self._post(
                f'{self._config.api_base_url}/api/node/offline',
                json=self._auth(),
                timeout=5,
            )
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
