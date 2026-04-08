# ccmini 作为 Embeddable Unified Brain 的宿主接口

这份文档面向这样的使用方式：

- 你把 `ccmini` 当作长期持有的统一大脑
- 宿主负责具身执行、前台表达、实时控制、安全环
- 宿主希望通过稳定 API 驱动用户回合、宿主事件和外部工具恢复
- 宿主不想再访问 `agent._private_field`

目标不是把 `ccmini` 变成另一个 CLI，而是把它当成可嵌入的 brain SDK。

## 一句话边界

推荐分层：

- **宿主负责**
  - 电机 / 相机 / 语音 / 表情真实执行
  - 前台 UI / TTS / 动画 / 状态展示
  - 安全控制、实时环路、传感器采样
- **ccmini 负责**
  - query/tool loop
  - client tool 暂停恢复
  - background task / multi-agent / coordinator
  - memory / session / prompt composition
  - 对宿主发出流式事件

## 推荐创建方式

如果你想把默认 coding 工具池关掉，建议显式关闭：

```python
from ccmini import create_agent
from ccmini.providers import ProviderConfig

agent = create_agent(
    provider=ProviderConfig(
        type="openai",
        model="gpt-5.4",
        api_key="...",
        base_url="https://...",
    ),
    system_prompt="You are the unified brain of an embodied host.",
    profile="robot_brain",
    use_default_tools=False,
    tools=[
        # 只放宿主真正希望暴露给大脑的工具
    ],
)
```

如果你要保留默认 runtime 行为，也可以继续用默认装配，不需要改老代码。

## 生命周期

把 `Agent` 当作常驻实例，而不是单次函数调用：

```python
await agent.start()
try:
    ...
finally:
    await agent.stop()
```

`start()` 之后，session hook、idle loop、Kairos 常驻能力、background runner 才算真正进入工作状态。

## 宿主最小嵌入示例

```python
import asyncio

from ccmini import HostEvent, HostToolResult, create_agent
from ccmini.providers import ProviderConfig
from ccmini.messages import CompletionEvent, PendingToolCallEvent


async def main() -> None:
    agent = create_agent(
        provider=ProviderConfig(type="openai", model="gpt-5.4", api_key="..."),
        system_prompt="You are the unified brain of an embodied host.",
        profile="robot_brain",
        use_default_tools=False,
        tools=[],
    )

    agent.set_working_directory("D:/runtime/workspace")
    agent.set_memory_roots(
        profile_root="D:/runtime/profile/brain-a",
    )
    agent.set_append_system_prompt(
        "You are embedded inside a robot host. Prefer stable, reversible actions."
    )
    agent.set_user_context({
        "hostName": "reachy-mini-host",
        "deployment": "lab-a",
    })

    async def handle_event(event: object) -> None:
        if isinstance(event, PendingToolCallEvent):
            results = []
            for call in event.calls:
                if call.tool_name == "speak":
                    results.append(
                        HostToolResult(
                            tool_use_id=call.tool_use_id,
                            text="speech queued",
                            metadata={"tts_job": "job-123"},
                        )
                    )
            async for followup in agent.submit_tool_results(event.run_id, results):
                handle_sync_event(followup)
        else:
            handle_sync_event(event)

    def handle_sync_event(event: object) -> None:
        if isinstance(event, CompletionEvent):
            print("assistant:", event.text)
        else:
            print("event:", getattr(event, "type", type(event).__name__))

    unsubscribe = agent.on_event(handle_event)

    await agent.start()
    try:
        turn_id = agent.submit_user_input(
            "跟用户打个招呼",
            user_id="user-1",
            metadata={"source": "speech_asr"},
        )
        print("turn_id:", turn_id)

        agent.publish_host_event(
            HostEvent(
                conversation_id=agent.conversation_id,
                event_type="sensor_summary",
                role="system",
                text="battery=82%; user_present=yes; estop=no",
                metadata={"source": "robot_state"},
            )
        )

        await agent.wait_reply(timeout=30.0)
    finally:
        unsubscribe()
        await agent.stop()


asyncio.run(main())
```

