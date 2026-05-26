"""v4 opt-in pipeline package."""

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    ActionResultFrame,
    AudioFrame,
    BrowserInputFrame,
    InterruptFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
)
from .live_io import CallableSpeakerSink, MicrophoneSource, SpeakerSink
from .output_bus import OutputBus, OutputSubscription
from .session import RuntimeSession
from .speech_presenter import SpeechPresenter
from .wire import WireDecodeError, WireSerializationError, decode_inbound, encode_frame
from .ws_app import WS_PATH, build_ws_app, run_ws_app

__all__ = [
    "ActionDispatcher",
    "ActionResultFrame",
    "AudioFrame",
    "BrainProcessor",
    "BrowserInputFrame",
    "CallableSpeakerSink",
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
