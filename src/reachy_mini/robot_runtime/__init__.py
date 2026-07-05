"""Enterprise RobotRuntime public API."""

from .adapters.base import AdapterContext, RobotAdapter
from .behavior_tree import RobotBehaviorNodeSnapshot, RobotBehaviorTreeSnapshot
from .bridges import PolicyServiceBackend, PolicyServiceRequest, PolicyServiceResponse
from .capabilities import CapabilityMatch, CapabilityQuery, CapabilityRegistry
from .config import RobotAdapterConfig, RobotRuntimeConfig, SafetyProfileConfig
from .contracts import (
    AdapterResult,
    AdapterResultStatus,
    AdapterState,
    BehaviorPlan,
    BehaviorStep,
    Capability,
    EmbodiedIntent,
    FallbackPolicy,
    IntentSource,
    IntentType,
    LifecycleState,
    PlanStatus,
    RobotCommand,
    RobotEvent,
    RobotState,
    RuntimeMode,
    SafetyDecision,
    SafetyState,
    SafetyStatus,
    SpeechRelation,
    TimingAnchor,
)
from .metrics import RobotMetricsSnapshot
from .policies import LearnedPolicy
from .policy import (
    PlanPolicy,
    PolicyContext,
    PolicyResult,
    RobotPolicyEngine,
    create_default_policy_engine,
)
from .resolver import CapabilityResolver, ResolutionResult
from .runtime import RobotRuntime, RobotRuntimeDependencies
from .safety import SafetyRule, SafetyRuleResult, SafetySupervisor
from .scheduler import ScheduleDecision, ScheduledPlan, TimelineScheduler
from .structured_log import RobotStructuredLogRecord
from .telemetry import RobotTelemetrySink, RobotTraceContext, RobotTraceTimeline
from .tools import RobotIntentTools
from .visualization import (
    RerunExportResult,
    RerunTimelineExporter,
    RobotVisualizationRecord,
    export_timeline_to_rerun,
    visualization_records,
)

__all__ = [
    "AdapterContext",
    "AdapterResult",
    "AdapterResultStatus",
    "AdapterState",
    "Avatar3DAdapter",
    "BehaviorPlan",
    "BehaviorStep",
    "Capability",
    "CapabilityMatch",
    "CapabilityQuery",
    "CapabilityRegistry",
    "CapabilityResolver",
    "EmbodiedIntent",
    "FallbackPolicy",
    "IntentSource",
    "IntentType",
    "LifecycleState",
    "LearnedPolicy",
    "PlanStatus",
    "PlanPolicy",
    "PolicyServiceBackend",
    "PolicyServiceRequest",
    "PolicyServiceResponse",
    "PolicyContext",
    "PolicyResult",
    "RobotAdapterConfig",
    "RobotAdapter",
    "RobotBehaviorNodeSnapshot",
    "RobotBehaviorTreeSnapshot",
    "RobotCommand",
    "RobotEvent",
    "RobotMetricsSnapshot",
    "RobotRuntime",
    "RobotRuntimeConfig",
    "RobotRuntimeDependencies",
    "RobotIntentTools",
    "RobotPolicyEngine",
    "RobotState",
    "RobotStructuredLogRecord",
    "RobotVisualizationRecord",
    "ROS2Adapter",
    "RuntimeMode",
    "RerunExportResult",
    "RerunTimelineExporter",
    "ResolutionResult",
    "RobotTelemetrySink",
    "RobotTraceContext",
    "RobotTraceTimeline",
    "SafetyRule",
    "SafetyRuleResult",
    "SafetySupervisor",
    "SafetyDecision",
    "SafetyProfileConfig",
    "SafetyState",
    "SafetyStatus",
    "ScheduleDecision",
    "SpeechRelation",
    "ScheduledPlan",
    "TimingAnchor",
    "TimelineScheduler",
    "create_default_policy_engine",
    "export_timeline_to_rerun",
    "visualization_records",
]

_LAZY_EXPORTS = {
    "Avatar3DAdapter": "reachy_mini.robot_runtime.adapters.avatar3d",
    "ROS2Adapter": "reachy_mini.robot_runtime.adapters.ros2",
}


def __getattr__(name: str) -> object:
    """Load adapter exports that pull optional UI/runtime dependencies lazily."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
