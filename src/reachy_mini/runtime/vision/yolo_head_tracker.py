"""Optional YOLO-based head tracker extracted from the legacy conversation app."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray
from huggingface_hub import hf_hub_download

logger = logging.getLogger(__name__)


class HeadTracker:
    """Lightweight head tracker using a YOLO face detector."""

    def __init__(
        self,
        model_repo: str = "AdamCodd/YOLOv11n-face-detection",
        model_filename: str = "model.pt",
        confidence_threshold: float = 0.3,
        device: str = "cpu",
    ) -> None:
        self.confidence_threshold = confidence_threshold
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

    def get_head_position(
        self,
        img: NDArray[np.uint8],
    ) -> tuple[NDArray[np.float32] | None, float | None]:
        """Return face center in normalized coordinates and an optional roll."""
        face_center, roll, _ = self.get_head_observation(img)
        return face_center, roll

    def get_head_observation(
        self,
        img: NDArray[np.uint8],
    ) -> tuple[NDArray[np.float32] | None, float | None, float | None]:
        """Return one richer face observation for reactive-vision emitters."""
        h, w = img.shape[:2]
        try:
            results = self.model(img, verbose=False)
            detections = self._detections_cls.from_ultralytics(results[0])
            face_idx = self._select_best_face(detections)
            if face_idx is None:
                return None, None, None
            bbox = detections.xyxy[face_idx]
            face_center = self._bbox_to_mp_coords(bbox, w, h)
            confidence = None
            if detections.confidence is not None:
                confidence = float(detections.confidence[face_idx])
            return face_center, 0.0, confidence
        except Exception as exc:  # pragma: no cover - runtime fallback
            logger.warning("YOLO head tracking failed: %s", exc)
            return None, None, None
