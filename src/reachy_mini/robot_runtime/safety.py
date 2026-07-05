"""Safety supervisor for RobotRuntime command execution."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from reachy_mini.robot_runtime.contracts import (
    Capability,
    LifecycleState,
    RobotCommand,
    RobotState,
    RuntimeMode,
    SafetyDecision,
    SafetyStatus,
    now_ms,
)


@dataclass(frozen=True, slots=True)
class SafetyRuleResult:
    """Result returned by a custom safety rule."""

    status: SafetyStatus
    reasons: tuple[str, ...] = ()
    effective_limits: dict[str, object] = field(default_factory=dict)
    replacement_command: RobotCommand | None = None
    operator_action_required: bool = False


class SafetyRule(Protocol):
    """Custom safety rule protocol."""

    def evaluate(
        self,
        command: RobotCommand,
        state: RobotState,
        capability: Capability | None,
    ) -> SafetyRuleResult | None:
        """Evaluate one command against the current runtime state."""


@dataclass(slots=True)
class SafetySupervisor:
    """Mandatory safety gate before adapter execution."""

    limits: dict[str, object] = field(default_factory=dict)
    rules: list[SafetyRule] = field(default_factory=list)

    def evaluate(
        self,
        command: RobotCommand,
        state: RobotState,
        capability: Capability | None = None,
    ) -> SafetyDecision:
        """Return a structured allow/deny/degrade decision."""
        results = self._built_in_results(command, state, capability)
        for rule in self.rules:
            result = rule.evaluate(command, state, capability)
            if result is not None:
                results.append(result)
        return self._decision(command, results)

    def _built_in_results(
        self,
        command: RobotCommand,
        state: RobotState,
        capability: Capability | None,
    ) -> list[SafetyRuleResult]:
        results: list[SafetyRuleResult] = []
        if state.safety_state.emergency_stop:
            results.append(_deny("emergency_stop_active", operator_required=True))
        if state.lifecycle is not LifecycleState.ACTIVE:
            results.append(_deny(f"runtime_not_active:{state.lifecycle.value}"))
        mode_result = _mode_gate(command, state, capability)
        if mode_result is not None:
            results.append(mode_result)
        profile_mode_result = _profile_mode_gate(state, self.limits)
        if profile_mode_result is not None:
            results.append(profile_mode_result)
        blocked_adapter_result = _blocked_adapter_gate(command, self.limits)
        if blocked_adapter_result is not None:
            results.append(blocked_adapter_result)
        watchdog_result = _watchdog_gate(command, state, self.limits)
        if watchdog_result is not None:
            results.append(watchdog_result)
        results.extend(self._limit_results(command))
        if _speech_motion_conflict(state, capability):
            results.append(_degrade(command, "speech_active_motion_degraded"))
        if not results:
            results.append(SafetyRuleResult(status=SafetyStatus.ALLOW))
        return results

    def _limit_results(self, command: RobotCommand) -> list[SafetyRuleResult]:
        reasons: list[str] = []
        effective_limits: dict[str, object] = {}
        replacement = command
        max_duration = _optional_int(self.limits.get("max_duration_ms"))
        if max_duration is not None and (command.duration_ms or 0) > max_duration:
            replacement = replace(replacement, duration_ms=max_duration)
            reasons.append("duration_limited")
            effective_limits["max_duration_ms"] = max_duration
        max_timeout = _optional_int(self.limits.get("max_timeout_ms"))
        if max_timeout is not None and command.timeout_ms > max_timeout:
            replacement = replace(replacement, timeout_ms=max_timeout)
            reasons.append("timeout_limited")
            effective_limits["max_timeout_ms"] = max_timeout
        max_intensity = _optional_float(self.limits.get("max_intensity"))
        intensity = _payload_intensity(command)
        if (
            max_intensity is not None
            and intensity is not None
            and intensity > max_intensity
        ):
            payload = {**replacement.payload, "intensity": max_intensity}
            replacement = replace(replacement, payload=payload)
            reasons.append("intensity_limited")
            effective_limits["max_intensity"] = max_intensity
        if not reasons:
            return []
        return [
            SafetyRuleResult(
                status=SafetyStatus.DEGRADE,
                reasons=tuple(reasons),
                effective_limits=effective_limits,
                replacement_command=replacement,
            )
        ]

    def _decision(
        self,
        command: RobotCommand,
        results: list[SafetyRuleResult],
    ) -> SafetyDecision:
        status = _highest_status(results)
        reasons = [reason for result in results for reason in result.reasons]
        limits = {
            key: value
            for result in results
            for key, value in result.effective_limits.items()
        }
        replacement = next(
            (item.replacement_command for item in results if item.replacement_command),
            None,
        )
        return SafetyDecision(
            command_id=command.command_id,
            status=status,
            reasons=reasons,
            effective_limits=limits,
            replacement_command=replacement,
            operator_action_required=any(item.operator_action_required for item in results),
        )


def _deny(reason: str, *, operator_required: bool = False) -> SafetyRuleResult:
    return SafetyRuleResult(
        status=SafetyStatus.DENY,
        reasons=(reason,),
        operator_action_required=operator_required,
    )


def _degrade(command: RobotCommand, reason: str) -> SafetyRuleResult:
    return SafetyRuleResult(
        status=SafetyStatus.DEGRADE,
        reasons=(reason,),
        replacement_command=replace(command, command_type="safe_idle", payload={}),
    )


def _highest_status(results: list[SafetyRuleResult]) -> SafetyStatus:
    order = {
        SafetyStatus.ALLOW: 0,
        SafetyStatus.DELAY: 1,
        SafetyStatus.DEGRADE: 2,
        SafetyStatus.DENY: 3,
    }
    return max((item.status for item in results), key=lambda item: order[item])


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, list | tuple | set | frozenset):
        return {str(item) for item in value}
    return {str(value)}


def _payload_intensity(command: RobotCommand) -> float | None:
    value = command.payload.get("intensity")
    if value is None:
        return None
    return float(value)


def _speech_motion_conflict(state: RobotState, capability: Capability | None) -> bool:
    if state.speech_state not in {"preparing", "speaking"}:
        return False
    if capability is None:
        return False
    return bool({"head", "body", "gesture"} & set(capability.channels))


def _mode_gate(
    command: RobotCommand,
    state: RobotState,
    capability: Capability | None,
) -> SafetyRuleResult | None:
    adapter_mode = _adapter_mode(command, state, capability)
    if adapter_mode is None:
        return None
    if adapter_mode in _allowed_adapter_modes(state.mode):
        return None
    return _deny(
        f"mode_mismatch:runtime={state.mode.value},adapter={adapter_mode.value}"
    )


def _profile_mode_gate(
    state: RobotState,
    limits: dict[str, object],
) -> SafetyRuleResult | None:
    allowed_modes = _string_set(limits.get("allowed_modes"))
    if not allowed_modes or state.mode.value in allowed_modes:
        return None
    return _deny(f"runtime_mode_blocked:{state.mode.value}")


def _blocked_adapter_gate(
    command: RobotCommand,
    limits: dict[str, object],
) -> SafetyRuleResult | None:
    blocked_adapters = _string_set(limits.get("blocked_adapters"))
    if command.adapter_id not in blocked_adapters:
        return None
    return _deny(f"adapter_blocked:{command.adapter_id}")


def _allowed_adapter_modes(runtime_mode: RuntimeMode) -> frozenset[RuntimeMode]:
    if runtime_mode is RuntimeMode.HYBRID:
        return frozenset(RuntimeMode)
    if runtime_mode is RuntimeMode.SIMULATION:
        return frozenset({RuntimeMode.SIMULATION, RuntimeMode.AVATAR_ONLY})
    if runtime_mode is RuntimeMode.HARDWARE:
        return frozenset({RuntimeMode.HARDWARE, RuntimeMode.AVATAR_ONLY})
    return frozenset({RuntimeMode.AVATAR_ONLY})


def _adapter_mode(
    command: RobotCommand,
    state: RobotState,
    capability: Capability | None,
) -> RuntimeMode | None:
    adapter_state = state.adapter_states.get(command.adapter_id)
    if adapter_state is not None:
        return adapter_state.mode
    if capability is None:
        return None
    return _mode_from_embodiment(capability.embodiment)


def _mode_from_embodiment(embodiment: str) -> RuntimeMode | None:
    value = embodiment.strip().lower()
    if value in {"live2d", "avatar", "avatar3d"}:
        return RuntimeMode.AVATAR_ONLY
    if value in {"mujoco", "simulation", "sim"}:
        return RuntimeMode.SIMULATION
    if value in {"reachy", "hardware", "robot"}:
        return RuntimeMode.HARDWARE
    if value == "ros2":
        return RuntimeMode.HYBRID
    return None


def _watchdog_gate(
    command: RobotCommand,
    state: RobotState,
    limits: dict[str, object],
) -> SafetyRuleResult | None:
    timeout_ms = _optional_int(limits.get("watchdog_timeout_ms"))
    if timeout_ms is None:
        return None
    adapter_state = state.adapter_states.get(command.adapter_id)
    if adapter_state is None or adapter_state.mode is not RuntimeMode.HARDWARE:
        return None
    heartbeat_ms = _optional_int(adapter_state.metadata.get("last_heartbeat_ms"))
    if heartbeat_ms is None:
        return _deny(
            f"watchdog_missing:{command.adapter_id}",
            operator_required=True,
        )
    age_ms = max(0, now_ms() - heartbeat_ms)
    if age_ms > timeout_ms:
        return _deny(
            f"watchdog_stale:{command.adapter_id}",
            operator_required=True,
        )
    return None


__all__ = ["SafetyRule", "SafetyRuleResult", "SafetySupervisor"]
