"""
Configuration management for the Photographer Node.
Reads from .env file and provides validated config values.
"""

import os
from pathlib import Path
from dotenv import load_dotenv, set_key

# Paths to environment files
ENV_PATH = Path(__file__).parent / '.env'
EXAMPLE_ENV_PATH = Path(__file__).parent / '.env.example'

# Load example defaults first, then override with actual values.
if EXAMPLE_ENV_PATH.exists():
    load_dotenv(EXAMPLE_ENV_PATH, override=False)
load_dotenv(ENV_PATH, override=True)


class Config:
    """Centralized configuration for the Photographer Node."""

    def _normalize_env_value(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ''
        if normalized.lower().startswith('your_') or 'your_' in normalized.lower():
            return ''
        return normalized

    def __init__(self):
        # â”€â”€ Supabase â”€â”€
        self.supabase_url = os.getenv('SUPABASE_URL', '')
        self.supabase_key = os.getenv('SUPABASE_ANON_KEY', '')



        # â”€â”€ Photographer Identity â”€â”€
        self.photographer_id = self._normalize_env_value(os.getenv('PHOTOGRAPHER_ID', ''))
        self.event_id = self._normalize_env_value(os.getenv('EVENT_ID', ''))

        # â”€â”€ API â”€â”€
        self.api_base_url = os.getenv('API_BASE_URL', 'https://aipicsqr.vercel.app')

        # â”€â”€ Folder Watching â”€â”€
        scan_paths = os.getenv('SCAN_FOLDERS', '').strip()
        if scan_paths:
            self.scan_folders = [p.strip() for p in scan_paths.split(',') if p.strip()]
        else:
            default_folder = Path(__file__).parent / 'photos'
            self.scan_folders = [str(default_folder)]

        # â”€â”€ Image Compression â”€â”€
        self.target_size_mb = float(os.getenv('TARGET_SIZE_MB', '1.5'))
        self.jpeg_quality_start = int(os.getenv('JPEG_QUALITY_START', '85'))
        self.jpeg_quality_min = int(os.getenv('JPEG_QUALITY_MIN', '40'))
        self.max_dimension = int(os.getenv('MAX_DIMENSION', '3840'))

        # â”€â”€ Vision / Face Recognition â”€â”€
        self.models_dir = Path(__file__).parent / 'models'
        self.yunet_model = str(self.models_dir / 'face_detection_yunet_2023mar.onnx')
        self.sface_model = str(self.models_dir / 'face_recognition_sface_2021dec.onnx')
        self.face_confidence_threshold = float(os.getenv('FACE_CONFIDENCE', '0.7'))
        self.detection_input_size = (640, 480)  # Resize for detection
        self.embedding_dim = 512

        # â”€â”€ Resource Limits â”€â”€
        self.max_cpu_percent = int(os.getenv('MAX_CPU_PERCENT', '90'))
        self.max_cpu_temp = float(os.getenv('MAX_CPU_TEMP', '85.0'))
        self.cooldown_period = int(os.getenv('COOLDOWN_PERIOD', '30'))

        # â”€â”€ Telemetry â”€â”€
        self.pulse_interval = int(os.getenv('PULSE_INTERVAL', '60'))
        self.node_id = os.getenv('NODE_ID', '')  # Set after first registration

    def save(self, key: str, value: str) -> bool:
        """Persist a single env key into the local .env file."""
        if not ENV_PATH.exists():
            ENV_PATH.write_text('')
        try:
            set_key(str(ENV_PATH), key, value)
            return True
        except Exception:
            return False

    def clear_pairing(self) -> None:
        """Clear pairing state and stored node identity."""
        self.photographer_id = ''
        self.event_id = ''
        self.node_id = ''
        self.save('PHOTOGRAPHER_ID', '')
        self.save('EVENT_ID', '')
        self.save('NODE_ID', '')

    def validate(self) -> bool:
        """Validate that all required configuration is present."""
        required = [
            ('SUPABASE_URL', self.supabase_url),
            ('SUPABASE_ANON_KEY', self.supabase_key),
        ]

        valid = True
        for name, value in required:
            if not value:
                print(f"âŒ Missing required config: {name}")
                valid = False

        if not self.photographer_id:
            print("âš ï¸ Photographer ID not configured. Login through the web dashboard and copy it from Settings.")

        if not self.event_id:
            print("âš ï¸ Event ID not configured. The node will watch folders, but uploads will be paused until EVENT_ID is set.")

        if not self.scan_folders:
            print("âš ï¸ No SCAN_FOLDERS configured. Folder watcher will be idle.")

        # Check model files
        if not Path(self.yunet_model).exists():
            print(f"âš ï¸ YuNet model not found: {self.yunet_model}")
            print("   Download from: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet")

        if not Path(self.sface_model).exists():
            print(f"âš ï¸ SFace model not found: {self.sface_model}")
            print("   Download from: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface")

        return valid

        # Check model files
        if not Path(self.yunet_model).exists():
            print(f"âš ï¸ YuNet model not found: {self.yunet_model}")
            print("   Download from: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet")

        if not Path(self.sface_model).exists():
            print(f"âš ï¸ SFace model not found: {self.sface_model}")
            print("   Download from: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface")

        return valid
