"""
API client for all communication with the AIPICSQR backend.
The node never touches Supabase directly — everything goes through this client.
Authentication is a node_token issued on registration (stored in node_config.json).
"""

import socket
import logging
import requests

logger = logging.getLogger('AIPICSQR-node')


class APIClient:
    def __init__(self, config):
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({'Content-Type': 'application/json'})

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
        resp = self._session.post(
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
            'node_id': self._config.node_id,
            'photographer_id': self._config.photographer_id,
            'hostname': socket.gethostname(),
            'ip_address': self._local_ip(),
            'cpu_percent': resource_status.get('cpu_percent', 0),
            'cpu_temp': resource_status.get('cpu_temp', 0),
            'status': 'busy' if resource_status.get('paused') else 'online',
        }
        resp = self._session.post(
            f'{self._config.api_base_url}/api/nodes/pulse',
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Folder polling ────────────────────────────────────────────────────────

    def get_folders(self) -> list:
        """
        Fetch all active watch folders for this photographer's events.
        Each entry: {id, event_id, path, pool_type, watch_until}
        Called every 30 s; node dynamically adjusts which paths it watches.
        """
        resp = self._session.post(
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
        resp = self._session.post(
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
        face_results: list,
    ) -> dict:
        """
        Notify the server that the photo has been uploaded and vectorized.
        Face vectors are sent here; the server writes them to Supabase.
        """
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
            'file_size_bytes': file_size,
            'width': width,
            'height': height,
            'face_vectors': face_vectors,
        }
        resp = self._session.post(
            f'{self._config.api_base_url}/api/node/upload/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Mesh jobs ─────────────────────────────────────────────────────────────

    def get_jobs(self) -> list:
        resp = self._session.post(
            f'{self._config.api_base_url}/api/node/jobs',
            json=self._auth(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('jobs', [])

    def claim_job(self, job_id: str) -> dict:
        resp = self._session.post(
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
        resp = self._session.post(
            f'{self._config.api_base_url}/api/node/jobs/{job_id}/complete',
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def fail_job(self, job_id: str, error: str) -> None:
        try:
            self._session.post(
                f'{self._config.api_base_url}/api/node/jobs/{job_id}/fail',
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
