import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:/work/py/reachy_mini/src")
sys.path.insert(0, str(ROOT))

from ccmini.agent import AgentConfig
from ccmini.bridge.client import BridgeClient
from ccmini.bridge.core import BridgeConfig
from ccmini.bridge.host import create_remote_executor_host
from ccmini.bridge.messaging import BridgeMessage, MessageType
from ccmini.factory import create_coding_agent
from ccmini.kairos.core import GateConfig, is_kairos_active
from ccmini.memory.store import JsonlMemoryStore
from ccmini.messages import CompletionEvent, ErrorEvent, TextEvent
from ccmini.providers import ProviderConfig
from ccmini.services.session_memory import manually_extract_session_memory
from ccmini.tool import Tool, ToolUseContext, find_tool_by_name

base_url = os.environ["CCMINI_LIVE_BASE_URL"]
api_key = os.environ["CCMINI_LIVE_API_KEY"]
model = os.environ["CCMINI_LIVE_MODEL"]

provider_cfg = ProviderConfig(
    type="compatible",
    model=model,
    api_key=api_key,
    base_url=base_url,
    max_tokens=160,
    temperature=0,
    extras={"reasoning_effort": "low"},
)


class EchoUpperTool(Tool):
    name = "EchoUpper"
    description = "Return uppercased text"
    is_read_only = True

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs):
        return str(kwargs.get("text", "")).upper()


async def collect_reply(agent, prompt):
    final = ""
    chunks = []
    errors = []
    async for event in agent.query(prompt):
        if isinstance(event, CompletionEvent):
            final = event.text
        elif isinstance(event, TextEvent):
            chunks.append(event.text)
        elif isinstance(event, ErrorEvent):
            errors.append(event.error)
    return (final or "".join(chunks)).strip(), errors


