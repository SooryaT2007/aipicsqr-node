"""
Image Compressor Service
========================
Compresses high-resolution event photos to a target size (1-2MB)
while maintaining good visual quality.

Strategy:
- Resize if dimensions exceed max (default 3840px)
- Iteratively reduce JPEG quality until target size is met
- Preserve EXIF orientation
"""

import io
import logging
from pathlib import Path
from PIL import Image, ExifTags

logger = logging.getLogger('AIPICSQR-node')


def compress_image(
    file_path: str,
    target_size_mb: float = 1.5,
    quality_start: int = 85,
    quality_min: int = 40,
    max_dimension: int = 3840,
) -> tuple[bytes, int, int]:
    """
    Compress an image to target size while maintaining quality.
    
    Args:
        file_path: Path to the input image
        target_size_mb: Target file size in MB
        quality_start: Initial JPEG quality (0-100)
        quality_min: Minimum acceptable JPEG quality
        max_dimension: Maximum width or height in pixels
    
    Returns:
        Tuple of (compressed_bytes, width, height)
    """
    target_bytes = int(target_size_mb * 1024 * 1024)

    # Open and fix orientation
    img = Image.open(file_path)
    img = _fix_orientation(img)

    # Convert to RGB if necessary (handles RGBA, P mode, etc.)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    # Resize if too large
    original_w, original_h = img.size
    if max(original_w, original_h) > max_dimension:
        ratio = max_dimension / max(original_w, original_h)
        new_w = int(original_w * ratio)
        new_h = int(original_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.debug(f"  Resized: {original_w}x{original_h} â†’ {new_w}x{new_h}")

    final_w, final_h = img.size

    # Iteratively compress to meet target size
    quality = quality_start
    while quality >= quality_min:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        size = buffer.tell()

        if size <= target_bytes:
            logger.debug(f"  Compressed: {size / 1024:.0f}KB @ quality={quality}")
            return buffer.getvalue(), final_w, final_h

        quality -= 5

    # If we can't meet target even at minimum quality, return as-is
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=quality_min, optimize=True)
    logger.warning(f"  Could not reach target size. Final: {buffer.tell() / 1024:.0f}KB")
    return buffer.getvalue(), final_w, final_h


def _fix_orientation(img: Image.Image) -> Image.Image:
    """Fix image orientation based on EXIF data."""
    try:
        exif = img.getexif()
        orientation_key = None
        for key, val in ExifTags.TAGS.items():
            if val == 'Orientation':
                orientation_key = key
                break

        if orientation_key and orientation_key in exif:
            orientation = exif[orientation_key]
            if orientation == 3:
                img = img.rotate(180, expand=True)
            elif orientation == 6:
                img = img.rotate(270, expand=True)
            elif orientation == 8:
                img = img.rotate(90, expand=True)
    except Exception:
        pass  # No EXIF or can't read it

    return img
