"""v4 opt-in pipeline package."""

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    ActionResultFrame,
    ActionSpecFrame,
    BrainReplyFrame,
    BrowserInputFrame,
    InterruptFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
)
from .speech_presenter import SpeechPresenter

__all__ = [
    "ActionDispatcher",
    "ActionResultFrame",
    "ActionSpecFrame",
    "BrainProcessor",
    "BrainReplyFrame",
    "BrowserInputFrame",
    "InterruptFrame",
    "SpeechActivityFrame",
    "SpeechPresenter",
    "SpeechPresenterFrame",
    "TranscriptionFrame",
    "TTSAudioFrame",
]
