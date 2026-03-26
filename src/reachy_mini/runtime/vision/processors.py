"""Optional local-vision helpers extracted from the legacy conversation app."""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_VISION_MODEL = "HuggingFaceTB/SmolVLM2-2.2B-Instruct"
DEFAULT_HF_HOME = "./cache"

LOCAL_VISION_RESPONSE_INSTRUCTIONS = (
    "Respond to the request using only details that are clearly visible in the image. "
    "Do not guess, infer hidden details, or invent missing information. "
    "If the answer is not clearly visible, say exactly: I can't tell from this image. "
    "Keep the answer short and factual."
)


@dataclass(slots=True)
class VisionConfig:
    """Configuration for local vision processing."""

    model_path: str = os.environ.get("LOCAL_VISION_MODEL", DEFAULT_LOCAL_VISION_MODEL)
    hf_home: str = os.environ.get("HF_HOME", DEFAULT_HF_HOME)
    max_new_tokens: int = 64
    max_retries: int = 3
    retry_delay: float = 1.0
    device_preference: str = "auto"


class VisionProcessor:
    """Lazy local-vision wrapper around SmolVLM2."""

    def __init__(self, vision_config: VisionConfig | None = None) -> None:
        self.vision_config = vision_config or VisionConfig()
        self.device = self._determine_device()
        self.processor: Any | None = None
        self.model: Any | None = None
        self._initialized = False

    def _determine_device(self) -> str:
        torch = self._import_torch()
        pref = self.vision_config.device_preference
        if pref == "cpu":
            return "cpu"
        if pref == "cuda":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if pref == "mps":
            return "mps" if torch.backends.mps.is_available() else "cpu"
        if torch.backends.mps.is_available():
            return "mps"
        return "cuda" if torch.cuda.is_available() else "cpu"

    @staticmethod
    def _import_torch() -> Any:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Local vision requires optional dependencies. "
                "Install the local-vision extras first."
            ) from exc
        return torch

    @staticmethod
    def _import_transformers() -> tuple[Any, Any]:
        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Local vision requires transformers. "
                "Install the local-vision extras first."
            ) from exc
        return AutoProcessor, AutoModelForImageTextToText

    @staticmethod
    def _import_pil_image() -> Any:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "Local vision requires Pillow. Install the local-vision extras first."
            ) from exc
        return Image

    def initialize(self) -> None:
        """Load the processor and model onto the selected device."""
        torch = self._import_torch()
        auto_processor, auto_model = self._import_transformers()
        logger.info("Loading local vision model on %s", self.device)

        processor = auto_processor.from_pretrained(self.vision_config.model_path)
        model_kwargs: dict[str, object] = {
            "dtype": torch.bfloat16 if self.device == "cuda" else torch.float32,
        }
        model = auto_model.from_pretrained(
            self.vision_config.model_path,
            **model_kwargs,
        )
        model = model.to(self.device)
        model.eval()

        self.processor = processor
        self.model = model
        self._initialized = True

    def process_image(
        self,
        frame: NDArray[np.uint8],
        prompt: str,
    ) -> str:
        """Process one BGR image and return a short text answer."""
        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            raise ValueError("prompt must be a non-empty string")

        if not self._initialized or self.processor is None or self.model is None:
            return "Vision model not initialized"

        torch = self._import_torch()
        image_cls = self._import_pil_image()

        rgb_image = image_cls.fromarray(np.ascontiguousarray(frame[..., ::-1]))
        request = "\n\n".join(
            [prompt_text, LOCAL_VISION_RESPONSE_INSTRUCTIONS]
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": rgb_image},
                    {"type": "text", "text": request},
                ],
            }
        ]

        for attempt in range(self.vision_config.max_retries):
            try:
                inputs = self.processor.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=True,
                    return_dict=True,
                    return_tensors="pt",
                )
                inputs = inputs.to(self.device)
                input_ids = inputs.get("input_ids")
                prompt_len = int(input_ids.shape[-1]) if getattr(input_ids, "shape", None) else None

                with torch.inference_mode():
                    generated_ids = self.model.generate(
                        **inputs,
                        do_sample=False,
                        max_new_tokens=self.vision_config.max_new_tokens,
                        pad_token_id=self.processor.tokenizer.eos_token_id,
                    )

                if prompt_len is None:
                    new_token_ids = generated_ids
                else:
                    new_token_ids = generated_ids[:, prompt_len:]
                response = self.processor.batch_decode(
                    new_token_ids,
                    skip_special_tokens=True,
                )[0]
                return str(response).replace("\n", " ").strip()
            except Exception as exc:
                logger.warning(
                    "Local vision processing failed on attempt %s: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < self.vision_config.max_retries - 1:
                    time.sleep(self.vision_config.retry_delay)
                    continue
                return (
                    "Vision processing error after "
                    f"{self.vision_config.max_retries} attempts"
                )

        return (
            "Vision processing error after "
            f"{self.vision_config.max_retries} attempts"
        )


def initialize_vision_processor(
    vision_config: VisionConfig | None = None,
) -> VisionProcessor:
    """Download the configured model and return an initialized processor."""
    config = vision_config or VisionConfig()
    cache_dir = os.path.expanduser(config.hf_home)
    os.makedirs(cache_dir, exist_ok=True)
    os.environ["HF_HOME"] = cache_dir

    logger.info("Downloading local vision model %s", config.model_path)
    snapshot_download(
        repo_id=config.model_path,
        repo_type="model",
        cache_dir=cache_dir,
    )

    processor = VisionProcessor(config)
    processor.initialize()
    logger.info(
        "Local vision enabled: %s on %s",
        processor.vision_config.model_path,
        processor.device,
    )
    return processor