## 正式宿主接口

### 1. 用户回合入口

推荐用：

```python
turn_id = agent.submit_user_input(
    "用户说的话",
    conversation_id="conv-1",
    user_id="user-42",
    metadata={"source": "asr"},
    attachments=[...],
)
```

特点：

- 非阻塞，立即返回 `turn_id`
- 适合前台 runtime、机器人主循环、游戏循环
- `metadata / attachments` 会进入正式消息构造链路，而不是丢失

如果宿主希望阻塞式消费，也仍然可以用：

```python
async for event in agent.query(...):
    ...
```

### 2. 事件输出接口

宿主正式可用：

- `agent.on_event(callback)`
- `await agent.wait_event(timeout=...)`
- `agent.poll_event()`
- `agent.drain_events()`
- `agent.event_signal`

### 事件关联字段

宿主可见事件会稳定携带：

- `conversation_id`
- `turn_id`

按事件类型可能还会带：

- `run_id`
- `tool_use_id`
- `metadata`

这让宿主可以把同一回合下的：

- TTS
- 表情
- 前台字幕
- 工具执行
- 打断与恢复

都对齐到同一个 turn。

### 3. 宿主事件注入

宿主可以把前台、传感器、系统摘要注入会话：

```python
from ccmini import HostEvent

agent.publish_host_event(
    HostEvent(
        conversation_id=agent.conversation_id,
        turn_id="optional-turn-id",
        event_type="sensor_summary",
        role="system",
        text="battery=51%; charging=no; head_tracking=yes",
        metadata={"source": "state_fuser"},
    )
)
```

这件事会同时发生三件事：

- 追加到当前会话 transcript
- 作为 runtime event 对宿主立即可见
- 若 memory store 已启用，写入 event stream

适合注入：

- 传感器摘要
- 前台系统事件
- UI 操作结果
- 宿主状态切换

不建议直接注入：

- 高频原始传感器流
- 大体积未摘要日志
- 需要每秒几十次变化的控制量

高频数据应先在宿主侧降采样、融合、摘要。

### 4. 外部工具恢复

当模型触发 client tool 暂停时，宿主会收到 `PendingToolCallEvent`。

恢复方式：

```python
from ccmini import HostToolResult

async for event in agent.submit_tool_results(
    run_id,
    [
        HostToolResult(
            tool_use_id="toolu_123",
            text="camera capture complete",
            is_error=False,
            metadata={"image_id": "img-1"},
            attachments=[
                {"type": "text", "text": "face_detected=yes"},
            ],
        )
    ],
):
    ...
```

特点：

- 不要求宿主只传纯字符串
- 支持 `text / is_error / metadata / attachments`
- 会被归一化进现有 tool-result 消息链，而不是绕私有字段

### 5. 实例级 mode 控制

宿主不必再靠全局 env 切换 coordinator：

```python
agent.set_mode("normal")
agent.set_mode("coordinator")
mode = agent.get_mode()
```

注意：

- 这是实例级 API
- 现有 env 路径仍保留兼容
- 当宿主显式 `set_mode(...)` 后，实例状态优先

### 6. working directory 配置

```python
agent.set_working_directory("D:/runtime/workspace")
cwd = agent.working_directory
```

它会影响：

- 输入里的相对路径解析
- tool context 里的 `working_directory`
- prompt / git status / memory hook 的部分上下文

### 7. memory / session root 注入

推荐宿主显式挂到自己的 runtime 目录：

```python
agent.set_memory_roots(
    profile_root="D:/runtime/profile/brain-a",
    session_root="D:/runtime/profile/brain-a/sessions",
    memory_root="D:/runtime/profile/brain-a/memory",
)
```

或者直接注入自己的 store / adapter：

