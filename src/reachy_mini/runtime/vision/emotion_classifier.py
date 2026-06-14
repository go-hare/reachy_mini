"""Facial-expression classifiers for YOLO face crops."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

EMOTION_LABELS: tuple[str, ...] = (
    "Neutral",
    "Happiness",
    "Sadness",
    "Surprise",
    "Fear",
    "Disgust",
    "Anger",
)
POSTER_VAR_AFFECTNET_LABELS: tuple[str, ...] = (
    "Neutral",
    "Happy",
    "Sad",
    "Surprise",
    "Fear",
    "Disgust",
    "Anger",
    "Contempt",
)
EMOTION_LABELS_ZH: dict[str, str] = {
    "Anger": "生气",
    "Contempt": "轻蔑",
    "Disgust": "厌恶",
    "Fear": "害怕",
    "Happy": "开心",
    "Happiness": "开心",
    "Neutral": "平静",
    "Sad": "难过",
    "Sadness": "难过",
    "Surprise": "惊讶",
}

_AFFECTNET_BGR_MEAN = np.array([91.4953, 103.8827, 131.0912], dtype=np.float32)
_IMAGENET_RGB_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_RGB_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True, slots=True)
class EmotionPrediction:
    """One expression prediction for a detected face crop."""

    label: str
    label_zh: str
    confidence: float
    index: int
    probabilities: tuple[float, ...]

    def to_metadata(self) -> dict[str, Any]:
        """Return a compact JSON-serializable metadata payload."""
        return {
            "label": self.label,
            "label_zh": self.label_zh,
            "confidence": round(float(self.confidence), 3),
            "index": int(self.index),
            "probabilities": [
                round(float(item), 4) for item in self.probabilities
            ],
        }


class TorchScriptEmotionClassifier:
    """Run the EMO-AffectNet TorchScript model on YOLO face crops."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "auto",
        min_interval_s: float = 0.25,
        labels: tuple[str, ...] = EMOTION_LABELS,
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        self.labels = labels
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_prediction_at = 0.0
        self._last_prediction: EmotionPrediction | None = None
        self._torch = self._import_torch()
        self.device = self._resolve_device(device)
        self.model = self._load_model()

    @staticmethod
    def _import_torch() -> Any:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Emotion classification requires torch. "
                "Install the yolo-vision/local vision dependencies first."
            ) from exc
        return torch

    def _resolve_device(self, device: str) -> str:
        requested = str(device or "auto").strip().lower()
        if requested == "cpu":
            return "cpu"
        if requested == "cuda":
            return "cuda" if self._torch.cuda.is_available() else "cpu"
        if requested == "mps":
            return (
                "mps"
                if getattr(self._torch.backends, "mps", None)
                and self._torch.backends.mps.is_available()
                else "cpu"
            )
        if (
            getattr(self._torch.backends, "mps", None)
            and self._torch.backends.mps.is_available()
        ):
            return "mps"
        return "cuda" if self._torch.cuda.is_available() else "cpu"

    def _load_model(self) -> Any:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Emotion model not found: {self.model_path}")
        model = self._torch.jit.load(str(self.model_path), map_location=self.device)
        model = model.to(self.device)
        model.eval()
        logger.info(
            "Emotion classifier loaded from %s on %s",
            self.model_path,
            self.device,
        )
        return model

    def predict(
        self,
        frame_bgr: NDArray[np.uint8],
        bbox_xyxy: NDArray[np.float32],
        *,
        now: float | None = None,
    ) -> EmotionPrediction | None:
        """Predict one expression from a BGR frame and absolute ``xyxy`` bbox."""
        current_time = time.monotonic() if now is None else float(now)
        if (
            self._last_prediction is not None
            and current_time - self._last_prediction_at < self.min_interval_s
        ):
            return self._last_prediction

        crop = self._crop_face(frame_bgr, bbox_xyxy)
        if crop is None:
            return None

        try:
            tensor = self._preprocess(crop)
            with self._torch.inference_mode():
                logits = self.model(tensor)
                probs = self._torch.nn.functional.softmax(logits, dim=1)
            prob_values = probs.detach().cpu().numpy()[0].astype(float)
            index = int(np.argmax(prob_values))
            label = self.labels[index] if index < len(self.labels) else str(index)
            prediction = EmotionPrediction(
                label=label,
                label_zh=EMOTION_LABELS_ZH.get(label, label),
                confidence=float(prob_values[index]),
                index=index,
                probabilities=tuple(float(item) for item in prob_values.tolist()),
            )
            self._last_prediction = prediction
            self._last_prediction_at = current_time
            return prediction
        except Exception as exc:  # pragma: no cover - runtime model fallback
            logger.warning("Emotion classification failed: %s", exc)
            return None

    @staticmethod
    def _crop_face(
        frame_bgr: NDArray[np.uint8],
        bbox_xyxy: NDArray[np.float32],
    ) -> NDArray[np.uint8] | None:
        h, w = frame_bgr.shape[:2]
        x1 = int(np.floor(np.clip(float(bbox_xyxy[0]), 0, max(w - 1, 0))))
        y1 = int(np.floor(np.clip(float(bbox_xyxy[1]), 0, max(h - 1, 0))))
        x2 = int(np.ceil(np.clip(float(bbox_xyxy[2]), 0, w)))
        y2 = int(np.ceil(np.clip(float(bbox_xyxy[3]), 0, h)))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        return np.ascontiguousarray(crop)

    def _preprocess(self, crop_bgr: NDArray[np.uint8]) -> Any:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Emotion classification requires Pillow for image resizing."
            ) from exc

        image = Image.fromarray(crop_bgr[..., ::-1])
        image = image.resize((224, 224), Image.Resampling.NEAREST)
        rgb = np.asarray(image, dtype=np.float32)
        bgr = rgb[..., ::-1] - _AFFECTNET_BGR_MEAN
        chw = np.transpose(bgr, (2, 0, 1)).copy()
        tensor = self._torch.from_numpy(chw).unsqueeze(0)
        return tensor.to(self.device)


