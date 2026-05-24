"""Action registry and lightweight JSON schema validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .action import ActionSpec, RobotAction
from .errors import ActionParamError, DuplicateActionError, UnknownActionError
from .metadata import ActionBuilder, ActionMetadata


class ActionRegistry:
    """Source of truth for available v4 robot actions."""

    def __init__(self) -> None:
        """Create an empty action registry."""
        self._metadata: dict[str, ActionMetadata] = {}
        self._builders: dict[str, ActionBuilder] = {}

    def register(self, metadata: ActionMetadata, builder: ActionBuilder) -> None:
        """Register action metadata and a builder."""
        if metadata.name in self._metadata:
            raise DuplicateActionError(f"Action already registered: {metadata.name}")
        if "-" in metadata.name:
            raise ActionParamError(
                f"Action name must use snake_case, got {metadata.name!r}."
            )
        self._metadata[metadata.name] = metadata
        self._builders[metadata.name] = builder

    def get_metadata(self, name: str) -> ActionMetadata:
        """Return metadata for a registered action."""
        try:
            return self._metadata[name]
        except KeyError as exc:
            raise UnknownActionError(f"Unknown action: {name}") from exc

    def build(self, spec: ActionSpec) -> RobotAction:
        """Validate an action spec and build an executable action."""
        metadata = self.get_metadata(spec.name)
        validate_json_schema_subset(spec.params, metadata.parameter_schema)
        return self._builders[spec.name](spec)

    def list_metadata(self) -> list[ActionMetadata]:
        """Return shallow copies of all action metadata."""
        return list(self._metadata.values())

    def tool_schemas(self) -> list[dict[str, Any]]:
        """Return LLM-safe tool definitions for registered actions."""
        return [
            {
                "name": metadata.name,
                "description": metadata.description,
                "input_schema": deepcopy(metadata.parameter_schema),
                "metadata": {"tags": list(metadata.tags)},
            }
            for metadata in self.list_metadata()
        ]


def validate_json_schema_subset(value: Any, schema: dict[str, Any]) -> None:
    """Validate a value against the draft-07 subset used by action metadata."""
    _validate(value, schema, path="$")


def _validate(value: Any, schema: dict[str, Any], *, path: str) -> None:
    if "anyOf" in schema:
        errors: list[str] = []
        for option in schema["anyOf"]:
            try:
                _validate(value, option, path=path)
                return
            except ActionParamError as exc:
                errors.append(str(exc))
        raise ActionParamError(f"{path} did not match any schema: {errors}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            [expected_type] if isinstance(expected_type, str) else list(expected_type)
        )
        if not any(_matches_type(value, item) for item in expected_types):
            raise ActionParamError(
                f"{path} expected type {expected_types}, got {type(value).__name__}."
            )

    if "enum" in schema and value not in schema["enum"]:
        raise ActionParamError(f"{path} expected one of {schema['enum']}, got {value!r}.")

    if isinstance(value, dict):
        _validate_object(value, schema, path=path)
    elif isinstance(value, list):
        _validate_array(value, schema, path=path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(float(value), schema, path=path)


def _validate_object(value: dict[str, Any], schema: dict[str, Any], *, path: str) -> None:
    required = schema.get("required", [])
    for field_name in required:
        if field_name not in value:
            raise ActionParamError(f"{path}.{field_name} is required.")

    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        if key in properties:
            _validate(item, properties[key], path=f"{path}.{key}")
        elif additional is False:
            raise ActionParamError(f"{path}.{key} is not allowed.")


def _validate_array(value: list[Any], schema: dict[str, Any], *, path: str) -> None:
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        raise ActionParamError(f"{path} has fewer than {schema['minItems']} items.")
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        raise ActionParamError(f"{path} has more than {schema['maxItems']} items.")

    item_schema = schema.get("items")
    if item_schema is None:
        return
    for index, item in enumerate(value):
        _validate(item, item_schema, path=f"{path}[{index}]")


def _validate_number(value: float, schema: dict[str, Any], *, path: str) -> None:
    if "minimum" in schema and value < float(schema["minimum"]):
        raise ActionParamError(f"{path} must be >= {schema['minimum']}.")
    if "maximum" in schema and value > float(schema["maximum"]):
        raise ActionParamError(f"{path} must be <= {schema['maximum']}.")


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise ActionParamError(f"Unsupported schema type: {expected_type}")
