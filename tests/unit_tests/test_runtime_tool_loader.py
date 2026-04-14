"""Tests for runtime system/profile tool loading."""

from pathlib import Path

from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.tool_loader import build_runtime_tool_bundle


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "有真实工具就要用真实工具。",
        "FRONT.md": "表达自然。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"brain_model","provider":"mock","model":"reachy_mini_brain_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_runtime_tool_bundle_splits_kernel_front_and_profile_tools(tmp_path: Path) -> None:
    """Runtime tools should separate kernel, front, and profile ownership."""
    app_root = tmp_path / "demo_app"
    profile_root = app_root / "profiles"
    profile_root.mkdir(parents=True)
    _write_profile(profile_root)
    (profile_root / "tools" / "custom_tool.py").write_text(
        (
            "from reachy_mini.runtime.profile_tools import FunctionTool\n"
            "\n"
            "def build_tools(*, workspace_root, profile_root, tools_dir):\n"
            "    _ = workspace_root\n"
            "    _ = profile_root\n"
            "    _ = tools_dir\n"
            "    return [FunctionTool(\n"
            "        name='ping_tool',\n"
            "        description='Return pong.',\n"
            "        parameters={'type': 'object', 'properties': {'message': {'type': 'string'}}, 'required': ['message']},\n"
            "        func=lambda *, message: f'pong:{message}',\n"
            "    )]\n"
        ),
        encoding="utf-8",
    )

    profile = load_profile_bundle(profile_root)
    bundle = build_runtime_tool_bundle(profile)

    assert bundle.workspace_root == app_root.resolve()
    assert "write_file" in bundle.kernel_system_tool_names
    assert "read_file" in bundle.kernel_system_tool_names
    assert "move_head" not in bundle.kernel_system_tool_names
    assert "do_nothing" in bundle.front_tool_names
    assert "move_head" in bundle.front_tool_names
    assert "head_tracking" in bundle.front_tool_names
    assert "camera" in bundle.front_tool_names
    assert "play_emotion" in bundle.front_tool_names
    assert "dance" in bundle.front_tool_names
    assert "stop_emotion" in bundle.front_tool_names
    assert "stop_dance" in bundle.front_tool_names
    assert "ping_tool" in bundle.profile_tool_names
    assert "ping_tool" in [str(getattr(tool, "name", "") or "").strip() for tool in bundle.kernel_tools]
    assert "move_head" not in [str(getattr(tool, "name", "") or "").strip() for tool in bundle.kernel_tools]