class EmotiEffLibEmotionClassifier:
    """Run an EmotiEffLib model on YOLO face crops."""

    def __init__(
        self,
        *,
        model_name: str = "enet_b2_7",
        engine: str = "onnx",
        device: str = "cpu",
        min_interval_s: float = 0.25,
    ) -> None:
        self.model_name = str(model_name or "enet_b2_7").strip()
        self.engine = str(engine or "onnx").strip().lower()
        self.device = self._resolve_device(device)
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_prediction_at = 0.0
        self._last_prediction: EmotionPrediction | None = None
        self.recognizer = self._load_recognizer()

    def _resolve_device(self, device: str) -> str:
        requested = str(device or "cpu").strip().lower()
        if self.engine == "onnx":
            return "cpu"
        if requested in {"auto", "mps"}:
            return "cpu"
        if requested == "cuda":
            try:
                import torch
            except ImportError:
                return "cpu"
            return "cuda" if torch.cuda.is_available() else "cpu"
        return "cpu"

    def _load_recognizer(self) -> Any:
        try:
            from emotiefflib.facial_analysis import EmotiEffLibRecognizer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "EmotiEffLib emotion classification requires emotiefflib. "
                "Install it with: pip install emotiefflib."
            ) from exc
        recognizer = EmotiEffLibRecognizer(
            engine=self.engine,
            model_name=self.model_name,
            device=self.device,
        )
        logger.info(
            "EmotiEffLib emotion classifier loaded: model=%s engine=%s device=%s",
            self.model_name,
            self.engine,
            self.device,
        )
        return recognizer

    def predict(
        self,
        frame_bgr: NDArray[np.uint8],
        bbox_xyxy: NDArray[np.float32],
        *,
        now: float | None = None,
    ) -> EmotionPrediction | None:
        """Predict one expression from a BGR frame and absolute ``xyxy`` bbox."""
        current_time = time.monotonic() if now is None else float(now)
        if (
            self._last_prediction is not None
            and current_time - self._last_prediction_at < self.min_interval_s
        ):
            return self._last_prediction

        crop = TorchScriptEmotionClassifier._crop_face(frame_bgr, bbox_xyxy)
        if crop is None:
            return None

        try:
            crop_rgb = np.ascontiguousarray(crop[..., ::-1])
            labels, scores = self.recognizer.predict_emotions(
                crop_rgb,
                logits=False,
            )
            prob_values = np.asarray(scores, dtype=float)[0]
            label = str(labels[0] if labels else "").strip()
            index = int(np.argmax(prob_values))
            if not label:
                label = self._label_for_index(index)
            prediction = EmotionPrediction(
                label=label,
                label_zh=EMOTION_LABELS_ZH.get(label, label),
                confidence=float(prob_values[index]),
                index=index,
                probabilities=tuple(float(item) for item in prob_values.tolist()),
            )
            self._last_prediction = prediction
            self._last_prediction_at = current_time
            return prediction
        except Exception as exc:  # pragma: no cover - runtime model fallback
            logger.warning("EmotiEffLib emotion classification failed: %s", exc)
            return None

    def _label_for_index(self, index: int) -> str:
        labels = getattr(self.recognizer, "idx_to_emotion_class", {})
        if isinstance(labels, dict):
            return str(labels.get(index, index))
        return str(index)


