"""Stable contracts for the enterprise RobotRuntime."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from typing import Any, Self, TypeVar


class LifecycleState(StrEnum):
    """Managed lifecycle states for RobotRuntime and adapters."""

    UNCONFIGURED = "unconfigured"
    CONFIGURING = "configuring"
    INACTIVE = "inactive"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    ERROR = "error"
    RECOVERING = "recovering"
    SHUTDOWN = "shutdown"


class RuntimeMode(StrEnum):
    """Supported runtime deployment modes."""

    AVATAR_ONLY = "avatar_only"
    SIMULATION = "simulation"
    HARDWARE = "hardware"
    HYBRID = "hybrid"


class IntentSource(StrEnum):
    """Origin of an embodied intent."""

    BRAIN = "brain"
    POLICY = "policy"
    SYSTEM = "system"
    OPERATOR = "operator"


class IntentType(StrEnum):
    """Canonical embodied intent types visible to high-level policy."""

    GREET = "greet"
    LISTEN = "listen"
    THINK = "think"
    SPEAK = "speak"
    ACKNOWLEDGE = "acknowledge"
    AGREE = "agree"
    REFUSE = "refuse"
    ATTENTION_SHIFT = "attention_shift"
    TASK_EXECUTE = "task_execute"
    IDLE = "idle"
    RECOVER = "recover"


class SpeechRelation(StrEnum):
    """How an intent should relate to speech output."""

    BEFORE_SPEECH = "before_speech"
    DURING_SPEECH = "during_speech"
    AFTER_SPEECH = "after_speech"
    IDLE = "idle"
    INTERRUPT = "interrupt"


class TimingAnchor(StrEnum):
    """Canonical scheduler timing anchors."""

    TURN_START = "turn_start"
    SPEECH_PREPARE = "speech_prepare"
    SPEECH_START = "speech_start"
    SPEECH_CHUNK = "speech_chunk"
    SPEECH_END = "speech_end"
    TURN_END = "turn_end"
    IDLE = "idle"
    INTERRUPT = "interrupt"
    TASK_START = "task_start"
    TASK_PROGRESS = "task_progress"
    TASK_END = "task_end"


class PlanStatus(StrEnum):
    """Lifecycle of a behavior plan."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DEGRADED = "degraded"


class SafetyStatus(StrEnum):
    """Safety supervisor decision status."""

    ALLOW = "allow"
    DENY = "deny"
    DEGRADE = "degrade"
    DELAY = "delay"


