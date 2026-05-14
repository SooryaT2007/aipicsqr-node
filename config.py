"""
Node configuration — stored in node_config.json, never in .env.
No cloud credentials are kept on the photographer's laptop.
Supports multiple linked photographer IDs; one is active at a time.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / 'node_config.json'
API_BASE_URL = 'https://dashboard.aipicsqr.com'


class Config:
    """
    All persistent identity state lives in node_config.json, created on first
    registration.  Multiple photographer IDs can be stored; switching the
    active ID (via App.py) requires a node restart to take effect.
    """

    def __init__(self):
        # API
        self.api_base_url = API_BASE_URL

        # Image compression
        self.target_size_mb = 1.5
        self.jpeg_quality_start = 85
        self.jpeg_quality_min = 40
        self.max_dimension = 3840

        # Vision / face recognition
        self.models_dir = Path(__file__).parent / 'models'
        self.yunet_model = str(self.models_dir / 'face_detection_yunet_2023mar.onnx')
        self.sface_model = str(self.models_dir / 'face_recognition_sface_2021dec.onnx')
        self.face_confidence_threshold = 0.5
        self.detection_input_size = (640, 480)
        self.embedding_dim = 128

        # Resource limits
        self.max_cpu_percent = 90
        self.max_cpu_temp = 85.0
        self.cooldown_period = 30

        # Telemetry
        self.pulse_interval = 60

        # Persistent identity (populated from node_config.json)
        self._photographer_ids: list[str] = []
        self._active_photographer_id: str = ''
        self._node_identities: dict = {}  # {photographer_id: {node_id, node_token}}
        self._event_id: str = ''

        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            return

        # New multi-ID format
        self._photographer_ids = list(data.get('photographer_ids', []))
        self._active_photographer_id = data.get('active_photographer_id', '')
        self._node_identities = data.get('node_identities', {})
        self._event_id = data.get('event_id', '')

        # Legacy single-ID migration
        legacy_pid = data.get('photographer_id', '')
        legacy_nid = data.get('node_id', '')
        legacy_tok = data.get('node_token', '')
        if legacy_pid:
            if legacy_pid not in self._photographer_ids:
                self._photographer_ids.insert(0, legacy_pid)
            if not self._active_photographer_id:
                self._active_photographer_id = legacy_pid
            if legacy_nid and legacy_tok and legacy_pid not in self._node_identities:
                self._node_identities[legacy_pid] = {
                    'node_id': legacy_nid,
                    'node_token': legacy_tok,
                }

        # Ensure active_id is consistent
        if self._active_photographer_id not in self._photographer_ids:
            self._active_photographer_id = self._photographer_ids[0] if self._photographer_ids else ''

    def save(self):
        data = {
            'photographer_ids': self._photographer_ids,
            'active_photographer_id': self._active_photographer_id,
            'node_identities': self._node_identities,
            'event_id': self._event_id,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ── Properties (drop-in for old single-ID callers) ───────────────────────

    @property
    def photographer_id(self) -> str:
        return self._active_photographer_id

    @property
    def node_id(self) -> str:
        return self._node_identities.get(self._active_photographer_id, {}).get('node_id', '')

    @property
    def node_token(self) -> str:
        return self._node_identities.get(self._active_photographer_id, {}).get('node_token', '')

    @property
    def event_id(self) -> str:
        return self._event_id

    # ── Registration helpers ──────────────────────────────────────────────────

    def apply_registration(self, photographer_id: str, node_id: str, node_token: str):
        """Store node identity for the given photographer and make them active."""
        if photographer_id not in self._photographer_ids:
            self._photographer_ids.append(photographer_id)
        self._active_photographer_id = photographer_id
        self._node_identities[photographer_id] = {
            'node_id': node_id,
            'node_token': node_token,
        }
        self.save()

    def clear_pairing(self):
        """Remove the active photographer's identity (logout)."""
        pid = self._active_photographer_id
        if pid in self._photographer_ids:
            self._photographer_ids.remove(pid)
        self._node_identities.pop(pid, None)
        self._active_photographer_id = self._photographer_ids[0] if self._photographer_ids else ''
        self.save()

    # ── State checks ─────────────────────────────────────────────────────────

    def is_registered(self) -> bool:
        pid = self._active_photographer_id
        identity = self._node_identities.get(pid, {})
        return bool(pid and identity.get('node_id') and identity.get('node_token'))

    def is_ready(self) -> bool:
        return self.is_registered() and bool(self._event_id)
