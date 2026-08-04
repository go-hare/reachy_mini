#!/usr/bin/env python3
"""E2E smoke: Pi RPC brain + Claude gateway + Live2D adapter body path."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reachy_mini.reachy_brain.agent import BrainTurnInput  # noqa: E402
from reachy_mini.reachy_brain.config import (  # noqa: E402
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.pi_binary_brain import (  # noqa: E402
    AssistantMessage,
    PiBinaryBrain,
    PiResultMessage,
)
from reachy_mini.reachy_brain.pi_tool_bridge import PiToolBridge  # noqa: E402
from reachy_mini.reachy_brain.pi_tool_bridge_server import PiToolBridgeServer  # noqa: E402
from reachy_mini.robot_runtime.adapters.live2d import Live2DAdapter  # noqa: E402
from reachy_mini.robot_runtime.config import RobotRuntimeConfig  # noqa: E402
from reachy_mini.robot_runtime.contracts import RuntimeMode  # noqa: E402
from reachy_mini.robot_runtime.runtime import RobotRuntime  # noqa: E402
from reachy_mini.robot_runtime.tools import RobotIntentTools  # noqa: E402
from reachy_mini.runtime.live2d_avatar import (  # noqa: E402
    Live2DCapabilities,
    Live2DNativeAction,
)


def _load_claude_env() -> dict[str, str]:
    settings_path = Path.home() / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    env = data.get("env") or {}
    if not env.get("ANTHROPIC_AUTH_TOKEN") and not env.get("ANTHROPIC_API_KEY"):
        raise SystemExit(f"no ANTHROPIC token in {settings_path}")
    return {str(k): str(v) for k, v in env.items()}


def _live2d_capabilities() -> Live2DCapabilities:
    """In-memory IceGirl-like caps so greet resolves without profile assets."""
    return Live2DCapabilities(
        model_name="IceGirl",
        root_url="/static/assets/live2d/IceGirl",
        vtube_file="IceGirl.vtube.json",
        model_file="IceGirl.model3.json",
        idle_motion="DaiJi",
        motions=("DaiJi", "HuiShou"),
        expressions=(),
        motion_details=(
            Live2DNativeAction(
                name="HuiShou",
                file_name="HuiShou.motion3.json",
                aliases=("greet", "wave"),
                duration_s=2.0,
            ),
        ),
    )


async def main() -> int:
    env_cfg = _load_claude_env()
    token = env_cfg.get("ANTHROPIC_AUTH_TOKEN") or env_cfg.get("ANTHROPIC_API_KEY") or ""
    base = (env_cfg.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    model = env_cfg.get("ANTHROPIC_MODEL") or "grok-4.5"
    proxy = env_cfg.get("HTTP_PROXY") or env_cfg.get("HTTPS_PROXY") or ""
    no_proxy = env_cfg.get("NO_PROXY") or "localhost,127.0.0.1,::1"

    if not base:
        raise SystemExit("ANTHROPIC_BASE_URL missing from Claude settings")

    print("gateway", base, "model", model)

    ext = Path(r"D:\work\py\pi\packages\robot-agent\extensions\robot-tools.ts")
    child_env = {
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_BASE_URL": base,
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "PI_OFFLINE": "1",
        "REACHY_BRIDGE_MODE": "http",
        "REACHY_PI_EXT": str(ext),
    }
    os.environ.update(child_env)

    frames: list[object] = []

    async def publish_frame(frame: object) -> None:
        frames.append(frame)
        print(
            "FRAME",
            getattr(frame, "action", None),
            getattr(frame, "target", None),
            getattr(frame, "payload", None),
            getattr(frame, "turn_id", None),
        )

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(enabled=True, mode=RuntimeMode.AVATAR_ONLY)
    )
    tools = RobotIntentTools(runtime)
    bridge_obj = PiToolBridge(robot_tools=tools)
    server = PiToolBridgeServer(bridge_obj, host="127.0.0.1", port=0)
    await runtime.start()
    await runtime.register_adapter(
        Live2DAdapter(_live2d_capabilities(), publish_frame=publish_frame)
    )
    await server.start()
    url = server.base_url
    print("bridge", url)
    child_env["REACHY_RUNTIME_URL"] = url

    cfg = AgentConfig(
        model=ModelConfig(
            provider="anthropic",
            model=model,
            api_key=token,
            base_url=base,
        ),
        speech=SpeechConfig(),
        speech_input=SpeechInputConfig(),
        vision=VisionConfig(),
        extras={},
    )
    brain = PiBinaryBrain(
        config=cfg,
        runtime_url=url,
        tool_bridge=bridge_obj,
        extension_path=ext,
        extra_env=child_env,
        system_prompt_append=(
            "You control a robot body only via the emit_embodied_intent tool. "
            'When the user greets you, you MUST call emit_embodied_intent with '
            'intent_type="greet" (and modalities=["gesture"] if supported) '
            "before answering. Then reply briefly in Chinese."
        ),
    )

    events: list[dict] = []
    await brain.start()
    assert brain._client is not None

    def on_ev(ev: dict) -> None:
        t = ev.get("type")
        summary: dict = {"type": t}
        if t == "message_update":
            ame = ev.get("assistantMessageEvent") or {}
            summary["ame_type"] = ame.get("type")
            if ame.get("type") == "text_delta":
                summary["delta"] = str(ame.get("delta") or "")[:80]
        elif t and "tool" in str(t):
            summary["name"] = ev.get("toolName") or ev.get("name") or ev.get("tool")
            for k in ("error", "result", "status", "isError"):
                if k in ev:
                    summary[k] = str(ev.get(k))[:240]
        events.append(summary)
        if summary.get("ame_type") != "thinking_delta":
            print("EV", json.dumps(summary, ensure_ascii=False)[:320])

    unsub = brain._client.on_event(on_ev)
    turn = BrainTurnInput(
        text="你好，请打个招呼并做个 greet 动作。",
        turn_id="e2e-claude-live2d-1",
        context="body=live2d; intent_tools_enabled=true; adapter=live2d",
    )
    texts: list[str] = []
    results: list[str] = []
    try:
        async for msg in brain.run_turn(turn):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    texts.append(getattr(block, "text", "") or "")
            if isinstance(msg, PiResultMessage):
                results.append(str(msg.result or ""))
                print("MSG PiResultMessage result=", (msg.result or "")[:200])
    except Exception as exc:
        print("TURN_ERR", type(exc).__name__, exc)
        traceback.print_exc()
        print("stderr", (brain._client.stderr if brain._client else "")[-2000:])
    finally:
        unsub()

    metrics = await tools.query_robot_metrics()
    log = await tools.query_robot_structured_log(limit=30)
    full_text = "".join(texts).strip()
    print("TEXT", full_text[:800])
    print("RESULT", [r[:200] for r in results])
    print(
        "METRICS",
        {
            k: metrics.get(k)
            for k in (
                "intent_count",
                "completed_intent_count",
                "adapter_success_count",
                "capability_unresolved_count",
                "adapter_results_by_adapter",
            )
        },
    )
    print(
        "FRAMES",
        [
            {
                "action": getattr(f, "action", None),
                "target": getattr(f, "target", None),
                "payload": getattr(f, "payload", None),
                "turn_id": getattr(f, "turn_id", None),
            }
            for f in frames
        ],
    )
    print(
        "EVENT_TYPES",
        [e.get("type") for e in events if e.get("type") != "message_update"][:40],
    )
    try:
        state = await brain._client.get_state()
        print("STATE_MODEL", (state.get("model") or {}).get("id"), (state.get("model") or {}).get("baseUrl"))
    except Exception as exc:
        print("STATE_ERR", exc)

    await brain.stop()
    await server.stop()
    await runtime.stop()

    intent_count = int(metrics.get("intent_count") or 0)
    adapter_ok = int(metrics.get("adapter_success_count") or 0)
    unresolved = int(metrics.get("capability_unresolved_count") or 0)
    frame_ok = any(
        getattr(f, "action", None) == "live2d_motion"
        and (getattr(f, "payload", None) or {}).get("name") == "HuiShou"
        for f in frames
    )
    text_ok = bool(full_text or any(r.strip() for r in results))
    tool_events = sum(
        1 for e in events if e.get("type") and "tool_execution" in str(e.get("type"))
    )

    ok = (
        intent_count >= 1
        and adapter_ok >= 1
        and unresolved == 0
        and frame_ok
        and tool_events >= 1
    )
    print(
        "E2E",
        "PASS" if ok else "FAIL",
        "text_ok",
        text_ok,
        "intent_count",
        intent_count,
        "adapter_success",
        adapter_ok,
        "unresolved",
        unresolved,
        "frame_ok",
        frame_ok,
        "tool_events",
        tool_events,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