```python
agent.set_runtime_stores(
    session_store=my_session_store,
    memory_store=my_memory_store,
    memory_adapter=my_memory_adapter,
)
```

也可以只换长期记忆后端：

```python
agent.set_memory_backend(my_memory_store, memory_adapter=my_memory_adapter)
```

这样宿主就不需要依赖默认 `~/.ccmini` 路径。

### 8. system prompt / context 拼接

```python
agent.set_custom_system_prompt("...")
agent.set_append_system_prompt("...")
agent.set_user_context({"robotName": "Reachy"})
agent.set_system_context({"safetyMode": "lab"})
```

推荐语义：

- `set_custom_system_prompt(...)`
  - 完整替换默认 system prompt 主体
- `set_append_system_prompt(...)`
  - 在默认 prompt 后附加宿主规则
- `set_user_context(...)`
  - 注入面向模型的用户/宿主上下文块
- `set_system_context(...)`
  - 注入系统级上下文块

### 9. 宿主侧后台任务控制

宿主可以直接查询和管理 background task：

```python
agent.list_background_tasks(include_completed=True)
agent.get_task(task_id)
agent.cancel_task(task_id)
agent.send_message_to_task(task_id, "继续处理刚才的问题")
```

适合：

- 前台任务板
- 多 agent 宿主 dashboard
- 机器人前台“正在思考/后台处理中”状态页

### 10. 当前状态查询

宿主常用状态：

- `agent.is_busy`
- `agent.pending_client_run_id`
- `agent.pending_client_calls`
- `agent.last_reply`

### 11. 协调者 / 多 Agent / team / peer

宿主可以直接控制协调者与协作运行时，而不必只能等模型通过工具间接驱动：

```python
agent.set_mode("coordinator")
agent.is_coordinator_mode()
agent.list_live_peers()

team_info = await agent.create_team(
    team_name="robot-runtime-team",
    description="runtime-owned persistent team",
)
await agent.delete_team("robot-runtime-team")
```

适合：

- 前台任务中心
- 宿主自己的 team 生命周期管理
- 多 session / 多 agent orchestration dashboard

说明：

- `set_mode(...)` / `is_coordinator_mode()` 是实例级接口
- `list_live_peers()` 面向同机其他运行中的 session
- `create_team(...)` / `delete_team(...)` 面向 persistent teammate / team 生命周期

### 12. Kairos 宿主运行时接口

Kairos 不是普通 query API，它承担的是自主运行时：

- sleep / wake
- proactive tick
- cron
- channel wake
- brief / transcript 视图
- inbox 快照

宿主可以直接控制：

```python
agent.configure_kairos(
    {
        "kairos_enabled": True,
        "brief_enabled": True,
        "proactive_enabled": True,
        "cron_enabled": True,
        "channels_enabled": True,
    }
)

await agent.activate_kairos(mode="assistant", trust_accepted=True)
agent.is_kairos_active()
agent.get_kairos_state()

agent.pause_proactive()
agent.resume_proactive()
agent.is_proactive_active()

agent.enqueue_runtime_command(
    source="system",
    content="background maintenance ready",
    metadata={"source": "host"},
)
await agent.wake("system", "wake now", {"source": "host"})

await agent.publish_channel_notification(
    "server:alerts",
    "battery low on robot-2",
    sender="ops",
)
```

#### Cron

```python
task = agent.create_cron_task(
    name="night-check",
    cron_expr="0 * * * *",
    prompt="run runtime check",
)
tasks = agent.list_cron_tasks()
agent.delete_cron_task(task.id)
```

#### Brief / view / inbox

```python
agent.set_brief_level("minimal")
agent.get_brief_level()

agent.set_view_mode("chat")
agent.get_view_mode()

snapshot = agent.get_inbox_snapshot()
```

宿主侧推荐用途：