class AdapterResultStatus(StrEnum):
    """Adapter command execution status."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMEOUT = "timeout"


class FallbackPolicy(StrEnum):
    """Scheduler fallback policy."""

    SKIP = "skip"
    DEGRADE = "degrade"
    RETRY = "retry"
    SAFE_IDLE = "safe_idle"


ALLOWED_CHANNELS = frozenset(
    {"gaze", "head", "body", "face", "gesture", "voice", "navigation"}
)

EnumT = TypeVar("EnumT", bound=StrEnum)


def make_robot_id(prefix: str) -> str:
    """Create a traceable runtime id."""
    return f"{prefix}_{uuid.uuid4().hex}"


def now_ms() -> int:
    """Return epoch milliseconds."""
    return int(time.time() * 1000)


def _coerce_enum(value: EnumT | str | None, enum_type: type[EnumT], field_name: str) -> EnumT:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _coerce_optional_enum(
    value: EnumT | str | None,
    enum_type: type[EnumT],
    field_name: str,
) -> EnumT | None:
    if value is None:
        return None
    return _coerce_enum(value, enum_type, field_name)


def _validate_priority(priority: int, field_name: str = "priority") -> None:
    if not 0 <= int(priority) <= 100:
        raise ValueError(f"{field_name} must be between 0 and 100")


def _validate_intensity(intensity: float) -> None:
    if not 0.0 <= float(intensity) <= 1.0:
        raise ValueError("intensity must be between 0.0 and 1.0")


def _validate_channels(channels: list[str], field_name: str) -> None:
    invalid = sorted(set(channels) - ALLOWED_CHANNELS)
    if invalid:
        raise ValueError(f"{field_name} contains unsupported channels: {invalid}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class ContractMixin:
    """Mixin for JSON-friendly dataclass contracts."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize this contract into JSON-friendly primitives."""
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class EmbodiedIntent(ContractMixin):
    """Body-agnostic intent produced by Brain, policy, or system code."""

    intent_type: IntentType | str
    source: IntentSource | str = IntentSource.BRAIN
    intent_id: str = field(default_factory=lambda: make_robot_id("intent"))
    turn_id: str = ""
    created_at_ms: int = field(default_factory=now_ms)
    affect: str | None = None
    target: dict[str, Any] | None = None
    intensity: float = 0.5
    priority: int = 40
    speech_relation: SpeechRelation | str = SpeechRelation.IDLE
    timing_anchor: TimingAnchor | str | None = None
    modalities: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize enums and validate bounded fields."""
        object.__setattr__(
            self,
            "intent_type",
            _coerce_enum(self.intent_type, IntentType, "intent_type"),
        )
        object.__setattr__(self, "source", _coerce_enum(self.source, IntentSource, "source"))
        object.__setattr__(
            self,
            "speech_relation",
            _coerce_enum(self.speech_relation, SpeechRelation, "speech_relation"),
        )
        object.__setattr__(
            self,
            "timing_anchor",
            _coerce_optional_enum(self.timing_anchor, TimingAnchor, "timing_anchor"),
        )
        _validate_intensity(self.intensity)
        _validate_priority(self.priority)
        _validate_channels(self.modalities, "modalities")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an intent from serialized data."""
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AdapterState(ContractMixin):
    """Runtime state exposed by one adapter."""

    adapter_id: str
    lifecycle: LifecycleState | str = LifecycleState.UNCONFIGURED
    mode: RuntimeMode | str = RuntimeMode.AVATAR_ONLY
    active_command_ids: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize adapter enum fields."""
        object.__setattr__(
            self,
            "lifecycle",
            _coerce_enum(self.lifecycle, LifecycleState, "lifecycle"),
        )
        object.__setattr__(self, "mode", _coerce_enum(self.mode, RuntimeMode, "mode"))


@dataclass(frozen=True, slots=True)
class SafetyState(ContractMixin):
    """Aggregate safety status for the runtime."""

    emergency_stop: bool = False
    degraded: bool = False
    active_limits: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RobotState(ContractMixin):
    """RobotRuntime state snapshot."""

    state_id: str = field(default_factory=lambda: make_robot_id("state"))
    observed_at_ms: int = field(default_factory=now_ms)
    lifecycle: LifecycleState | str = LifecycleState.UNCONFIGURED
    mode: RuntimeMode | str = RuntimeMode.AVATAR_ONLY
    active_turn_id: str | None = None
    speech_state: str = "idle"
    input_state: str = "idle"
    attention_target: dict[str, Any] | None = None
    active_behaviors: list[str] = field(default_factory=list)
    active_commands: list[str] = field(default_factory=list)
    adapter_states: dict[str, AdapterState] = field(default_factory=dict)
    safety_state: SafetyState = field(default_factory=SafetyState)
    capability_revision: str = ""

    def __post_init__(self) -> None:
        """Normalize state enum fields."""
        object.__setattr__(
            self,
            "lifecycle",
            _coerce_enum(self.lifecycle, LifecycleState, "lifecycle"),
        )
        object.__setattr__(self, "mode", _coerce_enum(self.mode, RuntimeMode, "mode"))


@dataclass(frozen=True, slots=True)
class BehaviorStep(ContractMixin):
    """One executable step inside a behavior plan."""

    channel: str
    capability_query: dict[str, Any]
    step_id: str = field(default_factory=lambda: make_robot_id("step"))
    plan_id: str = ""
    timing: dict[str, Any] = field(default_factory=dict)
    parallel_group: str | None = None
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    timeout_ms: int = 1000
    required: bool = True

    def __post_init__(self) -> None:
        """Validate behavior step channel and timeout."""
        _validate_channels([self.channel], "channel")
        if int(self.timeout_ms) < 0:
            raise ValueError("timeout_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class BehaviorPlan(ContractMixin):
    """Scheduler-ready plan produced by RobotRuntime policy."""

    intent_id: str
    plan_id: str = field(default_factory=lambda: make_robot_id("plan"))
    status: PlanStatus | str = PlanStatus.DRAFT
    priority: int = 40
    timing_anchor: TimingAnchor | str = TimingAnchor.IDLE
    start_after_ms: int | None = None
    deadline_ms: int | None = None
    duration_ms: int | None = None
    interruptible: bool = True
    channels: list[str] = field(default_factory=list)
    steps: list[BehaviorStep] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    fallback_policy: FallbackPolicy | str = FallbackPolicy.SAFE_IDLE

    def __post_init__(self) -> None:
        """Normalize plan enums and validate scheduling fields."""
        object.__setattr__(self, "status", _coerce_enum(self.status, PlanStatus, "status"))
        object.__setattr__(
            self,
            "timing_anchor",
            _coerce_enum(self.timing_anchor, TimingAnchor, "timing_anchor"),
        )
        object.__setattr__(
            self,
            "fallback_policy",
            _coerce_enum(self.fallback_policy, FallbackPolicy, "fallback_policy"),
        )
        _validate_priority(self.priority)
        _validate_channels(self.channels, "channels")
        for field_name in ("start_after_ms", "deadline_ms", "duration_ms"):
            value = getattr(self, field_name)
            if value is not None and int(value) < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class Capability(ContractMixin):
    """A body-agnostic capability exposed by one adapter."""

    capability_id: str
    adapter_id: str
    embodiment: str
    modality: str
    channels: list[str]
    semantic_tags: list[str] = field(default_factory=list)
    affect_range: list[str] = field(default_factory=list)
    intensity_range: tuple[float, float] = (0.0, 1.0)
    input_schema: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    timing_profile: dict[str, Any] = field(default_factory=dict)
    interruptibility: str = "soft"
    source_asset: dict[str, Any] | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        """Validate capability channel and matching bounds."""
        _validate_channels(self.channels, "channels")
        lo, hi = self.intensity_range
        if not 0.0 <= float(lo) <= float(hi) <= 1.0:
            raise ValueError("intensity_range must be within [0.0, 1.0]")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class RobotCommand(ContractMixin):
    """Safety-approved command sent to one adapter."""

    plan_id: str
    step_id: str
    adapter_id: str
    capability_id: str
    command_type: str
    command_id: str = field(default_factory=lambda: make_robot_id("command"))
    payload: dict[str, Any] = field(default_factory=dict)
    start_at_ms: int | None = None
    duration_ms: int | None = None
    timeout_ms: int = 1000
    cancellation_policy: str = "soft_stop"
    safety_envelope: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate command timing fields."""
        if int(self.timeout_ms) < 0:
            raise ValueError("timeout_ms must be non-negative")
        for field_name in ("start_at_ms", "duration_ms"):
            value = getattr(self, field_name)
            if value is not None and int(value) < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class SafetyDecision(ContractMixin):
    """Decision emitted by the safety supervisor."""

    command_id: str
    status: SafetyStatus | str
    decision_id: str = field(default_factory=lambda: make_robot_id("safety"))
    checked_at_ms: int = field(default_factory=now_ms)
    reasons: list[str] = field(default_factory=list)
    effective_limits: dict[str, Any] = field(default_factory=dict)
    replacement_command: RobotCommand | None = None
    operator_action_required: bool = False
    expires_at_ms: int | None = None

    def __post_init__(self) -> None:
        """Normalize safety status and validate expiry."""
        object.__setattr__(self, "status", _coerce_enum(self.status, SafetyStatus, "status"))
        if self.expires_at_ms is not None and int(self.expires_at_ms) < int(self.checked_at_ms):
            raise ValueError("expires_at_ms cannot be earlier than checked_at_ms")


