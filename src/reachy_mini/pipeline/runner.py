"""Opt-in v4 runtime runner."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from reachy_mini.action_runtime import ActionExecutor
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import from_profile
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient
from reachy_mini.reachy_brain.pipecat_bridge import extract_text_blocks

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    ActionResultFrame,
    BrowserInputFrame,
    SDKMessageFrame,
    SpeechPresenterFrame,
    TTSAudioFrame,
)
from .speech_presenter import SpeechPresenter
from .tts_kokoro import KokoroAdapter


class NoopMini:
    """Minimal fake SDK object used by v4 mock/text mode."""

    def __init__(self) -> None:
        """Track commands for traces."""
        self.calls: list[dict[str, Any]] = []

    def goto_target(self, **kwargs: Any) -> None:
        """Record a movement request."""
        self.calls.append({"method": "goto_target", "kwargs": kwargs})

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float,
        perform_movement: bool,
    ) -> None:
        """Record a look-at request."""
        self.calls.append(
            {
                "method": "look_at_world",
                "kwargs": {
                    "x": x,
                    "y": y,
                    "z": z,
                    "duration": duration,
                    "perform_movement": perform_movement,
                },
            }
        )

    def get_present_antenna_joint_positions(self) -> list[float]:
        """Return neutral antenna positions."""
        return [0.0, 0.0]


async def run_text_turn(
    *,
    profile_path: Path,
    text: str,
    overrides: dict[str, Any] | None = None,
) -> list[object]:
    """Run one text turn through the opt-in v4 path."""
    config = from_profile(profile_path, overrides=_text_mode_overrides(overrides or {}))
    registry = create_builtin_registry()
    executor = ActionExecutor(registry=registry, mini=NoopMini())
    frames: list[object] = []

    async def publish_action_result(frame: ActionResultFrame) -> None:
        frames.append(frame)

    actions = ActionDispatcher(executor, publish=publish_action_result)
    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=actions.run_action,
        client_factory=lambda options: OfflineSDKClient(
            options,
            action_runner=actions.run_action,
        ),
    )
    brain = BrainProcessor(agent)
    speech = SpeechPresenter(style={"voice": config.speech.voice, "speed": config.speech.speed})
    tts = KokoroAdapter(config.speech)

    sdk_frames = await brain.process(
        BrowserInputFrame(
            kind="text",
            payload={"text": text, "turn_id": "cli:turn"},
            session_id="cli",
        )
    )
    frames.extend(sdk_frames)
    for sdk_frame in sdk_frames:
        speech_frames = await speech.process(sdk_frame)
        frames.extend(speech_frames)
        for speech_frame in speech_frames:
            frames.extend(await tts.process(speech_frame))
    return frames


def _text_mode_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Force local Phase 1 text/mock smoke runs onto the deterministic model."""
    merged = dict(overrides)
    merged.update(
        {
            "model.provider": "mock",
            "model.model": "reachy_mini_v4_text_mock",
            "model.base_url": None,
            "model.api_key_ref": "",
            "model.api_key": "",
        }
    )
    return merged


def parse_args() -> argparse.Namespace:
    """Parse the v4 runner CLI."""
    parser = argparse.ArgumentParser(description="Run the opt-in Reachy Mini v4 runtime.")
    parser.add_argument("profile_path", type=Path)
    parser.add_argument("--mode", choices=["live", "text", "mock"], default="live")
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--override", action="append", default=[])
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--trace-file", type=Path, default=None)
    parser.add_argument("--message", "-m", default="")
    return parser.parse_args()


def main() -> None:
    """CLI entry point for ``python -m reachy_mini.pipeline.runner``."""
    args = parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))
    overrides = _parse_overrides(args.override)
    if args.no_camera:
        overrides["vision.no_camera"] = True
    if args.mode not in {"text", "mock"}:
        raise SystemExit("Phase 1 runner supports --mode text/mock for automated smoke.")
    text = str(args.message or "").strip()
    if not text:
        text = input("You: ").strip()
    frames = asyncio.run(
        run_text_turn(
            profile_path=args.profile_path,
            text=text,
            overrides=overrides,
        )
    )
    if args.trace_file is not None:
        _write_trace(args.trace_file, frames)
    _print_frames(frames)


def _parse_overrides(values: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for value in values:
        key, sep, raw = value.partition("=")
        if not sep:
            raise SystemExit(f"Invalid override, expected key=value: {value}")
        overrides[key] = _coerce(raw)
    return overrides


def _coerce(raw: str) -> Any:
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _write_trace(path: Path, frames: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for frame in frames:
            handle.write(json.dumps(_frame_to_dict(frame), ensure_ascii=False) + "\n")


def _print_frames(frames: list[object]) -> None:
    for frame in frames:
        if isinstance(frame, SDKMessageFrame):
            for text in extract_text_blocks(frame.message):
                print(text)
        elif isinstance(frame, ActionResultFrame):
            print(f"action_result: {frame.name} {frame.status}")
        elif isinstance(frame, SpeechPresenterFrame):
            print(f"speech: {frame.text}")
        elif isinstance(frame, TTSAudioFrame):
            print(f"tts_audio: {len(frame.pcm)} bytes")


def _frame_to_dict(frame: object) -> dict[str, Any]:
    data = getattr(frame, "__dict__", {}).copy()
    for key, value in list(data.items()):
        data[key] = _object_to_jsonable(value)
    return {"type": type(frame).__name__, **data}


def _object_to_jsonable(value: object) -> object:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "size": len(value),
            "data": base64.b64encode(value).decode("ascii"),
        }
    if hasattr(value, "__dict__"):
        return {
            key: _object_to_jsonable(item)
            for key, item in value.__dict__.items()
        }
    if isinstance(value, list):
        return [_object_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _object_to_jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    main()
