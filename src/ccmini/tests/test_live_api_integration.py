from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ccmini.agent import AgentConfig
from ccmini.delegation.multi_agent import AgentPool, SubAgentConfig, run_sub_agent
from ccmini.factory import create_coding_agent
from ccmini.kairos.core import GateConfig
from ccmini.kairos.inbox import get_inbox_snapshot
from ccmini.messages import CompletionEvent, ErrorEvent, TextEvent
from ccmini.providers import ProviderConfig, create_provider
from ccmini.services.session_memory import manually_extract_session_memory
from ccmini.tool import ToolUseContext, find_tool_by_name


def _live_provider_config(*, max_tokens: int = 256) -> ProviderConfig:
    base_url = os.environ.get("CCMINI_LIVE_BASE_URL", "").strip()
    api_key = os.environ.get("CCMINI_LIVE_API_KEY", "").strip()
    model = os.environ.get("CCMINI_LIVE_MODEL", "").strip()
    if not (base_url and api_key and model):
        pytest.skip("Live API env is missing. Set CCMINI_LIVE_BASE_URL / CCMINI_LIVE_API_KEY / CCMINI_LIVE_MODEL.")
    return ProviderConfig(
        type="compatible",
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=0,
        extras={"reasoning_effort": "low"},
    )


def _prepare_live_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    trusted: bool = False,
) -> tuple[Path, Path]:
    for key in list(os.environ):
        if key.startswith(("CCMINI_", "MINI_AGENT_", "MINI_TRUST_", "CLAUDE_CODE_")) and not key.startswith("CCMINI_LIVE_"):
            monkeypatch.delenv(key, raising=False)

    home = tmp_path / "home"
    cwd = tmp_path / "workspace"
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CCMINI_HOME", str(home))
    monkeypatch.chdir(cwd)
    if trusted:
        trusted_dir = cwd / ".ccmini"
        trusted_dir.mkdir(parents=True, exist_ok=True)
        (trusted_dir / "trusted").write_text("", encoding="utf-8")
    return home, cwd


async def _collect_reply(agent: object, prompt: str) -> tuple[str, list[str]]:
    final = ""
    chunks: list[str] = []
    errors: list[str] = []
    async for event in agent.query(prompt):  # type: ignore[attr-defined]
        if isinstance(event, CompletionEvent):
            final = event.text
        elif isinstance(event, TextEvent):
            chunks.append(event.text)
        elif isinstance(event, ErrorEvent):
            errors.append(event.error)
    return final or "".join(chunks), errors


def _tool_context(agent: object) -> ToolUseContext:
    extras = {
        "agent": agent,
        "system_prompt": agent._system_prompt.render(),  # type: ignore[attr-defined]
        "query_source": "sdk",
        "attachment_collector": agent._attachment_collector,  # type: ignore[attr-defined]
        "summary_provider": getattr(agent, "_summary_provider", None),
        "fallback_config": getattr(agent, "_fallback_config", None),
        "session_memory_content": agent._get_session_memory_content(),  # type: ignore[attr-defined]
    }
    return ToolUseContext(
        conversation_id=agent.conversation_id,  # type: ignore[attr-defined]
        agent_id=agent._agent_id,  # type: ignore[attr-defined]
        messages=agent.messages,  # type: ignore[attr-defined]
        extras=extras,
    )

