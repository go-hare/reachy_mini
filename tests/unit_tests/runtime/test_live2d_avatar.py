"""Tests for Live2D avatar capability loading."""

from __future__ import annotations

import json
from pathlib import Path

from reachy_mini.runtime.live2d_avatar import (
    live2d_system_prompt,
    load_live2d_capabilities,
)


def test_load_live2d_capabilities_from_vtube_config(tmp_path: Path) -> None:
    """Live2D capabilities come from the model's VTube config."""
    profile_root = tmp_path / "demo_app" / "profiles"
    static_root = tmp_path / "demo_app" / "demo_app" / "static"
    model_root = static_root / "assets" / "live2d" / "IceGirl"
    model_root.mkdir(parents=True)
    profile_root.mkdir(parents=True)
    (static_root / "avatar.config.json").write_text(
        json.dumps(
            {
                "mode": "live2d",
                "live2d": {
                    "root": "/static/assets/live2d/IceGirl",
                    "vtube": "IceGirl.vtube.json",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.vtube.json").write_text(
        json.dumps(
            {
                "FileReferences": {
                    "Model": "IceGirl.model3.json",
                    "IdleAnimation": "DaiJi.motion3.json",
                },
                "Hotkeys": [
                    {"Action": "TriggerAnimation", "File": "HuiShou.motion3.json"},
                    {"Action": "ToggleExpression", "File": "惊讶.exp3.json"},
                    {"Action": "ToggleExpression", "File": "脸红.exp3.json"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.model3.json").write_text(
        json.dumps(
            {
                "FileReferences": {
                    "Moc": "IceGirl.moc3",
                    "DisplayInfo": "IceGirl.cdi3.json",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.cdi3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "Parameters": [
                    {"Id": "Param58", "Name": "挥手"},
                    {"Id": "Param59", "Name": "挥手"},
                    {"Id": "JingYa", "Name": "惊讶"},
                    {"Id": "Param31", "Name": "脸红"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_root / "DaiJi.motion3.json").write_text(
        json.dumps({"Meta": {"Duration": 12}, "Curves": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_root / "HuiShou.motion3.json").write_text(
        json.dumps(
            {
                "Meta": {"Duration": 7},
                "Curves": [
                    {"Target": "Parameter", "Id": "Param58"},
                    {"Target": "Parameter", "Id": "Param59"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_root / "惊讶.exp3.json").write_text(
        json.dumps({"Parameters": [{"Id": "JingYa"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_root / "脸红.exp3.json").write_text(
        json.dumps({"Parameters": [{"Id": "Param31"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    capabilities = load_live2d_capabilities(profile_root)

    assert capabilities is not None
    assert capabilities.model_name == "IceGirl"
    assert capabilities.model_file == "IceGirl.model3.json"
    assert capabilities.idle_motion == "DaiJi"
    assert capabilities.motions == ("DaiJi", "HuiShou")
    assert capabilities.expressions == ("惊讶", "脸红")
    assert capabilities.motion_details[1].name == "HuiShou"
    assert capabilities.motion_details[1].file_name == "HuiShou.motion3.json"
    assert capabilities.motion_details[1].parameter_ids == ("Param58", "Param59")
    assert capabilities.motion_details[1].parameter_labels == ("挥手",)
    assert "举手" in capabilities.motion_details[1].aliases
    assert "招手" in capabilities.motion_details[1].aliases
    assert capabilities.expression_details[0].parameter_labels == ("惊讶",)

    prompt = live2d_system_prompt(capabilities)
    assert "Current embodiment mode: Live2D avatar" in prompt
    assert "live2d_motion_huishou" in prompt
    assert "mcp__reachy_actions__live2d_motion_huishou" in prompt
    assert "HuiShou.motion3.json" in prompt
    assert "挥手" in prompt
    assert "举手" in prompt
    assert "招手" in prompt
    assert "live2d_expression_jing_ya" in prompt
    assert "惊讶.exp3.json" in prompt
