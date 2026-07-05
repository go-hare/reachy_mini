"""Body-agnostic payload validation for model and policy boundaries."""

from __future__ import annotations

from collections.abc import Mapping

_ADAPTER_PRIVATE_KEYS = frozenset(
    {
        "actuator",
        "actuator_id",
        "actuator_name",
        "adapter_id",
        "capability_id",
        "command",
        "command_id",
        "command_type",
        "expression_file",
        "joint",
        "joint_id",
        "joint_name",
        "joint_targets",
        "motion_file",
        "motor",
        "motor_id",
        "motor_name",
        "motor_targets",
        "raw_command",
        "safety_envelope",
        "source_asset",
    }
)
_ADAPTER_PRIVATE_MARKERS = (
    ".exp3.json",
    ".moc3",
    ".model3.json",
    ".motion3.json",
    ".vtube.json",
    "body_rotation",
    "left_antenna",
    "live2d_expression_",
    "live2d_motion_",
    "raw_motor",
    "right_antenna",
    "stewart_1",
    "stewart_2",
    "stewart_3",
    "stewart_4",
    "stewart_5",
    "stewart_6",
)


def validate_body_agnostic_value(field_name: str, value: object) -> None:
    """Reject adapter-private fields and identifiers in a generic payload."""
    if value is None:
        return
    _scan_body_agnostic_value(root=field_name, path=field_name, value=value)


def _scan_body_agnostic_value(
    *,
    root: str,
    path: str,
    value: object,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            key_path = f"{path}.{key_text}"
            if _is_adapter_private_key(key_text):
                raise ValueError(
                    f"{root} contains adapter-private field at {key_path}"
                )
            _scan_body_agnostic_string(root=root, path=key_path, value=key_text)
            _scan_body_agnostic_value(root=root, path=key_path, value=item)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for index, item in enumerate(value):
            _scan_body_agnostic_value(
                root=root,
                path=f"{path}[{index}]",
                value=item,
            )
        return
    if isinstance(value, str):
        _scan_body_agnostic_string(root=root, path=path, value=value)


def _scan_body_agnostic_string(*, root: str, path: str, value: str) -> None:
    marker = _adapter_private_marker(value)
    if marker is None:
        return
    raise ValueError(
        f"{root} contains adapter-private identifier at {path}: {marker}"
    )


def _is_adapter_private_key(value: str) -> bool:
    return value.strip().lower() in _ADAPTER_PRIVATE_KEYS


def _adapter_private_marker(value: str) -> str | None:
    normalized = value.strip().lower()
    for marker in _ADAPTER_PRIVATE_MARKERS:
        if marker in normalized:
            return marker
    return None


__all__ = ["validate_body_agnostic_value"]
