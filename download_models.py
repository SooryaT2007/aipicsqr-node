"""
Model Downloader
=================
Downloads the required ONNX models for face detection and recognition
from the OpenCV Zoo GitHub repository.

Models:
- YuNet: Face detection (Apache 2.0 license)
- SFace: Face recognition (Apache 2.0 license)

Run this script once before starting the Node:
    python download_models.py
"""

import os
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).parent / 'models'

MODELS = {
    'face_detection_yunet_2023mar.onnx': {
        'url': 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
        'size_mb': 0.3,
        'description': 'YuNet Face Detector (Apache 2.0)',
    },
    'face_recognition_sface_2021dec.onnx': {
        'url': 'https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx',
        'size_mb': 37.0,
        'description': 'SFace Face Recognizer (Apache 2.0)',
    },
}


def download_file(url: str, dest: Path, description: str):
    """Download a file with progress reporting."""
    print(f"\nðŸ“¥ Downloading: {description}")
    print(f"   URL: {url}")
    print(f"   Destination: {dest}")

    if dest.exists():
        print(f"   âœ… Already exists, skipping.")
        return

    try:
        def progress_hook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(100, downloaded * 100 / total_size)
                bar_len = 30
                filled = int(bar_len * percent / 100)
                bar = 'â–ˆ' * filled + 'â–‘' * (bar_len - filled)
                sys.stdout.write(f"\r   [{bar}] {percent:.0f}%")
                sys.stdout.flush()

        urllib.request.urlretrieve(url, str(dest), reporthook=progress_hook)
        print(f"\n   âœ… Downloaded: {dest.stat().st_size / 1024 / 1024:.1f} MB")
    except Exception as e:
        print(f"\n   âŒ Failed: {e}")
        if dest.exists():
            dest.unlink()
        raise


def main():
    print("=" * 60)
    print("  AIPICSQR Model Downloader")
    print("  Commercially Licensed AI Models (Apache 2.0)")
    print("=" * 60)

    # Create models directory
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    for filename, info in MODELS.items():
        dest = MODELS_DIR / filename
        download_file(info['url'], dest, info['description'])

    print("\n" + "=" * 60)
    print("  âœ… All models downloaded successfully!")
    print(f"  Location: {MODELS_DIR}")
    print("=" * 60)
    print("\nYou can now run the Photographer Node:")
    print("  python main.py")


if __name__ == '__main__':
    main()
