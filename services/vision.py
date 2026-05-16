"""
Vision Service — Face Detection & Recognition
===============================================
Pipeline:
  Image → Resize(640×480) → Detect(YuNet) → Crop(Original) → Recognize(SFace) → 128-dim Vector

Why 640×480 is fixed
─────────────────────
YuNet's internal anchor grid is stride-based. The image height must be a
multiple of 32 for the feature maps to align correctly. If setInputSize is
called with a non-conforming height (e.g. 360 from 3840×2160 aspect-ratio
resize) the detector returns None immediately without running inference.

The safest approach: always feed exactly 640×480 (the training resolution).
Track separate x/y scale factors to map detected bboxes and landmarks back
to the original image coordinates for high-resolution SFace alignment.
"""

import cv2
import logging
import numpy as np
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger('AIPICSQR-node')

_DETECT_W = 640
_DETECT_H = 480  # must be a multiple of 32; matches YuNet training resolution

try:
    import onnxruntime as ort
    _openvino_available = any(
        p.startswith('OpenVINO') for p in ort.get_available_providers()
    )
except Exception:
    ort = None  # type: ignore[assignment]
    _openvino_available = False


class VisionService:
    """Face detection (YuNet) and recognition (SFace) using OpenCV DNN."""

    def __init__(self, models_dir: str):
        self.models_dir = Path(models_dir)
        self._detector:   Optional[cv2.FaceDetectorYN]  = None
        self._recognizer: Optional[cv2.FaceRecognizerSF] = None
        self._initialized = False

    def initialize(self) -> bool:
        yunet_path = str(self.models_dir / 'face_detection_yunet_2023mar.onnx')
        sface_path = str(self.models_dir / 'face_recognition_sface_2021dec.onnx')

        if not Path(yunet_path).exists():
            logger.error(f'YuNet model not found: {yunet_path}')
            return False
        if not Path(sface_path).exists():
            logger.error(f'SFace model not found: {sface_path}')
            return False

        backend_id = cv2.dnn.DNN_BACKEND_OPENCV
        target_id  = cv2.dnn.DNN_TARGET_CPU

        if _openvino_available and hasattr(cv2.dnn, 'DNN_BACKEND_INFERENCE_ENGINE'):
            backend_id = cv2.dnn.DNN_BACKEND_INFERENCE_ENGINE
            logger.info('OpenVINO backend detected — attempting accelerated inference')

        def _make(factory, *args, **kwargs):
            try:
                return factory(*args, **kwargs)
            except Exception:
                if backend_id != cv2.dnn.DNN_BACKEND_OPENCV:
                    logger.warning('OpenVINO backend failed; falling back to OpenCV CPU')
                    return factory(
                        *args,
                        backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
                        target_id=cv2.dnn.DNN_TARGET_CPU,
                        **kwargs,
                    )
                raise

        try:
            # Fixed input size matches training resolution — never changed at runtime.
            self._detector = _make(
                cv2.FaceDetectorYN.create,
                model=yunet_path,
                config='',
                input_size=(_DETECT_W, _DETECT_H),
                score_threshold=0.5,
                nms_threshold=0.3,
                top_k=5000,
                backend_id=backend_id,
                target_id=target_id,
            )
            logger.info(f'  ✔ YuNet loaded ({_DETECT_W}×{_DETECT_H}): {yunet_path}')

            self._recognizer = _make(
                cv2.FaceRecognizerSF.create,
                model=sface_path,
                config='',
                backend_id=backend_id,
                target_id=target_id,
            )
            logger.info(f'  ✔ SFace loaded: {sface_path}')

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f'Failed to load vision models: {e}')
            return False

    # ── Photo vectorization ────────────────────────────────────────────────────

    def process_image(self, image_path: str, confidence_threshold: float = 0.7) -> List[dict]:
        """
        Detect faces in a photo and return 128-dim SFace embeddings.

        Detection runs at fixed 640×480 to avoid YuNet's stride alignment
        requirement. Bboxes/landmarks are mapped back to the original
        resolution using per-axis scale factors before SFace alignment,
        so embedding quality is not affected by the detection downscale.
        """
        if not self._initialized:
            if not self.initialize():
                return []

        original = cv2.imread(image_path)
        if original is None:
            logger.error(f'  [photo] Could not read image: {image_path}')
            return []

        orig_h, orig_w = original.shape[:2]
        logger.info(f'  [photo] {Path(image_path).name}: {orig_w}×{orig_h} px')

        # Resize to detection resolution — separate scales per axis so that
        # any aspect ratio works without changing the model's inputSize.
        detect_img = cv2.resize(original, (_DETECT_W, _DETECT_H))
        scale_x = orig_w / _DETECT_W
        scale_y = orig_h / _DETECT_H

        _, faces = self._detector.detect(detect_img)

        if faces is None or len(faces) == 0:
            logger.info(f'  [photo] No faces detected in {Path(image_path).name}')
            return []

        logger.info(f'  [photo] {len(faces)} candidate(s), threshold={confidence_threshold}')

        results = []
        for face in faces:
            confidence = float(face[-1])
            if confidence < confidence_threshold:
                continue

            # Map detection bbox back to original resolution
            x, y, w, h = face[:4]
            orig_x = int(x * scale_x)
            orig_y = int(y * scale_y)
            orig_w_ = int(w * scale_x)
            orig_h_ = int(h * scale_y)

            # Scale landmarks (indices 4–13: 5 points × x,y) back to original
            face_orig = face.copy()
            for i in range(4, 14, 2):   # x coords
                face_orig[i]     = face[i]     * scale_x
            for i in range(5, 14, 2):   # y coords
                face_orig[i]     = face[i]     * scale_y
            face_orig[0] = orig_x
            face_orig[1] = orig_y
            face_orig[2] = orig_w_
            face_orig[3] = orig_h_

            # Align and crop face from HIGH-RES original for best embedding quality
            try:
                aligned = self._recognizer.alignCrop(original, face_orig)
            except Exception as e:
                logger.debug(f'  alignCrop failed: {e}')
                continue

            try:
                embedding = self._recognizer.feature(aligned).flatten().tolist()
            except Exception as e:
                logger.debug(f'  feature extraction failed: {e}')
                continue

            if len(embedding) < 64:
                logger.warning(f'  Embedding too small ({len(embedding)}-dim), skipping')
                continue

            results.append({
                'embedding':  embedding,
                'bbox':       {'x': orig_x, 'y': orig_y, 'w': orig_w_, 'h': orig_h_},
                'confidence': confidence,
            })

        logger.info(f'  🧠 {Path(image_path).name}: {len(results)} face(s) extracted')
        return results

    # ── Selfie embedding (guest face matching) ─────────────────────────────────

    def process_selfie(self, image_data: bytes) -> Optional[List[float]]:
        """
        Decode a selfie and return a 128-dim embedding for pgvector matching.
        Detection runs at 640×480; alignment uses the original selfie pixels.
        Returns None if no face is detected.
        """
        if not self._initialized:
            if not self.initialize():
                return None

        logger.info(f'  [selfie] {len(image_data):,} bytes received')

        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            logger.error('  [selfie] Failed to decode image bytes')
            return None

        orig_h, orig_w = img.shape[:2]
        logger.info(f'  [selfie] {orig_w}×{orig_h} px')

        # Resize to detection resolution (same fixed size as process_image)
        detect_img = cv2.resize(img, (_DETECT_W, _DETECT_H))
        scale_x = orig_w / _DETECT_W
        scale_y = orig_h / _DETECT_H

        _, faces = self._detector.detect(detect_img)

        if faces is None or len(faces) == 0:
            logger.warning('  [selfie] No face detected — check lighting and framing')
            return None

        logger.info(f'  [selfie] {len(faces)} candidate(s)')

        # Use the largest face (most likely the selfie subject)
        largest = max(faces, key=lambda f: f[2] * f[3])
        conf = float(largest[-1])
        logger.info(f'  [selfie] Selected face: conf={conf:.3f}')

        # Scale landmarks back to original selfie resolution for alignment
        face_orig = largest.copy()
        x, y, w, h = largest[:4]
        for i in range(4, 14, 2):
            face_orig[i] = largest[i] * scale_x
        for i in range(5, 14, 2):
            face_orig[i] = largest[i] * scale_y
        face_orig[0] = int(x * scale_x)
        face_orig[1] = int(y * scale_y)
        face_orig[2] = int(w * scale_x)
        face_orig[3] = int(h * scale_y)

        try:
            aligned = self._recognizer.alignCrop(img, face_orig)
            embedding = self._recognizer.feature(aligned).flatten().tolist()
            logger.info(f'  [selfie] ✅ Embedding: {len(embedding)}-dim')
            return embedding
        except Exception as e:
            logger.error(f'  [selfie] alignCrop/feature failed: {e}')
            return None
