"""
GDrive Uploader
===============
Uploads compressed photo bytes directly to Google Drive from the node,
bypassing the Vercel drive-sync cron (which streams R2→GDrive through
Vercel functions and tops out at ~65K photos/month).

The node calls /api/node/gdrive/upload-config once per event to get a
fresh access token + folder IDs, then calls GDriveUploader.upload() for
each photo.  The config is cached until expires_at so only one token
fetch per event per ~55-minute window.

Upload method: multipart (one HTTP request, no resumable session needed)
because compressed photos are always ≤ 2 MB — well under the 5 MB limit.
"""

import json
import logging
import time

import requests

logger = logging.getLogger('AIPICSQR-node')

_UPLOAD_API = 'https://www.googleapis.com/upload/drive/v3'
_DRIVE_API  = 'https://www.googleapis.com/drive/v3'
_BOUNDARY   = 'gdrive_aipicsqr_mp'


class GDriveUploader:
    """One instance per event — holds the access token and target folder ID."""

    def __init__(self, access_token: str, folder_id: str):
        self._token     = access_token
        self._folder_id = folder_id

    def upload(self, filename: str, data: bytes, mime_type: str = 'image/jpeg') -> str:
        """Upload bytes via multipart upload. Returns the GDrive file ID."""
        metadata   = json.dumps({'name': filename, 'parents': [self._folder_id]})
        header_str = (
            f'--{_BOUNDARY}\r\n'
            f'Content-Type: application/json; charset=UTF-8\r\n\r\n'
            f'{metadata}\r\n'
            f'--{_BOUNDARY}\r\n'
            f'Content-Type: {mime_type}\r\n\r\n'
        )
        footer_str = f'\r\n--{_BOUNDARY}--'
        body = header_str.encode() + data + footer_str.encode()

        resp = self._post(
            f'{_UPLOAD_API}/files?uploadType=multipart&fields=id',
            headers={
                'Authorization':  f'Bearer {self._token}',
                'Content-Type':   f'multipart/related; boundary="{_BOUNDARY}"',
            },
            data=body,
        )
        resp.raise_for_status()
        return resp.json()['id']

    def make_public(self, file_id: str) -> None:
        """Grant 'anyone with link = viewer' so guests can load thumbnails without OAuth."""
        resp = self._post(
            f'{_DRIVE_API}/files/{file_id}/permissions',
            headers={
                'Authorization': f'Bearer {self._token}',
                'Content-Type':  'application/json',
            },
            data=json.dumps({'role': 'reader', 'type': 'anyone'}).encode(),
        )
        if not resp.ok:
            logger.warning(f'GDrive makeFilePublic {file_id[:8]}: {resp.status_code}')

    def _post(self, url: str, headers: dict, data: bytes) -> requests.Response:
        """POST with exponential backoff on 429 / 503."""
        delay = 1.0
        resp  = None
        for attempt in range(6):
            resp = requests.post(url, headers=headers, data=data, timeout=60)
            if resp.status_code not in (429, 503) or attempt == 5:
                return resp
            retry_after = float(resp.headers.get('Retry-After', delay))
            time.sleep(retry_after)
            delay = min(delay * 2, 30.0)
        return resp  # type: ignore[return-value]