async def _wait_for(predicate: Callable[[], bool], *, timeout: float = 80.0, interval: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for live integration condition")


def test_live_main_flow_buddy_and_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_live_environment(monkeypatch, tmp_path)
    provider = _live_provider_config(max_tokens=160)

    async def run() -> None:
        agent = create_coding_agent(
            provider=provider,
            system_prompt="You are a smoke-test agent. Reply with the exact token requested and nothing else.",
            tools=[],
        )
        async with agent:
            reply, errors = await _collect_reply(agent, "Reply with exactly LIVE_MAIN_FLOW_OK")
            assert errors == []
            assert reply == "LIVE_MAIN_FLOW_OK"

            buddy_cmd = agent._command_registry.get("buddy")  # type: ignore[attr-defined]
            assert buddy_cmd is not None
            hatch = await buddy_cmd.execute("hatch Smoke", agent)
            pet = await buddy_cmd.execute("pet", agent)
            status = await buddy_cmd.execute("status", agent)
            assert "Smoke" in hatch
            assert "pets total" in pet
            assert "Smoke" in status

            extraction = await manually_extract_session_memory(
                agent.messages,
                agent.provider,
                session_id=agent.conversation_id,
            )
            assert extraction.success
            assert extraction.memory_path
            assert "# " in extraction.content

    asyncio.run(run())


def test_live_subagent_and_parallel_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_live_environment(monkeypatch, tmp_path)
    provider = create_provider(_live_provider_config(max_tokens=96))

    async def run() -> None:
        subagent = await run_sub_agent(
            provider=provider,
            config=SubAgentConfig(
                name="live-subagent",
                role="general",
                system_prompt="Reply with exactly LIVE_SUBAGENT_OK",
                max_turns=3,
            ),
            prompt="Return the required token.",
        )
        assert subagent.success
        assert subagent.reply == "LIVE_SUBAGENT_OK"

        pool = AgentPool(provider)
        results = await pool.run_parallel(
            [
                (
                    SubAgentConfig(
                        name="parallel-one",
                        role="general",
                        system_prompt="Reply with exactly LIVE_POOL_ONE_OK",
                        max_turns=3,
                    ),
                    "Return the required token.",
                ),
                (
                    SubAgentConfig(
                        name="parallel-two",
                        role="general",
                        system_prompt="Reply with exactly LIVE_POOL_TWO_OK",
                        max_turns=3,
                    ),
                    "Return the required token.",
                ),
            ]
        )
        assert [result.success for result in results] == [True, True]
        assert {result.reply for result in results} == {"LIVE_POOL_ONE_OK", "LIVE_POOL_TWO_OK"}

    asyncio.run(run())


def test_live_coordinator_team_and_messaging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_live_environment(monkeypatch, tmp_path, trusted=True)
    provider = _live_provider_config(max_tokens=192)

    async def run() -> None:
        agent = create_coding_agent(
            provider=provider,
            system_prompt="You are a coordinator smoke-test agent.",
        )
        async with agent:
            agent._coordinator_mode.activate(  # type: ignore[attr-defined]
                coordinator_tool_names=[tool.name for tool in agent.tools],
                worker_tool_names=[tool.name for tool in agent.tools if tool.name != "Agent"],
            )

            team_tool = agent._team_create_tool  # type: ignore[attr-defined]
            ctx = _tool_context(agent)
            team_data = json.loads(
                await team_tool.execute(
                    context=ctx,
                    team_name="live-team",
                    description="live coordinator smoke",
                )
            )
            team_name = team_data["team_name"]
            team = team_tool.get_team(team_name)
            assert team is not None

            agent_tool = find_tool_by_name(agent.tools, "Agent")
            assert agent_tool is not None
            spawn_data = json.loads(
                await agent_tool.execute(
                    context=ctx,
                    name="worker-one",
                    description="worker smoke",
                    prompt="Reply with a single line containing LIVE_TEAM_WORKER_OK",
                    subagent_type="worker",
                )
            )

            teammate = team.get_teammate(spawn_data["teammate_id"])
            assert teammate is not None
            await _wait_for(
                lambda: teammate.state.is_idle and teammate.state.messages_processed >= 1
            )
            assert teammate.state.error == ""
            first_messages = team.mailbox.read_and_mark("team-lead")
            assert any("LIVE_TEAM_WORKER_OK" in message.text for message in first_messages)

            send_tool = find_tool_by_name(agent.tools, "SendMessage")
            assert send_tool is not None
            send_result = await send_tool.execute(
                context=ctx,
                to="worker-one",
                summary="live follow-up",
                message="Reply with a single line containing LIVE_TEAM_FOLLOWUP_OK",
            )
            assert "Message sent to teammate" in send_result

            await _wait_for(
                lambda: teammate.state.is_idle and teammate.state.messages_processed >= 2
            )
            second_messages = team.mailbox.read_and_mark("team-lead")
            assert any("LIVE_TEAM_FOLLOWUP_OK" in message.text for message in second_messages)

            await team.shutdown_all()

    asyncio.run(run())


def test_live_kairos_query_cron_and_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_live_environment(monkeypatch, tmp_path, trusted=True)
    provider = _live_provider_config(max_tokens=160)

    async def run() -> None:
        kairos_agent = create_coding_agent(
            provider=provider,
            system_prompt="You are a Kairos smoke-test agent. Reply with the exact token requested and nothing else.",
            tools=[],
            config=AgentConfig(
                kairos_gate_config=GateConfig(
                    kairos_enabled=True,
                    brief_enabled=True,
                    cron_enabled=True,
                    channels_enabled=True,
                    dream_enabled=False,
                ),
            ),
        )
        async with kairos_agent:
            reply, errors = await _collect_reply(kairos_agent, "Reply with exactly LIVE_KAIROS_OK")
            assert errors == []
            assert reply == "LIVE_KAIROS_OK"
            assert kairos_agent.is_kairos_active()

        tools_agent = create_coding_agent(
            provider=provider,
            system_prompt="You are a Kairos tools smoke-test agent.",
            config=AgentConfig(
                kairos_gate_config=GateConfig(
                    kairos_enabled=True,
                    brief_enabled=True,
                    cron_enabled=True,
                    channels_enabled=True,
                    dream_enabled=False,
                ),
            ),
        )
        async with tools_agent:
            ctx = _tool_context(tools_agent)
            cron_create = find_tool_by_name(tools_agent.tools, "CronCreate")
            cron_list = find_tool_by_name(tools_agent.tools, "CronList")
            push_tool = find_tool_by_name(tools_agent.tools, "PushNotification")
            assert cron_create is not None
            assert cron_list is not None
            assert push_tool is not None

            created = json.loads(
                await cron_create.execute(
                    context=ctx,
                    name="live-cron",
                    cron_expr="*/5 * * * *",
                    prompt="ping",
                )
            )
            listed = json.loads(await cron_list.execute(context=ctx))
            assert any(task["id"] == created["id"] for task in listed["tasks"])

            pushed = json.loads(
                await push_tool.execute(
                    context=ctx,
                    title="live-kairos",
                    body="push smoke",
                    priority="high",
                )
            )
            assert pushed["ok"] is True

            snapshot = get_inbox_snapshot(limit_per_stream=10)
            assert any(
                item.get("title") == "live-kairos"
                for item in snapshot.get("push_notifications", [])
            )

    asyncio.run(run())