def tool_context(agent):
    extras = {
        "agent": agent,
        "system_prompt": agent._system_prompt.render(),
        "query_source": "sdk",
        "attachment_collector": agent._attachment_collector,
        "summary_provider": getattr(agent, "_summary_provider", None),
        "fallback_config": getattr(agent, "_fallback_config", None),
        "session_memory_content": agent._get_session_memory_content(),
    }
    return ToolUseContext(
        conversation_id=agent.conversation_id,
        agent_id=agent._agent_id,
        messages=agent.messages,
        extras=extras,
    )


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def main():
    results = {}
    td = tempfile.mkdtemp(prefix="ccmini_core_check_")
    try:
        home = Path(td) / "home"
        cwd = Path(td) / "workspace"
        home.mkdir(parents=True, exist_ok=True)
        cwd.mkdir(parents=True, exist_ok=True)
        os.environ["CCMINI_HOME"] = str(home)
        os.chdir(cwd)
        trusted = cwd / ".ccmini"
        trusted.mkdir(parents=True, exist_ok=True)
        (trusted / "trusted").write_text("", encoding="utf-8")

        agent = create_coding_agent(
            provider=provider_cfg,
            system_prompt="You are a smoke-test agent. Reply with exactly CORE_QUERY_OK and nothing else.",
            tools=[],
        )
        async with agent:
            reply, errors = await collect_reply(agent, "Reply with exactly CORE_QUERY_OK")
            results["query_main"] = {"ok": errors == [] and reply == "CORE_QUERY_OK", "reply": reply, "errors": errors}

            reply2, errors2 = await collect_reply(agent, "Repeat exactly the token you returned in the previous answer.")
            results["query_context"] = {"ok": errors2 == [] and reply2 == "CORE_QUERY_OK", "reply": reply2, "errors": errors2}

            extraction = await manually_extract_session_memory(agent.messages, agent.provider, session_id=agent.conversation_id)
            results["session_memory"] = {"ok": bool(getattr(extraction, "success", False) and getattr(extraction, "content", "")), "memory_path": getattr(extraction, "memory_path", "")}

        tool_agent = create_coding_agent(
            provider=provider_cfg,
            system_prompt="Use tools when needed. If the user asks for uppercasing, call the EchoUpper tool and then reply with only the tool result.",
            tools=[EchoUpperTool()],
        )
        async with tool_agent:
            tool_reply, tool_errors = await collect_reply(tool_agent, "Use the tool to uppercase core_tool_ok and reply with only the result.")
            results["tool_flow"] = {"ok": tool_errors == [] and tool_reply == "CORE_TOOL_OK", "reply": tool_reply, "errors": tool_errors}

        bg_agent = create_coding_agent(
            provider=provider_cfg,
            system_prompt="You are a coordinator smoke-test agent.",
        )
        async with bg_agent:
            ctx = tool_context(bg_agent)
            agent_tool = find_tool_by_name(bg_agent.tools, "Agent")
            if agent_tool is None:
                results["background_subagent"] = {"ok": False, "error": "Agent tool missing"}
            else:
                raw = await agent_tool.execute(
                    context=ctx,
                    name="bg-worker",
                    description="background smoke",
                    prompt="Reply with exactly CORE_BACKGROUND_OK",
                    subagent_type="worker",
                    run_in_background=True,
                )
                parsed = None
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = None
                launched_id = ""
                if parsed:
                    launched_id = str(parsed.get("task_id") or parsed.get("agentId") or "")
                results["background_subagent"] = {
                    "ok": bool(launched_id),
                    "raw": raw[:500],
                    "task_id": launched_id,
                    "agentId": (parsed or {}).get("agentId", ""),
                }

        kairos_agent = create_coding_agent(
            provider=provider_cfg,
            system_prompt="You are a Kairos smoke-test agent. Reply with exactly CORE_KAIROS_OK and nothing else.",
            tools=[],
            config=AgentConfig(
                kairos_gate_config=GateConfig(
                    kairos_enabled=True,
                    brief_enabled=True,
                    cron_enabled=True,
                    channels_enabled=True,
                    dream_enabled=False,
                )
            ),
        )
        async with kairos_agent:
            kairos_reply, kairos_errors = await collect_reply(kairos_agent, "Reply with exactly CORE_KAIROS_OK")
            results["kairos_lifecycle"] = {"ok": kairos_errors == [] and kairos_reply == "CORE_KAIROS_OK" and is_kairos_active(), "reply": kairos_reply, "errors": kairos_errors, "active": is_kairos_active()}

        store = JsonlMemoryStore(home / "profiletest")
        store.append_brain_record("conv1", {"role": "user", "content": "hello"})
        recent = store.recent_brain_records("conv1", 5)
        results["jsonl_memory_store"] = {"ok": len(recent) == 1 and recent[0].get("content") == "hello", "recent": recent}

        port = get_free_port()
        host = create_remote_executor_host(
            provider=provider_cfg,
            system_prompt="You are a remote smoke-test agent. Reply with exactly CORE_BRIDGE_OK and nothing else.",
            config=AgentConfig(max_turns=8),
            tools=[],
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=port, auth_token="localtesttoken"),
        )
        await host.start()
        try:
            handle = await host.create_session()
            client = BridgeClient(base_url=handle.base_url, auth_token=handle.auth_token, session_id=handle.session_id, poll_interval=0.3)
            await client.connect()
            events = []
            await client.send_message(BridgeMessage(type=MessageType.QUERY, payload={"text": "Reply with exactly CORE_BRIDGE_OK"}, session_id=handle.session_id))
            for _ in range(80):
                polled = await client.poll_events(limit=100)
                events.extend(polled)
                if any(((e.get("payload") or {}).get("event_type") == "completion") for e in events):
                    break
                await asyncio.sleep(0.25)
            completion_payloads = [e.get("payload", {}) for e in events if (e.get("payload") or {}).get("event_type") == "completion"]
            bridge_text = completion_payloads[-1].get("text", "").strip() if completion_payloads else ""
            results["bridge_remote"] = {"ok": bridge_text == "CORE_BRIDGE_OK", "reply": bridge_text, "events": len(events)}
            await client.close()
        finally:
            await host.stop()

        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass


asyncio.run(main())
