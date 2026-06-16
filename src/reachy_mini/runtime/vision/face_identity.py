"""Face identity recognition for YOLO face detections."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from reachy_mini.runtime.vision.emotion_classifier import crop_face

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass(frozen=True, slots=True)
class FaceIdentityPrediction:
    """One identity match for a detected face."""

    name: str
    confidence: float
    distance: float
    known: bool
    samples: int

    def to_metadata(self) -> dict[str, Any]:
        """Return a compact JSON-serializable metadata payload."""
        return {
            "name": self.name,
            "confidence": round(float(self.confidence), 3),
            "distance": round(float(self.distance), 3),
            "known": bool(self.known),
            "samples": int(self.samples),
        }


class InsightFaceIdentityRecognizer:
    """Recognize face identities from a known-faces image directory."""

    def __init__(
        self,
        known_faces_dir: str | Path,
        *,
        threshold: float = 0.42,
        model_name: str = "buffalo_l",
        providers: tuple[str, ...] = ("CPUExecutionProvider",),
        detection_size: tuple[int, int] = (320, 320),
        min_interval_s: float = 0.5,
    ) -> None:
        self.known_faces_dir = Path(known_faces_dir).expanduser()
        self.threshold = max(0.0, float(threshold))
        self.model_name = str(model_name or "buffalo_l").strip()
        self.providers = tuple(providers)
        self.detection_size = detection_size
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_prediction_at = 0.0
        self._last_prediction: FaceIdentityPrediction | None = None
        self._cv2 = self._import_cv2()
        self.app = self._load_app()
        self.gallery = self._load_gallery()

    @staticmethod
    def _import_cv2() -> Any:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError("Face identity recognition requires opencv-python.") from exc
        return cv2

    def _load_app(self) -> Any:
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Face identity recognition requires insightface. "
                "Install it with: pip install insightface."
            ) from exc

        app = FaceAnalysis(name=self.model_name, providers=list(self.providers))
        app.prepare(ctx_id=-1, det_size=self.detection_size)
        logger.info(
            "InsightFace identity recognizer loaded: model=%s providers=%s",
            self.model_name,
            ",".join(self.providers),
        )
        return app

    def _load_gallery(self) -> dict[str, NDArray[np.float32]]:
        if not self.known_faces_dir.is_dir():
            logger.info(
                "Known faces directory does not exist: %s",
                self.known_faces_dir,
            )
            return {}

        gallery: dict[str, NDArray[np.float32]] = {}
        for person_dir in sorted(self.known_faces_dir.iterdir()):
            if not person_dir.is_dir():
                continue
            embeddings = [
                embedding
                for image_path in self._iter_image_paths(person_dir)
                if (embedding := self._embedding_from_image(image_path)) is not None
            ]
            if not embeddings:
                continue
            mean_embedding = np.mean(np.stack(embeddings), axis=0)
            gallery[person_dir.name] = self._normalize_embedding(mean_embedding)
            logger.info(
                "Loaded known face identity: name=%s samples=%s",
                person_dir.name,
                len(embeddings),
            )
        logger.info("Known face identities loaded: %s", len(gallery))
        return gallery

    @staticmethod
    def _iter_image_paths(person_dir: Path) -> list[Path]:
        return [
            path
            for path in sorted(person_dir.iterdir())
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
        ]

    def _embedding_from_image(self, image_path: Path) -> NDArray[np.float32] | None:
        image = self._cv2.imread(str(image_path))
        if image is None:
            logger.warning("Known face image unreadable: %s", image_path)
            return None
        faces = self.app.get(image)
        if not faces:
            logger.warning("No face found in known face image: %s", image_path)
            return None
        face = max(faces, key=lambda item: self._face_area(item))
        return self._normalize_embedding(np.asarray(face.embedding, dtype=np.float32))

    @staticmethod
    def _face_area(face: Any) -> float:
        bbox = np.asarray(getattr(face, "bbox", []), dtype=float)
        if bbox.shape[0] < 4:
            return 0.0
        return max(float(bbox[2] - bbox[0]), 0.0) * max(float(bbox[3] - bbox[1]), 0.0)

    @staticmethod
    def _normalize_embedding(embedding: NDArray[np.float32]) -> NDArray[np.float32]:
        norm = float(np.linalg.norm(embedding))
        if norm <= 0.0:
            return embedding.astype(np.float32)
        return (embedding / norm).astype(np.float32)

    def predict(
        self,
        frame_bgr: NDArray[np.uint8],
        bbox_xyxy: NDArray[np.float32],
        *,
        now: float | None = None,
    ) -> FaceIdentityPrediction | None:
        """Predict the closest known identity for one detected face crop."""
        current_time = time.monotonic() if now is None else float(now)
        if (
            self._last_prediction is not None
            and current_time - self._last_prediction_at < self.min_interval_s
        ):
            return self._last_prediction

        crop = crop_face(frame_bgr, bbox_xyxy)
        if crop is None:
            return None

        if not self.gallery:
            prediction = FaceIdentityPrediction(
                name="未登记",
                confidence=0.0,
                distance=1.0,
                known=False,
                samples=0,
            )
            self._last_prediction = prediction
            self._last_prediction_at = current_time
            return prediction

        faces = self.app.get(crop)
        if not faces:
            return None
        face = max(faces, key=lambda item: self._face_area(item))
        embedding = self._normalize_embedding(np.asarray(face.embedding, dtype=np.float32))
        prediction = self._match_embedding(embedding)
        self._last_prediction = prediction
        self._last_prediction_at = current_time
        return prediction

    def _match_embedding(
        self,
        embedding: NDArray[np.float32],
    ) -> FaceIdentityPrediction:
        best_name = ""
        best_similarity = -1.0
        for name, known_embedding in self.gallery.items():
            similarity = float(np.dot(embedding, known_embedding))
            if similarity > best_similarity:
                best_name = name
                best_similarity = similarity

        distance = 1.0 - max(best_similarity, -1.0)
        known = best_similarity >= self.threshold
        return FaceIdentityPrediction(
            name=best_name if known else "陌生人",
            confidence=max(0.0, min(1.0, best_similarity)),
            distance=distance,
            known=known,
            samples=len(self.gallery),
        )
