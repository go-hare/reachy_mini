"""Optional YOLO-based head tracker extracted from the legacy conversation app."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
from huggingface_hub import hf_hub_download
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class HeadTracker:
    """Lightweight head tracker using a YOLO face detector."""

    def __init__(
        self,
        model_repo: str = "AdamCodd/YOLOv11n-face-detection",
        model_filename: str = "model.pt",
        confidence_threshold: float = 0.3,
        device: str = "cpu",
        emotion_device: str = "auto",
        emotion_min_interval_s: float = 0.25,
        poster_var_model_path: str | Path = "",
        face_identity_enabled: bool = False,
        known_faces_dir: str | Path = "",
        face_identity_threshold: float = 0.42,
        face_identity_model_name: str = "buffalo_l",
        face_identity_min_interval_s: float = 0.5,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.emotion_classifier: Any | None = None
        self.identity_recognizer: Any | None = None
        try:
            from supervision import Detections
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "YOLO head tracking requires optional dependencies. "
                "Install the yolo-vision extras first."
            ) from exc

        self._detections_cls = Detections
        model_path = hf_hub_download(repo_id=model_repo, filename=model_filename)
        self.model = YOLO(model_path).to(device)
        logger.info("YOLO face tracker loaded from %s", model_repo)
        poster_var_path = str(poster_var_model_path or "").strip()
        if poster_var_path:
            self.emotion_classifier = self._build_poster_var_classifier(
                poster_var_path,
                emotion_device=emotion_device,
                emotion_min_interval_s=emotion_min_interval_s,
            )
        if face_identity_enabled:
            self._add_identity_recognizer(
                known_faces_dir=known_faces_dir,
                threshold=face_identity_threshold,
                model_name=face_identity_model_name,
                min_interval_s=face_identity_min_interval_s,
            )

    def _build_poster_var_classifier(
        self,
        poster_var_path: str,
        *,
        emotion_device: str,
        emotion_min_interval_s: float,
    ) -> Any:
        from reachy_mini.runtime.vision.emotion_classifier import (
            PosterVarEmotionClassifier,
        )

        return PosterVarEmotionClassifier(
            poster_var_path,
            device=emotion_device,
            min_interval_s=emotion_min_interval_s,
        )

    def _add_identity_recognizer(
        self,
        *,
        known_faces_dir: str | Path,
        threshold: float,
        model_name: str,
        min_interval_s: float,
    ) -> None:
        from reachy_mini.runtime.vision.face_identity import (
            InsightFaceIdentityRecognizer,
        )

        self.identity_recognizer = InsightFaceIdentityRecognizer(
            known_faces_dir,
            threshold=threshold,
            model_name=model_name,
            min_interval_s=min_interval_s,
        )

    def _select_best_face(self, detections: Any) -> int | None:
        if detections.xyxy.shape[0] == 0 or detections.confidence is None:
            return None
        valid_mask = detections.confidence >= self.confidence_threshold
        if not np.any(valid_mask):
            return None

        valid_indices = np.where(valid_mask)[0]
        boxes = detections.xyxy[valid_indices]
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        confidences = detections.confidence[valid_indices]
        scores = confidences * 0.7 + (areas / np.max(areas)) * 0.3
        return int(valid_indices[np.argmax(scores)])

    @staticmethod
    def _bbox_to_mp_coords(
        bbox: NDArray[np.float32],
        w: int,
        h: int,
    ) -> NDArray[np.float32]:
        center_x = (bbox[0] + bbox[2]) / 2.0
        center_y = (bbox[1] + bbox[3]) / 2.0
        norm_x = (center_x / w) * 2.0 - 1.0
        norm_y = (center_y / h) * 2.0 - 1.0
        return np.array([norm_x, norm_y], dtype=np.float32)

    @staticmethod
    def _bbox_to_norm_xywh(
        bbox: NDArray[np.float32],
        w: int,
        h: int,
    ) -> list[float]:
        left = float(np.clip(bbox[0] / w, 0.0, 1.0))
        top = float(np.clip(bbox[1] / h, 0.0, 1.0))
        right = float(np.clip(bbox[2] / w, 0.0, 1.0))
        bottom = float(np.clip(bbox[3] / h, 0.0, 1.0))
        return [
            round(left, 4),
            round(top, 4),
            round(max(right - left, 0.0), 4),
            round(max(bottom - top, 0.0), 4),
        ]

    def get_head_position(
        self,
        img: NDArray[np.uint8],
    ) -> tuple[NDArray[np.float32] | None, float | None]:
        """Return face center in normalized coordinates and an optional roll."""
        face_center, roll, _, _ = self.get_head_observation(img)
        return face_center, roll

    def get_head_observation(
        self,
        img: NDArray[np.uint8],
    ) -> tuple[
        NDArray[np.float32] | None,
        float | None,
        float | None,
        dict[str, Any] | None,
    ]:
        """Return one richer face observation for reactive-vision emitters."""
        h, w = img.shape[:2]
        try:
            results = self.model(img, verbose=False)
            detections = self._detections_cls.from_ultralytics(results[0])
            face_idx = self._select_best_face(detections)
            if face_idx is None:
                return None, None, None, None
            bbox = detections.xyxy[face_idx]
            face_center = self._bbox_to_mp_coords(bbox, w, h)
            confidence = None
            if detections.confidence is not None:
                confidence = float(detections.confidence[face_idx])
            observation = {
                "bbox_norm": self._bbox_to_norm_xywh(bbox, w, h),
            }
            emotion = self._predict_emotion(img, bbox)
            if emotion is not None:
                observation["emotion"] = emotion
            identity = self._predict_identity(img, bbox)
            if identity is not None:
                observation["identity"] = identity
            return face_center, 0.0, confidence, observation
        except Exception as exc:  # pragma: no cover - runtime fallback
            logger.warning("YOLO head tracking failed: %s", exc)
            return None, None, None, None

    def _predict_emotion(
        self,
        img: NDArray[np.uint8],
        bbox: NDArray[np.float32],
    ) -> dict[str, Any] | None:
        if self.emotion_classifier is None:
            return None
        now = time.monotonic()
        prediction = self.emotion_classifier.predict(img, bbox, now=now)
        if prediction is None:
            return None
        metadata = prediction.to_metadata()
        metadata["model"] = "POSTER-Var"
        metadata["primary_model"] = "POSTER-Var"
        return metadata

    def _predict_identity(
        self,
        img: NDArray[np.uint8],
        bbox: NDArray[np.float32],
    ) -> dict[str, Any] | None:
        if self.identity_recognizer is None:
            return None
        prediction = self.identity_recognizer.predict(
            img,
            bbox,
            now=time.monotonic(),
        )
        if prediction is None:
            return None
        return prediction.to_metadata()
