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
from PIL import Image, ExifTags

logger = logging.getLogger('AIPICSQR-node')

# Resolved once at import time — avoids an O(n) ExifTags.TAGS scan on every image.
_ORIENTATION_TAG: int | None = next(
    (k for k, v in ExifTags.TAGS.items() if v == 'Orientation'), None
)


def compress_image_with_thumbnail(
    file_path: str,
    target_size_mb: float = 1.5,
    quality_start: int = 85,
    quality_min: int = 40,
    max_dimension: int = 3840,
    thumb_width: int = 400,
    thumb_quality: int = 70,
) -> tuple[bytes, bytes, int, int]:
    """
    Compress an image and generate a thumbnail in one pass.

    Returns:
        Tuple of (compressed_bytes, thumbnail_bytes, width, height)
    """
    target_bytes = int(target_size_mb * 1024 * 1024)

    img = Image.open(file_path)
    img = _fix_orientation(img)

    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    original_w, original_h = img.size
    if max(original_w, original_h) > max_dimension:
        ratio = max_dimension / max(original_w, original_h)
        img = img.resize((int(original_w * ratio), int(original_h * ratio)), Image.LANCZOS)

    final_w, final_h = img.size

    # Generate thumbnail from the already-resized image (no second file open)
    thumb_h = int(final_h * (thumb_width / final_w)) if final_w > 0 else thumb_width
    thumb_img = img.resize((thumb_width, thumb_h), Image.LANCZOS)
    thumb_buf = io.BytesIO()
    thumb_img.save(thumb_buf, format='WEBP', quality=thumb_quality, method=4)
    thumbnail_bytes = thumb_buf.getvalue()

    quality = quality_start
    while quality >= quality_min:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        if buffer.tell() <= target_bytes:
            return buffer.getvalue(), thumbnail_bytes, final_w, final_h
        quality -= 5

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=quality_min, optimize=True)
    return buffer.getvalue(), thumbnail_bytes, final_w, final_h


def _fix_orientation(img: Image.Image) -> Image.Image:
    """Fix image orientation based on EXIF data."""
    if not _ORIENTATION_TAG:
        return img
    try:
        exif = img.getexif()
        orientation = exif.get(_ORIENTATION_TAG)
        if orientation == 3:   img = img.rotate(180, expand=True)
        elif orientation == 6: img = img.rotate(270, expand=True)
        elif orientation == 8: img = img.rotate(90,  expand=True)
    except Exception:
        pass  # No EXIF or can't read it
    return img
