"""v4 opt-in pipeline package.

Heavy modules (session, ws_app, pipecat_runtime) are imported lazily so that
lightweight helpers such as frames / speech_presenter can be used without
pulling the full runtime graph (and its optional third-party deps).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    ActionResultFrame,
    AudioFrame,
    BrowserInputFrame,
    EmbodimentFrame,
    InterruptFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
)
from .live_io import CallableSpeakerSink, MicrophoneSource, SpeakerSink
from .output_bus import OutputBus, OutputSubscription
from .speech_presenter import SpeechPresenter
from .wire import WireDecodeError, WireSerializationError, decode_inbound, encode_frame

if TYPE_CHECKING:
    from .session import RuntimeSession as RuntimeSession

_LAZY = {
    "RuntimeSession": ".session",
    "WS_PATH": ".ws_app",
    "build_ws_app": ".ws_app",
    "run_ws_app": ".ws_app",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name], __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ActionDispatcher",
    "ActionResultFrame",
    "AudioFrame",
    "BrainProcessor",
    "BrowserInputFrame",
    "CallableSpeakerSink",
    "EmbodimentFrame",
    "InterruptFrame",
    "MicrophoneSource",
    "OutputBus",
    "OutputSubscription",
    "RuntimeSession",
    "SDKMessageFrame",
    "SpeakerSink",
    "SpeechActivityFrame",
    "SpeechPresenter",
    "SpeechPresenterFrame",
    "TranscriptionFrame",
    "TTSAudioFrame",
    "WireDecodeError",
    "WireSerializationError",
    "WS_PATH",
    "build_ws_app",
    "decode_inbound",
    "encode_frame",
    "run_ws_app",
]
