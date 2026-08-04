#!/usr/bin/env python3
"""E2E smoke: Pi RPC brain using ~/.claude/settings.json gateway (auto models.json staging)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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
from reachy_mini.robot_runtime.config import RobotRuntimeConfig  # noqa: E402
from reachy_mini.robot_runtime.runtime import RobotRuntime  # noqa: E402
from reachy_mini.robot_runtime.tools import RobotIntentTools  # noqa: E402


def _load_claude_env() -> dict[str, str]:
    settings_path = Path.home() / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    env = data.get("env") or {}
    if not env.get("ANTHROPIC_AUTH_TOKEN") and not env.get("ANTHROPIC_API_KEY"):
        raise SystemExit(f"no ANTHROPIC token in {settings_path}")
    return {str(k): str(v) for k, v in env.items()}


def _write_temp_models_json(*, base: str, model: str) -> Path:
    agent_dir = Path(tempfile.mkdtemp(prefix="pi-agent-claude-e2e-"))
    models = {
        "providers": {
            "anthropic": {
                "baseUrl": base,
                "api": "anthropic-messages",
                "apiKey": "$ANTHROPIC_AUTH_TOKEN",
                "authHeader": True,
                "models": [
                    {
                        "id": model,
                        "name": "Grok via Claude gateway",
                        "reasoning": True,
                        "input": ["text"],
                        "contextWindow": 200000,
                        "maxTokens": 8192,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }
    (agent_dir / "models.json").write_text(
        json.dumps(models, indent=2), encoding="utf-8"
    )
    return agent_dir


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
    # Parent env: proxies + auth. models.json is staged by PiBinaryBrain.
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

    runtime = RobotRuntime(config=RobotRuntimeConfig(enabled=True))
    tools = RobotIntentTools(runtime)
    bridge_obj = PiToolBridge(robot_tools=tools)
    server = PiToolBridgeServer(bridge_obj, host="127.0.0.1", port=0)
    await runtime.start()
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
            'intent_type="greet" before answering. Then reply briefly in Chinese.'
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
            if ame.get("type") in {"text_delta", "thinking_delta"}:
                summary["delta"] = str(ame.get("delta") or "")[:80]
        elif t and "tool" in str(t):
            summary["name"] = ev.get("toolName") or ev.get("name") or ev.get("tool")
            summary["keys"] = list(ev.keys())[:15]
            for k in ("error", "result", "status", "isError"):
                if k in ev:
                    summary[k] = str(ev.get(k))[:200]
        elif "error" in json.dumps(ev, ensure_ascii=False).lower():
            summary["raw"] = {k: str(v)[:160] for k, v in list(ev.items())[:12]}
        else:
            summary["keys"] = list(ev.keys())[:10]
        events.append(summary)
        print("EV", json.dumps(summary, ensure_ascii=False)[:320])

    unsub = brain._client.on_event(on_ev)
    turn = BrainTurnInput(
        text="你好，请打个招呼并做个 greet 动作。",
        turn_id="e2e-claude-1",
        context="body=live2d; intent_tools_enabled=true",
    )
    texts: list[str] = []
    results: list[str] = []
    try:
        async for msg in brain.run_turn(turn):
            print(
                "MSG",
                type(msg).__name__,
                "result=",
                getattr(msg, "result", None),
                "content=",
                getattr(msg, "content", None),
            )
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    texts.append(getattr(block, "text", "") or "")
            if isinstance(msg, PiResultMessage):
                results.append(str(msg.result or ""))
    except Exception as exc:
        print("TURN_ERR", type(exc).__name__, exc)
        traceback.print_exc()
        print("stderr", (brain._client.stderr if brain._client else "")[-2000:])
    finally:
        unsub()

    metrics = await tools.query_robot_metrics()
    log = await tools.query_robot_structured_log(limit=20)
    full_text = "".join(texts).strip()
    print("TEXT", full_text[:800])
    print("RESULT", results)
    print("METRICS", metrics)
    print("LOG", json.dumps(log, ensure_ascii=False)[:1200])
    print("EVENT_TYPES", [e.get("type") for e in events])
    print("stderr_tail", (brain._client.stderr if brain._client else "")[-1500:])
    try:
        state = await brain._client.get_state()
        print("STATE", json.dumps(state, ensure_ascii=False)[:1000])
    except Exception as exc:
        print("STATE_ERR", exc)

    await brain.stop()
    await server.stop()
    await runtime.stop()

    text_ok = bool(full_text or any(r.strip() for r in results))
    intent_count = 0
    if isinstance(metrics, dict):
        intent_count = int(
            metrics.get("intent_count")
            or metrics.get("intents_handled")
            or (metrics.get("metrics") or {}).get("intent_count")
            or 0
        )
    log_blob = json.dumps(log, ensure_ascii=False).lower()
    log_intents = log_blob.count("emit_embodied_intent") + log_blob.count(
        '"intent_type"'
    )
    tool_events = sum(1 for e in events if e.get("type") and "tool" in str(e.get("type")))
    ok = text_ok or intent_count > 0 or tool_events > 0
    print(
        "E2E",
        "PASS" if ok else "FAIL",
        "text_ok",
        text_ok,
        "intent_count",
        intent_count,
        "tool_events",
        tool_events,
        "log_intents",
        log_intents,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
