"""
Node configuration — stored in node_config.json, never in .env.
No cloud credentials are kept on the photographer's laptop.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / 'node_config.json'
API_BASE_URL = 'https://dashboard.aipicsqr.com'


class Config:
    """
    All persistent identity state (photographer_id, node_id, node_token)
    lives in node_config.json which is created automatically on first run.
    Everything else is a sensible default that can be overridden via CLI.
    """

    def __init__(self):
        # Identity — populated on first-run registration
        self.photographer_id = ''
        self.node_id = ''
        self.node_token = ''
        self.event_id = ''

        # Folder watching
        self.scan_folders: list[str] = [str(Path(__file__).parent / 'photos')]

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
        self.embedding_dim = 512

        # Resource limits
        self.max_cpu_percent = 90
        self.max_cpu_temp = 85.0
        self.cooldown_period = 30

        # Telemetry
        self.pulse_interval = 60

        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            self.photographer_id = data.get('photographer_id', '')
            self.node_id = data.get('node_id', '')
            self.node_token = data.get('node_token', '')
            self.event_id = data.get('event_id', '')
            folders = data.get('scan_folders', [])
            if folders:
                self.scan_folders = folders
        except Exception:
            pass

    def save(self):
        data = {
            'photographer_id': self.photographer_id,
            'node_id': self.node_id,
            'node_token': self.node_token,
            'event_id': self.event_id,
            'scan_folders': self.scan_folders,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ── Registration helpers ──────────────────────────────────────────────────

    def apply_registration(self, photographer_id: str, node_id: str, node_token: str):
        self.photographer_id = photographer_id
        self.node_id = node_id
        self.node_token = node_token
        self.save()

    def clear_pairing(self):
        self.photographer_id = ''
        self.node_id = ''
        self.node_token = ''
        self.event_id = ''
        self.save()

    # ── State checks ─────────────────────────────────────────────────────────

    def is_registered(self) -> bool:
        return bool(self.photographer_id and self.node_id and self.node_token)

    def is_ready(self) -> bool:
        return self.is_registered() and bool(self.event_id)