@dataclass(frozen=True, slots=True)
class AdapterResult(ContractMixin):
    """Execution result returned by an adapter."""

    command_id: str
    adapter_id: str
    status: AdapterResultStatus | str
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize result status and validate timing."""
        object.__setattr__(
            self,
            "status",
            _coerce_enum(self.status, AdapterResultStatus, "status"),
        )
        if (
            self.started_at_ms is not None
            and self.ended_at_ms is not None
            and int(self.ended_at_ms) < int(self.started_at_ms)
        ):
            raise ValueError("ended_at_ms cannot be earlier than started_at_ms")
        if self.duration_ms is not None and int(self.duration_ms) < 0:
            raise ValueError("duration_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class RobotEvent(ContractMixin):
    """Trace event emitted by RobotRuntime components."""

    source: str
    event_type: str
    event_id: str = field(default_factory=lambda: make_robot_id("event"))
    ts_ms: int = field(default_factory=now_ms)
    severity: str = "info"
    turn_id: str | None = None
    intent_id: str | None = None
    plan_id: str | None = None
    command_id: str | None = None
    status: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


__all__ = [
    "ALLOWED_CHANNELS",
    "AdapterResult",
    "AdapterResultStatus",
    "AdapterState",
    "BehaviorPlan",
    "BehaviorStep",
    "Capability",
    "EmbodiedIntent",
    "FallbackPolicy",
    "IntentSource",
    "IntentType",
    "LifecycleState",
    "PlanStatus",
    "RobotCommand",
    "RobotEvent",
    "RobotState",
    "RuntimeMode",
    "SafetyDecision",
    "SafetyState",
    "SafetyStatus",
    "SpeechRelation",
    "TimingAnchor",
    "make_robot_id",
    "now_ms",
]
