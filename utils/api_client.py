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

    # ── Internal: retry on DNS / connection errors ────────────────────────────

    def _post(self, url: str, **kwargs) -> requests.Response:
        return self._req('POST', url, **kwargs)

    def _put(self, url: str, **kwargs) -> requests.Response:
        return self._req('PUT', url, **kwargs)

    def _req(self, method: str, url: str, **kwargs) -> requests.Response:
        """Wraps session.request with retries on transient connection errors."""
        last_exc: Exception = RuntimeError('no attempts made')
        for attempt in range(_MAX_RETRIES):
            try:
                return self._session.request(method, url, **kwargs)
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    wait = _BACKOFF_FACTOR * (2 ** attempt)
                    logger.debug(f'Network error (attempt {attempt + 1}), retrying in {wait:.0f}s: {exc}')
                    time.sleep(wait)
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

    def pulse(self, resource_status: dict) -> dict:
        payload = {
            **self._auth(),
            'node_id':        self._config.node_id,
            'photographer_id': self._config.photographer_id,
            'hostname':       socket.gethostname(),
            'ip_address':     self._local_ip(),
            'cpu_percent':    resource_status.get('cpu_percent', 0),
            'cpu_temp':       resource_status.get('cpu_temp', 0),
            'status':         'busy' if resource_status.get('paused') else 'online',
            # Extended hardware specs
            'total_ram_mb':     resource_status.get('total_ram_mb'),
            'available_ram_mb': resource_status.get('available_ram_mb'),
            'cpu_cores':        resource_status.get('cpu_cores'),
            'cpu_threads':      resource_status.get('cpu_threads'),
            'cpu_freq_mhz':     resource_status.get('cpu_freq_mhz'),
        }
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
    ) -> dict:
        """
        Notify the server that the photo has been uploaded to R2.
        The server marks it done (public pool) or creates a vectoring_job
        assigned to the highest-score online node (private pool).
        Face detection is never done by the uploading node.
        """
        payload = {
            **self._auth(),
            'photo_id':        photo_id,
            'file_size_bytes': file_size,
            'width':           width,
            'height':          height,
        }
        resp = self._post(
            f'{self._config.api_base_url}/api/node/upload/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Mesh jobs ─────────────────────────────────────────────────────────────

    def get_jobs(self) -> list:
        resp = self._post(
            f'{self._config.api_base_url}/api/node/jobs',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('jobs', [])

    def claim_job(self, job_id: str) -> dict:
        resp = self._post(
            f'{self._config.api_base_url}/api/node/jobs/{job_id}/claim',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def complete_job(self, job_id: str, photo_id: str, face_results: list) -> dict:
        face_vectors = [
            {
                'embedding': f['embedding'],
                'bbox': f['bbox'],
                'confidence': f['confidence'],
            }
            for f in face_results
        ]
        payload = {
            **self._auth(),
            'photo_id': photo_id,
            'face_vectors': face_vectors,
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
        """Send the 512-dim selfie embedding; server runs pgvector match and stores results."""
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