class PosterVarEmotionClassifier:
    """Run an exported POSTER-Var TorchScript model on YOLO face crops."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "auto",
        min_interval_s: float = 0.25,
        labels: tuple[str, ...] = POSTER_VAR_AFFECTNET_LABELS,
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        self.labels = labels
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_prediction_at = 0.0
        self._last_prediction: EmotionPrediction | None = None
        self._torch = TorchScriptEmotionClassifier._import_torch()
        self.device = self._resolve_device(device)
        self.model = self._load_model()

    def _resolve_device(self, device: str) -> str:
        return TorchScriptEmotionClassifier._resolve_device(self, device)

    def _load_model(self) -> Any:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"POSTER-Var model not found: {self.model_path}")
        model = self._torch.jit.load(str(self.model_path), map_location=self.device)
        model = model.to(self.device)
        model.eval()
        logger.info(
            "POSTER-Var emotion classifier loaded from %s on %s",
            self.model_path,
            self.device,
        )
        return model

    def predict(
        self,
        frame_bgr: NDArray[np.uint8],
        bbox_xyxy: NDArray[np.float32],
        *,
        now: float | None = None,
    ) -> EmotionPrediction | None:
        """Predict one expression from a BGR frame and absolute ``xyxy`` bbox."""
        current_time = time.monotonic() if now is None else float(now)
        if (
            self._last_prediction is not None
            and current_time - self._last_prediction_at < self.min_interval_s
        ):
            return self._last_prediction

        crop = TorchScriptEmotionClassifier._crop_face(frame_bgr, bbox_xyxy)
        if crop is None:
            return None

        try:
            tensor = self._preprocess(crop)
            with self._torch.inference_mode():
                logits = self.model(tensor)
                probs = self._torch.nn.functional.softmax(logits, dim=1)
            prob_values = probs.detach().cpu().numpy()[0].astype(float)
            index = int(np.argmax(prob_values))
            label = self.labels[index] if index < len(self.labels) else str(index)
            prediction = EmotionPrediction(
                label=label,
                label_zh=EMOTION_LABELS_ZH.get(label, label),
                confidence=float(prob_values[index]),
                index=index,
                probabilities=tuple(float(item) for item in prob_values.tolist()),
            )
            self._last_prediction = prediction
            self._last_prediction_at = current_time
            return prediction
        except Exception as exc:  # pragma: no cover - runtime model fallback
            logger.warning("POSTER-Var emotion classification failed: %s", exc)
            return None

    def _preprocess(self, crop_bgr: NDArray[np.uint8]) -> Any:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "POSTER-Var emotion classification requires Pillow."
            ) from exc

        image = Image.fromarray(crop_bgr[..., ::-1])
        image = image.resize((236, 236), Image.Resampling.BILINEAR)
        left = (236 - 224) // 2
        image = image.crop((left, left, left + 224, left + 224))
        rgb = np.asarray(image, dtype=np.float32) / 255.0
        normalized = (rgb - _IMAGENET_RGB_MEAN) / _IMAGENET_RGB_STD
        chw = np.transpose(normalized, (2, 0, 1)).copy()
        tensor = self._torch.from_numpy(chw).unsqueeze(0)
        return tensor.to(self.device)
