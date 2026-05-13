"""
Vision Service - Face Detection & Recognition
===============================================
Core facial recognition pipeline using:
- YuNet (cv2.FaceDetectorYN) for face detection
- SFace (cv2.FaceRecognizerSF) for face recognition / embedding

Both models are Apache 2.0 / MIT licensed and commercially safe.
This module runs in an isolated process to not interfere with
YT/LED live streams.

Pipeline: Image â†’ Resize(640px) â†’ Detect(YuNet) â†’ Crop(Original) â†’ Recognize(SFace) â†’ 512-dim Vector
"""

import cv2
import numpy as np
import logging
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger('AIPICSQR-node')


try:
    import onnxruntime as ort
    _onnxruntime_openvino_available = any(
        provider.startswith('OpenVINO')
        for provider in ort.get_available_providers()
    )
except Exception:
    ort = None  # type: ignore[assignment]
    _onnxruntime_openvino_available = False


class VisionService:
    """
    Face detection and recognition using YuNet + SFace.
    
    Both models are loaded from ONNX files and run purely on CPU
    using OpenCV's DNN backend, so no external ONNX runtime dependency
    is required for normal operation.
    """

    def __init__(self, models_dir: str):
        self.models_dir = Path(models_dir)
        self._detector: Optional[cv2.FaceDetectorYN] = None
        self._recognizer: Optional[cv2.FaceRecognizerSF] = None
        self._initialized = False

    def initialize(self) -> bool:
        """
        Load the YuNet and SFace models.
        
        Returns:
            True if both models loaded successfully.
        """
        yunet_path = str(self.models_dir / 'face_detection_yunet_2023mar.onnx')
        sface_path = str(self.models_dir / 'face_recognition_sface_2021dec.onnx')

        # Check model files exist
        if not Path(yunet_path).exists():
            logger.error(f"YuNet model not found: {yunet_path}")
            return False
        if not Path(sface_path).exists():
            logger.error(f"SFace model not found: {sface_path}")
            return False

        backend_id = cv2.dnn.DNN_BACKEND_OPENCV
        target_id = cv2.dnn.DNN_TARGET_CPU

        if _onnxruntime_openvino_available and hasattr(cv2.dnn, 'DNN_BACKEND_INFERENCE_ENGINE'):
            backend_id = cv2.dnn.DNN_BACKEND_INFERENCE_ENGINE
            logger.info('Optional onnxruntime-openvino support detected. Trying OpenVINO-backed inference engine.')

        def _create_model(factory, *args, **kwargs):
            try:
                return factory(*args, **kwargs)
            except Exception as ex:
                if backend_id != cv2.dnn.DNN_BACKEND_OPENCV:
                    logger.warning(
                        'OpenVINO backend is not supported by the current OpenCV build. Falling back to CPU backend.'
                    )
                    return factory(*args, backend_id=cv2.dnn.DNN_BACKEND_OPENCV, target_id=cv2.dnn.DNN_TARGET_CPU, **kwargs)
                raise

        try:
            # Initialize YuNet face detector
            self._detector = _create_model(
                cv2.FaceDetectorYN.create,
                model=yunet_path,
                config='',
                input_size=(640, 480),
                score_threshold=0.5,
                nms_threshold=0.3,
                top_k=5000,
                backend_id=backend_id,
                target_id=target_id,
            )
            logger.info(f"  âœ“ YuNet loaded: {yunet_path}")

            # Initialize SFace recognizer
            self._recognizer = _create_model(
                cv2.FaceRecognizerSF.create,
                model=sface_path,
                config='',
                backend_id=backend_id,
                target_id=target_id,
            )
            logger.info(f"  âœ“ SFace loaded: {sface_path}")

            if backend_id == cv2.dnn.DNN_BACKEND_INFERENCE_ENGINE:
                logger.info('OpenVINO execution path enabled.')
            else:
                logger.info('Using OpenCV CPU inference fallback.')

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize vision models: {e}")
            return False

    def process_image(self, image_path: str, confidence_threshold: float = 0.7) -> List[dict]:
        """
        Process a single image: detect faces, generate embeddings.
        
        Args:
            image_path: Path to the image file
            confidence_threshold: Minimum face detection confidence
        
        Returns:
            List of dicts with keys: embedding (512-dim), bbox, confidence
        """
        if not self._initialized:
            if not self.initialize():
                return []

        # Read image
        original = cv2.imread(image_path)
        if original is None:
            logger.error(f"Could not read image: {image_path}")
            return []

        orig_h, orig_w = original.shape[:2]

        # â”€â”€ STEP 1: Resize for detection (YuNet expects smaller input) â”€â”€
        detect_w = 640
        scale = detect_w / orig_w
        detect_h = int(orig_h * scale)
        resized = cv2.resize(original, (detect_w, detect_h))

        # Update detector input size
        self._detector.setInputSize((detect_w, detect_h))

        # â”€â”€ STEP 2: Detect faces â”€â”€
        _, faces = self._detector.detect(resized)

        if faces is None or len(faces) == 0:
            logger.debug(f"  No faces detected in {Path(image_path).name}")
            return []

        results = []

        for face in faces:
            face_confidence = float(face[-1])
            if face_confidence < confidence_threshold:
                continue

            # â”€â”€ STEP 3: Scale bounding box back to original resolution â”€â”€
            x, y, w, h = face[:4]
            orig_x = int(x / scale)
            orig_y = int(y / scale)
            orig_face_w = int(w / scale)
            orig_face_h = int(h / scale)

            # Scale landmarks back to original resolution
            face_for_align = face.copy()
            # Landmarks are at indices 4-13 (5 points Ã— 2 coords)
            for i in range(4, 14):
                if i % 2 == 0:  # x coordinates
                    face_for_align[i] = face[i] / scale
                else:  # y coordinates
                    face_for_align[i] = face[i] / scale
            # Also scale bbox
            face_for_align[0] = orig_x
            face_for_align[1] = orig_y
            face_for_align[2] = orig_face_w
            face_for_align[3] = orig_face_h

            # â”€â”€ STEP 4: Align and crop face from ORIGINAL resolution â”€â”€
            try:
                aligned_face = self._recognizer.alignCrop(original, face_for_align)
            except Exception as e:
                logger.debug(f"  Failed to align face: {e}")
                continue

            # â”€â”€ STEP 5: Generate 512-dim embedding â”€â”€
            try:
                embedding = self._recognizer.feature(aligned_face)
                embedding = embedding.flatten().tolist()
            except Exception as e:
                logger.debug(f"  Failed to extract features: {e}")
                continue

            if len(embedding) != 512:
                logger.warning(f"  Unexpected embedding dimension: {len(embedding)}")
                continue

            results.append({
                'embedding': embedding,
                'bbox': {
                    'x': orig_x,
                    'y': orig_y,
                    'w': orig_face_w,
                    'h': orig_face_h,
                },
                'confidence': face_confidence,
            })

        logger.info(
            f"  ðŸ§  {Path(image_path).name}: {len(results)} face(s) detected "
            f"(out of {len(faces)} candidates)"
        )

        return results

    def process_selfie(self, image_data: bytes) -> Optional[List[float]]:
        """
        Process a selfie image and return the face embedding.
        Used for guest face matching.
        
        Args:
            image_data: Raw image bytes (JPEG/PNG)
        
        Returns:
            512-dimensional embedding list, or None if no face found
        """
        if not self._initialized:
            if not self.initialize():
                return None

        # Decode image from bytes
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return None

        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))

        _, faces = self._detector.detect(img)

        if faces is None or len(faces) == 0:
            return None

        # Take the largest face (most likely the selfie subject)
        largest_face = max(faces, key=lambda f: f[2] * f[3])

        try:
            aligned = self._recognizer.alignCrop(img, largest_face)
            embedding = self._recognizer.feature(aligned)
            return embedding.flatten().tolist()
        except Exception as e:
            logger.error(f"Selfie processing error: {e}")
            return None
