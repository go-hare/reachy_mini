"""Tests for RobotRuntime smoke and external preflight checks."""

from __future__ import annotations

import json

import pytest

from reachy_mini.robot_runtime.smoke import (
    SmokeCheckResult,
    SmokeSuiteResult,
    main,
    run_avatar3d_frame_smoke,
    run_mujoco_facade_smoke,
    run_reachy_hardware_preflight,
    run_robot_runtime_smoke_suite,
    run_ros2_bridge_preflight,
)


def test_smoke_result_serializes_status_reason_and_details() -> None:
    """Smoke result payloads are JSON-friendly audit artifacts."""
    result = SmokeCheckResult(
        name="check",
        status="skipped",
        reason="missing dependency",
        details={"items": ["a"]},
    )
    suite = SmokeSuiteResult((result,))

    assert result.to_dict() == {
        "name": "check",
        "status": "skipped",
        "reason": "missing dependency",
        "details": {"items": ["a"]},
    }
    assert suite.status == "skipped"
    assert suite.to_dict()["checks"] == [result.to_dict()]


def test_smoke_suite_reports_partial_when_external_checks_are_skipped() -> None:
    """Skipped external checks keep aggregate status distinct from full pass."""
    suite = SmokeSuiteResult(
        (
            SmokeCheckResult(name="local", status="passed"),
            SmokeCheckResult(name="external", status="skipped"),
        )
    )

    assert suite.status == "partial"
    assert suite.to_dict()["status"] == "partial"


@pytest.mark.asyncio
async def test_mujoco_facade_smoke_runs_generic_intent() -> None:
    """MuJoCo smoke proves a generic intent reaches the adapter facade."""
    result = await run_mujoco_facade_smoke()

    assert result.status == "passed"
    assert result.details["commands"] == 1
    assert result.details["adapter_id"] == "mujoco"


@pytest.mark.asyncio
async def test_avatar3d_frame_smoke_publishes_frame() -> None:
    """3D avatar smoke proves a generic intent produces a renderer frame."""
    result = await run_avatar3d_frame_smoke()

    assert result.status == "passed"
    assert result.details["frames"]
    assert result.details["frames"][0]["action"] == "avatar3d_animation"


@pytest.mark.asyncio
async def test_ros2_bridge_preflight_skips_without_ros2_executable() -> None:
    """ROS2 preflight is explicit when the external CLI is unavailable."""
    result = await run_ros2_bridge_preflight(ros2_executable="")

    assert result.status == "skipped"
    assert result.reason == "ros2 executable not found"


def test_reachy_hardware_preflight_skips_without_device_globs() -> None:
    """Hardware smoke does not run when no candidate serial device exists."""
    result = run_reachy_hardware_preflight(device_globs=())

    assert result.status == "skipped"
    assert result.reason == "no candidate Reachy serial device found"


@pytest.mark.asyncio
async def test_robot_runtime_smoke_suite_can_run_local_only() -> None:
    """Local-only suite excludes external ROS2 and hardware gates."""
    suite = await run_robot_runtime_smoke_suite(include_external=False)

    assert suite.status == "passed"
    assert [check.name for check in suite.checks] == [
        "mujoco_facade",
        "avatar3d_frame",
    ]


def test_smoke_cli_emits_json_for_local_only(capsys: pytest.CaptureFixture[str]) -> None:
    """The module can be used as a repeatable JSON smoke command."""
    exit_code = main(["--local-only", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "passed"
    assert [check["name"] for check in payload["checks"]] == [
        "mujoco_facade",
        "avatar3d_frame",
    ]
