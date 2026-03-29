from __future__ import annotations

"""Host-side runtime adapters used by resident Reachy Mini apps."""

import logging
from inspect import isawaitable, signature
from pathlib import Path
from typing import Any

from reachy_mini.runtime.tools import ReachyToolContext


class AppRuntimeHostAdapter:
    """Keep host/runtime adaptation details out of ReachyMiniApp itself."""

    def __init__(
        self,
        *,
        profile_root: Path | None,
        logger: logging.Logger,
    ) -> None:
        self.profile_root = profile_root
        self.logger = logger

    def build_runtime_tool_context(
        self,
        reachy_mini: Any,
    ) -> ReachyToolContext | None:
        """Build optional runtime tool dependencies from one running app instance."""
        from reachy_mini.runtime.config import (
            VisionRuntimeConfig,
            load_profile_runtime_config,
        )
        from reachy_mini.runtime.embodiment import EmbodimentCoordinator
        from reachy_mini.runtime.moves import MovementManager
        from reachy_mini.runtime.profile_loader import load_profile_bundle
        from reachy_mini.runtime.speech_driver import SpeechDriver
        from reachy_mini.runtime.surface_driver import SurfaceDriver

        if reachy_mini is None or not hasattr(reachy_mini, "goto_target"):
            return None

        runtime_config = None
        vision_config = VisionRuntimeConfig()
        if self.profile_root is not None:
            profile = load_profile_bundle(self.profile_root)
            runtime_config = load_profile_runtime_config(profile)
            vision_config = runtime_config.vision

        camera_worker = None
        vision_processor = None
        movement_manager = None
        head_wobbler = None
        speech_driver = None
        surface_driver = None
        embodiment_coordinator = None
        reply_audio_service = None

        if not vision_config.no_camera:
            media = getattr(reachy_mini, "media", None)
            if media is not None and hasattr(media, "get_frame"):
                head_tracker = self._build_head_tracker(vision_config)
                try:
                    from reachy_mini.runtime.camera_worker import CameraWorker

                    camera_worker = CameraWorker(reachy_mini, head_tracker)
                    camera_worker.start()
                except Exception as exc:
                    self.logger.warning("Failed to start camera worker: %s", exc)

            if vision_config.local_vision:
                vision_processor = self._build_vision_processor(vision_config)

        if all(
            hasattr(reachy_mini, attribute)
            for attribute in (
                "set_target",
                "get_current_head_pose",
                "get_current_joint_positions",
            )
        ):
            try:
                movement_manager = MovementManager(
                    reachy_mini,
                    camera_worker=camera_worker,
                )
                movement_manager.start()
            except Exception as exc:
                self.logger.warning("Failed to start movement manager: %s", exc)

        if movement_manager is not None:
            surface_driver = SurfaceDriver(movement_manager=movement_manager)
            try:
                from reachy_mini.runtime.audio import HeadWobbler

                head_wobbler = HeadWobbler(movement_manager.set_speech_offsets)
                speech_driver = SpeechDriver(head_wobbler=head_wobbler)
                speech_driver.start()
            except Exception as exc:
                self.logger.warning("Failed to start head wobbler: %s", exc)
            embodiment_coordinator = EmbodimentCoordinator(
                reachy_mini=reachy_mini,
                movement_manager=movement_manager,
                camera_worker=camera_worker,
                motion_duration_s=1.0,
                surface_driver=surface_driver,
                speech_driver=speech_driver,
            )

        media = getattr(reachy_mini, "media", None)
        if runtime_config is not None:
            try:
                from reachy_mini.runtime.reply_audio import (
                    build_runtime_reply_audio_service,
                )

                reply_audio_service = build_runtime_reply_audio_service(
                    config=runtime_config.speech,
                    media=media,
                    speech_driver=speech_driver,
                    fallback_api_key=runtime_config.front_model.api_key,
                )
                if reply_audio_service is not None:
                    self.logger.info(
                        "Runtime reply audio ready: provider=%s media=%s speech_driver=%s",
                        getattr(runtime_config.speech, "provider", ""),
                        type(media).__name__ if media is not None else "None",
                        type(speech_driver).__name__
                        if speech_driver is not None
                        else "None",
                    )
                else:
                    self.logger.info(
                        "Runtime reply audio disabled or unavailable: provider=%s media=%s",
                        getattr(runtime_config.speech, "provider", ""),
                        type(media).__name__ if media is not None else "None",
                    )
            except Exception as exc:
                self.logger.warning("Failed to build reply audio service: %s", exc)

        return ReachyToolContext(
            reachy_mini=reachy_mini,
            camera_worker=camera_worker,
            vision_processor=vision_processor,
            movement_manager=movement_manager,
            head_wobbler=head_wobbler,
            speech_driver=speech_driver,
            surface_driver=surface_driver,
            embodiment_coordinator=embodiment_coordinator,
            reply_audio_service=reply_audio_service,
        )

    def cleanup_runtime_tool_context(self, context: Any | None) -> None:
        """Stop runtime-managed helper resources."""
        if context is None:
            return

        speech_driver = getattr(context, "speech_driver", None)
        if speech_driver is not None and hasattr(speech_driver, "stop"):
            try:
                speech_driver.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop speech driver: %s", exc)

        head_wobbler = getattr(context, "head_wobbler", None)
        if (
            head_wobbler is not None
            and hasattr(head_wobbler, "stop")
            and speech_driver is None
        ):
            try:
                head_wobbler.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop head wobbler: %s", exc)

        movement_manager = getattr(context, "movement_manager", None)
        if movement_manager is not None and hasattr(movement_manager, "stop"):
            try:
                movement_manager.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop movement manager: %s", exc)

        camera_worker = getattr(context, "camera_worker", None)
        if camera_worker is not None and hasattr(camera_worker, "stop"):
            try:
                camera_worker.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop camera worker: %s", exc)

    def feed_runtime_audio_delta(
        self,
        context: Any | None,
        delta_b64: str,
    ) -> bool:
        """Feed one assistant audio delta into the runtime speech path."""
        coordinator = getattr(context, "embodiment_coordinator", None)
        if coordinator is not None and hasattr(coordinator, "feed_audio_delta"):
            return bool(coordinator.feed_audio_delta(delta_b64))

        head_wobbler = getattr(context, "head_wobbler", None)
        if head_wobbler is None or not hasattr(head_wobbler, "feed"):
            return False

        head_wobbler.feed(delta_b64)
        return True

    def reset_runtime_audio_motion(self, context: Any | None) -> bool:
        """Reset queued speech-motion state for the resident runtime."""
        coordinator = getattr(context, "embodiment_coordinator", None)
        if coordinator is not None and hasattr(coordinator, "reset_speech_motion"):
            return bool(coordinator.reset_speech_motion())

        head_wobbler = getattr(context, "head_wobbler", None)
        if head_wobbler is None or not hasattr(head_wobbler, "reset"):
            return False

        head_wobbler.reset()
        return True

    async def play_runtime_reply_audio(
        self,
        context: Any | None,
        payload: dict[str, Any],
    ) -> bool:
        """Synthesize and play one final runtime reply when speech output is configured."""
        reply_audio_service = getattr(context, "reply_audio_service", None)
        if reply_audio_service is None or not hasattr(reply_audio_service, "speak_text"):
            self.logger.info("Runtime reply audio skipped: no reply_audio_service available.")
            return False

        text = str(payload.get("text", "") or "").strip()
        if not text:
            self.logger.info("Runtime reply audio skipped: empty reply text.")
            return False

        self.logger.info(
            "Runtime reply audio requested: chars=%s service=%s",
            len(text),
            type(reply_audio_service).__name__,
        )
        speak_text = reply_audio_service.speak_text
        callback_kwargs = {
            key: payload.get(key)
            for key in ("on_started", "on_audio_delta", "on_finished")
            if payload.get(key) is not None
        }
        if callback_kwargs and not self._supports_reply_audio_callbacks(speak_text):
            callback_kwargs = {}

        result = speak_text(text, **callback_kwargs) if callback_kwargs else speak_text(text)
        if isawaitable(result):
            played = bool(await result)
        else:
            played = bool(result)
        self.logger.info("Runtime reply audio finished: played=%s chars=%s", played, len(text))
        return played

    @staticmethod
    def _supports_reply_audio_callbacks(speak_text: Any) -> bool:
        """Whether ``speak_text`` accepts reply-audio lifecycle callbacks."""

        try:
            parameters = signature(speak_text).parameters.values()
        except (TypeError, ValueError):
            return False

        parameter_names = {parameter.name for parameter in parameters}
        if {"on_started", "on_audio_delta", "on_finished"}.issubset(parameter_names):
            return True
        return any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters)

    def apply_runtime_surface_state(
        self,
        context: Any | None,
        state: dict[str, Any],
    ) -> None:
        """Apply one runtime surface-state snapshot onto the embodiment path."""
        coordinator = getattr(context, "embodiment_coordinator", None)
        if coordinator is not None and hasattr(coordinator, "apply_surface_state"):
            try:
                coordinator.apply_surface_state(dict(state))
            except Exception as exc:
                self.logger.warning("Failed to apply runtime surface state: %s", exc)
            return

        surface_driver = getattr(context, "surface_driver", None)
        if surface_driver is None or not hasattr(surface_driver, "apply_state"):
            return

        try:
            surface_driver.apply_state(dict(state))
        except Exception as exc:
            self.logger.warning("Failed to apply runtime surface state: %s", exc)

    def _build_head_tracker(self, vision_config: Any) -> Any | None:
        """Build the configured head tracker using legacy conversation-app semantics."""
        tracker_kind = str(getattr(vision_config, "head_tracker", "") or "").strip()
        if not tracker_kind:
            return None
        if tracker_kind == "yolo":
            from reachy_mini.runtime.vision.yolo_head_tracker import HeadTracker

            return HeadTracker()
        if tracker_kind == "mediapipe":
            try:
                from reachy_mini_toolbox.vision import HeadTracker
            except ImportError as exc:
                raise ImportError(
                    "MediaPipe head tracking requires reachy_mini_toolbox vision support."
                ) from exc
            return HeadTracker()
        raise ValueError(f"Unsupported head_tracker setting: {tracker_kind}")

    @staticmethod
    def _build_vision_processor(vision_config: Any) -> Any:
        """Build the configured local vision processor."""
        from reachy_mini.runtime.vision.processors import (
            VisionConfig,
            initialize_vision_processor,
        )

        return initialize_vision_processor(
            VisionConfig(
                model_path=(
                    str(getattr(vision_config, "local_vision_model", "") or "").strip()
                    or VisionConfig().model_path
                ),
                hf_home=(
                    str(getattr(vision_config, "hf_home", "") or "").strip()
                    or VisionConfig().hf_home
                ),
            )
        )