- 机器人空闲自治开关
- 外部系统唤醒 brain
- channel / cron / proactive 的前台可视化
- transcript/chat 双视图
- bridge / frontend / robot runtime 共用的 inbox 面板

### 13. Buddy / Companion 宿主接口

Buddy / Companion 不只是 UI 装饰，它会影响：

- prompt / intro attachment 注入
- post-sampling 反应
- mute / hatch / pet 生命周期
- 前台可视 render payload

宿主可以直接控制：

```python
agent.enable_buddy(True)
agent.is_buddy_enabled()

companion = agent.hatch_companion(name="Momo")
agent.get_companion()

agent.set_companion_muted(False)
agent.is_companion_muted()

agent.pet_companion()
agent.get_companion_nurture_stats()
agent.get_companion_intro_attachment()

payload = agent.get_companion_render_payload(columns=120)
```

如果宿主希望监听 companion 生命周期：

```python
unsubscribe = agent.on_companion_event(lambda event: print(event))
...
unsubscribe()
```

#### companion render payload

`get_companion_render_payload(...)` 返回的是宿主友好的数据，不绑定 terminal UI 实现。典型可用于：

- 机器人表情层
- 前台 companion 面板
- TTS / 动作联动
- Web / App / terminal 多端共用展示态

payload 里通常包含：

- `enabled`
- `muted`
- `companion`
- `rendered`
- `reserved_columns`
- `reaction`
- `pet_until`

### 14. 工具池与能力边界控制

嵌入宿主通常希望显式控制能力边界：

```python
agent.set_tools([...])
agent.set_sub_agent_tools([...])
agent.set_tool_profiles(
    {
        "writer": ToolProfile(...),
    }
)
```

建议：

- `create_agent(..., use_default_tools=False)` 用于完全自定义宿主工具池
- `set_tools([...])` 用于运行时替换主工具池
- `set_sub_agent_tools([...])` / `set_tool_profiles({...})` 用于明确限制 worker 能力

### 15. 建议的宿主控制分层

如果宿主要把 ccmini 当成 unified brain，推荐把宿主控制面拆成四条线：

- **回合线**
  - `submit_user_input(...)`
  - `submit_tool_results(...)`
- **事件线**
  - `on_event(...)`
  - `publish_host_event(...)`
- **运行时线**
  - `set_mode(...)`
  - Kairos API
  - background task / team / peer API
- **表达线**
  - Buddy / Companion API
  - `CompletionEvent` / `TextEvent`
  - 前台 TTS / 动作 / 表情联动

## 推荐宿主架构

推荐让宿主自己维护三条线：

### 前台表达线

- 消费 `TextEvent / CompletionEvent`
- 做字幕、TTS、表情、动画

### 工具执行线

- 消费 `PendingToolCallEvent`
- 调真实语音 / 相机 / 动作 / 表情系统
- 用 `submit_tool_results(...)` 恢复

### 状态注入线

- 把传感器摘要、前台动作结果、系统事件转成 `HostEvent`
- 用 `publish_host_event(...)` 注入

这样 `ccmini` 能长期持有为“统一大脑”，而不是只作为 REPL / CLI 运行时。

## 兼容性说明

这套接口是增量提供的：

- 原有 `query()` / `submit()` / `on_event()` 仍可用
- 默认工具装配仍保持原样
- 默认路径仍保持原样
- 旧的 Kairos / Buddy 内部路径仍保留兼容

如果你在做新宿主，建议优先用这里的正式接口，而不是访问私有字段。

## 相关文件

- `agent.py`
- `engine/query_engine.py`
- `engine/query.py`
- `tool.py`
- `messages.py`
- `memory/store.py`
- `session/store.py`
- `delegation/coordinator.py`
- `kairos/__init__.py`
- `kairos/core.py`
- `kairos/sleep.py`
- `kairos/channels.py`
- `kairos/proactive.py`
- `buddy/__init__.py`
- `buddy/companion.py`
- `buddy/prompt.py`
- `factory.py`
