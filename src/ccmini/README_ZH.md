# ccmini

`ccmini` 是从当前 `mini_agent` 中抽出来的一套共用内核。

它不是“只给机器人用”的裁剪版，也不是“只给命令行用”的编程助手版，而是：

- 可以当 UI 背后的 agent 内核
- 可以当机器人大脑 / runtime brain
- 可以当 bridge / remote executor 背后的共享执行核

它**不是**独立命令行产品入口。

- 不提供 `python -m ccmini` 这种 CLI 用法
- 推荐入口是：
  - `ccmini.factory`
  - `ccmini.bridge.host`
  - `ccmini/frontend`

当前设计原则是：
- 保留共用内核能力
- 用 profile 区分默认行为
- 用工厂接口简化不同宿主的创建方式

## 当前内核包含什么

核心主链：
- `Agent`
- `engine/query.py`
- `engine/query_engine.py`
- `providers/*`
- `messages.py`
- `tool.py`
- `permissions.py`
- `prompts.py`

协调与后台能力：
- `AgentTool`
- `BackgroundAgentRunner`
- `SendMessage`
- `TaskStop`
- `TeamCreate`
- persistent teammate
- `ListPeers`
- `bridge`

也就是说，当前 `ccmini` 已经具备：
- 普通 query 主链
- 协调者主链
- 后台 worker
- persistent teammate / team
- 多会话 / 跨会话扩展基础

## 记忆系统（双栈并行）

设计意图是 **两条记忆管线同时存在、互补**，而不是二选一。

**栈 A — Claude Code 对齐（文件型、偏工程协作）**

- **Session memory**：本会话结构化 Markdown 笔记，后台 fork 按 token / 工具调用阈值更新，与 compact 配合；实现见 `services/session_memory.py`。
- **Memdir / Extract memories**：项目下持久 `.md` 记忆（类型如 user / feedback / project / reference），侧查询选 relevant 再注入；实现见 `services/memdir.py`、`services/extract_memories.py`。
- **Relevant memory prefetch**：主请求前预读会话记忆（可选，环境变量 `MINI_AGENT_RELEVANT_MEMORY_PREFETCH`）；见 `services/relevant_memory_prefetch.py`。

**栈 B — Reachy / Letta 风格 JSONL（结构化长期层）**

- **`JsonlMemoryStore`**：三层存储（会话原始流 → cognitive → long-term），与既有 JSONL 格式兼容；见 `memory/store.py`。
- **`MemoryAdapter` + `ConsolidationAgent`**：把轮次写入各层、做长期摘要与候选记忆；注入路径含 `USER.md`、system 内 Memory 段、检索附件、可选 CoreMemory 块；见 `memory/adapter.py`、`memory/consolidation.py`。

两条栈在 `Agent._install_memory_runtime()` 中一并挂载（SessionMemory / ExtractMemories / AutoDream 等 hook + `MemoryAttachmentSource`）。若初始化失败会静默降级，仅打 debug 日志。

## Pip / 第三方包扩展（setuptools entry points）

与目录型插件（`.ccmini/plugins` / `plugin.json`）并列，支持 **任意 pip 安装的 wheel** 通过标准入口点注册工具与 hook：

| 组名 | 含义 |
|------|------|
| `ccmini.tools` | 可调用对象，返回单个 `Tool` 或 `list[Tool]` |
| `ccmini.hooks` | 可调用对象，返回单个 `Hook` 或 `list[Hook]` |

加载逻辑见 `distribution_plugins.py`；`Agent` 在 `_install_plugin_runtime()` 里于目录插件之后合并 entry point 结果。配置项 `AgentConfig.enable_distribution_entry_points`（默认 `True`）可关闭，用于不可信环境。

第三方扩展只要 **装进同一 Python 环境** 并在自己的分发里声明上述 entry points 即可被加载；不必把插件源码放进 ccmini 仓库。

## 扩展性怎么接（宿主 / 机器人侧）

你要的「扩展性强」= **不改 ccmini 内核**，用下面机制接能力：

| 要扩展的 | 怎么做 |
|----------|--------|
| **工具** | ① `create_agent(..., tools=[你的 Tool...])` 完全自定义列表；② 或依赖装了 `ccmini.tools` entry point 的包；③ 或项目/全局 `.ccmini/plugins`（`PluginRegistry`） |
| **生命周期** | `create_agent(..., hooks=[...])` 传入 `PreQueryHook` / `PostSamplingHook` / `IdleHook` 等；或 `ccmini.hooks` entry point |
| **上下文注入** | 构造 `AttachmentCollector`，实现自己的 `AttachmentSource`，`create_agent(..., attachment_collector=...)` |
| **默认行为** | `profile`（`coding_assistant` / `robot_brain`）+ `AgentConfig`（如 `enable_distribution_entry_points=False` 关闭全局 entry point，仅信自己传的 hooks/tools） |
| **记忆** | 双栈已内置；若要换实现，需在 fork 层替换 `MemoryAttachmentSource` / 自定义 hook（当前未暴露单一 `MemoryBackend` 接口，属于进阶） |

机器人代码里典型写法：**只调 `create_agent` / `create_robot_agent`，把电机、相机等封成 `Tool`，用 `hooks` 接周期任务**，内核保持不动。

## 机器人场景下的 Hook（选型）

所有协议定义在 **`ccmini/hooks/__init__.py`**，通过 **`create_agent(..., hooks=[...])`** 传入（也可用 **`ccmini.hooks` entry point** 从扩展包装载）。与「写电机驱动」无关的横切逻辑，优先放 Hook，少改 `QueryEngine`。

| Hook 类型 | 机器人上典型用途 |
|-----------|------------------|
| **`IdleHook`** | **常驻 + `start()` 后**才跑：空闲时周期性拉 **电池、急停状态、模式开关** 摘要；可返回 **`IdleAction`** 触发内置动作（见 `agent._execute_idle_action`）。**不要**在回调里做重计算或阻塞 I/O，必要时 `asyncio.to_thread` 或丢给下层硬件线程再通过队列回传。 |
| **`PreQueryHook`** | 每一轮用户/外部 `submit` 进模型 **之前**：把 **当前位姿、安全区、运行模式** 注入 `messages` 或副作用写上下文（只读传感器快照适合这里）。 |
| **`PostQueryHook`** | 一轮对话结束：记日志、打点、把「本轮结论」写给宿主状态机。 |
| **`PreToolUseHook`** | **安全闸**：模型要调 `goto` / `bash` / 任意危险工具前，**拦截、改参数、拒绝**（`PreToolUseResult.DENY`）。机器人上强烈建议对运动类 tool 加 matcher。 |
| **`PostToolUseHook`** | 工具执行后审计、把结果同步到物理仿真/真机状态缓存。 |
| **`SessionStartHook` / `SessionEndHook`** | 会话开始/结束：申请摄像头、释放资源、与 daemon 握手。 |
| **`NotificationHook`** | 内核产生的 **非交互通知**（警告、错误）统一转发到你的 **灯带 / 蜂鸣器 / 话题**。 |
| **`OnStreamEventHook`** | 流式输出侧过滤、脱敏、改写字面再交给 UI。 |
| **`StopHook`** | 自定义「这一 turn 必须停」的条件（例如收到硬件急停）。 |
| **`PostSamplingHook`** | 与 **Session memory / extract memories** 同类：模型吐完后的旁路（慎用，避免和内置 hook 重复做同一件事）。 |

**和「工具」的分工：** 一次性的 **动作**（举爪、抓）用 **`Tool`**；跨多轮、与模型调用时机绑定的 **策略**（安全、注入、统计）用 **Hook**。`IdleHook` 解决的是 **「没有 query 时也要定期看世界」**，和传感器线程里 `submit` 事件是两条线，可并存。

## 常驻（resident）模式

「扩展」之外，机器人还要 **进程级常驻**：只 `create_agent` 不够，必须显式进入生命周期，否则没有 idle 循环、Kairos 常驻、session start 钩子等。

| 步骤 | 说明 |
|------|------|
| **启动** | `await agent.start()`，或使用 `async with agent:`（进入时 `start`，退出时 `stop`） |
| **常驻里会跑什么** | `HookRunner` 的 session start；若启用 KAIROS 则 `_start_kairos_runtime`；若存在 **`IdleHook`**，则后台 `_idle_loop` 按最短 `interval` 周期性触发（仅当当前不在处理用户 query） |
| **后台任务** | `BackgroundAgentRunner`（工具里的 background / 定时任务等）依赖事件循环与 `start()` 后的状态；调度任务在 idle 循环里 `spawn` |
| **收尾** | 进程或场景结束前 **`await agent.stop()`**，取消 idle 任务、跑 session end、关 KAIROS 等 |

宿主侧（如 `reachy_mini` runtime）要保证：**同一 asyncio 事件循环里长期持有 `Agent`，并调用 `start`/`stop`**。若只做「来一条消息调一次 query、从不 start」，则没有真正意义上的常驻行为。

## 从外部通知 / 投喂 Agent（不只是「调一次 query」）

常驻机器人通常还有 **第二条线**：传感器线程、手机 App、另一个进程、定时器——要把「事件」送进当前会话。ccmini 里有多条通路，复杂度和可靠性不同：

| 通路 | 适用场景 | 机制（代码入口） |
|------|----------|------------------|
| **同进程：阻塞流** | UI / 简单宿主 | `async for ev in agent.query("用户话")` — 一次一轮对话，流式事件 |
| **同进程：非阻塞** | **控制循环里不能卡死**（走路、避障同时说话） | `agent.submit("话")` 立即返回，用 `poll_event()` / `wait_event()` / `drain_events()` 或 `on_event` 回调拉流；见 `agent.py` 文档串 |
| **跨进程 / 多会话** | 另一个进程要「给这个 session 塞一条」 | **`FileMailbox`**（`delegation/mailbox.py`）：`~/.mini_agent/session_mailboxes/` 等路径；下一轮 `query()` / `submit_tool_results` 开头会 **`_ingest_session_mailbox_messages`**，把未读变成模型可见的提示 |
| **集群 / 队友** | 多 Agent、swarm | **Team mailbox** + `SendMessage` 工具（`tools/send_message.py`），leader/teammate 目录下 JSON |
| **KAIROS：命令队列** | 异步事件要进「下一轮推理」 | `kairos` 里 **`QueuedCommand` + `get_command_queue()`**，channel/cron 等 `enqueue`，主循环在适当时机 **drain** 进上下文（与 `query` 路径配合） |
| **KAIROS：Channel** | 外部系统模拟「频道消息」 | `kairos/channels.py` **`ChannelRegistry.handle_notification`** → 入队 → 模型侧以 `<channel>` 等形式注入 |
| **KAIROS：定时** | cron 到期唤醒 | `start()` 后若启用，**cron scheduler** 可向 command queue 塞命令，idle 侧配合 `TaskScheduler` |
| **收件箱快照（偏 UI）** | 手机/Shell 推一条、文件投递记录 | **`kairos/inbox`** JSONL + HTTP `GET /api/kairos/inbox`（Bridge 上）；模型侧可用工具 **`PushNotification` 等** 写入 inbox，**不是**自动进主 transcript，除非你再写一条进 queue/mailbox |
| **Bridge / Remote** | 远端 REPL、另一台机器 | **`bridge/`**：WebSocket + HTTP，宿主在机器人上跑 BridgeServer，外部 UI 连上来发消息（等价于远程 `query`） |
| **Hook：Notification** | 宿主想统一拦截「要显示给用户」的流 | 实现 **`NotificationHook`**（见 `hooks/__init__.py`），在流事件路径上接一层 |

**设计上的分工：**

- **「马上让大脑推理一句」** → 同进程 **`query` / `submit`** 最直。
- **「另一个进程没有 Agent 引用」** → **session `FileMailbox`** 或 **Kairos queue**（需你保证消费者在同一次 `start` 后的 Agent 上跑）。
- **「只提醒人类、不进本轮上下文」** → 桌面 **`services/notifier`**、或 **inbox HTTP**；若也要模型知道，必须再显式写入 queue/mailbox 或发一条 `submit`。

机器人宿主若要「扩展性 + 通知」都稳：**在 runtime 里持有一个 `Agent` + `start()`**，对外暴露你自己的 API（ROS topic、ZMQ、HTTP），在回调里 **`submit(user_text)`** 或写 **mailbox**；不要只依赖 idle 轮询。

### 传感器 / ROS / C 回调在别的线程时

`Agent` 只应在 **绑定它的 asyncio 事件循环**上驱动。工作线程里收到事件后，用 **`loop.call_soon_threadsafe(lambda: agent.submit(...))`** 或 **`asyncio.run_coroutine_threadsafe`** 把调用扔回主循环，**不要**在线程里直接 `async for agent.query(...)`。高频数据先 **降采样 / 队列合并** 再 `submit`。

## 两种默认 profile

### `coding_assistant`

适合：
- UI 编程助手
- 调试、改代码、审查、协作

默认特点：
- 开启内置命令层
- 开启 bundled skills
- 主 Agent 保留完整 host 工具池
- `AgentTool` 派生出来的 worker 仍然使用受限工具子集

### `robot_brain`

适合：
- 机器人运行时
- 具身 agent
- 后台认知 + 前台外显

默认特点：
- 不自动加载 CLI 命令层
- 不自动加载 bundled skills
- 仍使用同一套 core runtime
- 是否暴露哪些工具，由宿主决定

## 推荐创建方式

### 通用工厂

```python
from ccmini import create_agent
from ccmini.providers import ProviderConfig

agent = create_agent(
    provider=ProviderConfig(
        type="openai",
        model="gpt-5.4",
        api_key="...",
        base_url="https://ai.hhhl.cc/v1",
    ),
    system_prompt="You are a helpful assistant.",
    profile="robot_brain",
)
```

### 快捷工厂

```python
from ccmini import create_coding_agent, create_robot_agent
from ccmini.providers import ProviderConfig

coding_agent = create_coding_agent(
    provider=ProviderConfig(
        type="openai",
        model="gpt-5.4",
        api_key="...",
        base_url="https://ai.hhhl.cc/v1",
    ),
    system_prompt="You are a coding assistant.",
)

robot_agent = create_robot_agent(
    provider=ProviderConfig(
        type="openai",
        model="gpt-5.4",
        api_key="...",
        base_url="https://ai.hhhl.cc/v1",
    ),
    system_prompt="You are a robot brain.",
)
```

## 什么时候自己传 tools

默认情况下，`create_agent(...)` 会按 profile 自动装配默认工具。

如果你明确传了 `tools=[...]`：
- 就会覆盖默认工具装配
- 适合宿主自己完全控制能力边界

示例：

```python
from ccmini import create_agent
from ccmini.providers import ProviderConfig
from ccmini.delegation.multi_agent import AgentTool

provider = ProviderConfig(type="openai", model="gpt-5.4", api_key="...")

agent = create_agent(
    provider=provider,
    system_prompt="You are a custom host agent.",
    profile="robot_brain",
    tools=[],
)
```

## 现在更推荐怎么理解 ccmini

可以把 `ccmini` 理解成：

- 一套共享 agent 内核
- 一套共享协调者/后台执行内核
- 一套给 bridge / frontend / robot runtime 共用的执行核心
- 两种默认宿主模式

而不是：
- 一套独立 CLI
- 一套只能做编程助手的壳
- 或一套只能做机器人大脑的特化包

## 和根 `mini_agent` 的关系

当前更推荐这样理解：

- 根 `mini_agent`：历史工作区、对照修复面、兼容宿主层
- `ccmini`：已经抽出的共享 core/runtime

也就是说：

- 需要继续做核心能力、bridge、frontend、robot runtime，优先落在 `ccmini`
- 根 `mini_agent` 更多保留为迁移源、兼容层、历史宿主代码面

## 相关文件

最常用入口：
- `ccmini/__init__.py`
- `ccmini/factory.py`
- `ccmini/profiles.py`
- `ccmini/tools/__init__.py`
- `ccmini/agent.py`（`_install_memory_runtime` 挂载双栈记忆）

宿主嵌入接口说明（统一大脑 / Kairos / Buddy）：
- `ccmini/CCMINI_EMBEDDABLE_BRAIN_SDK_ZH.md`

profile 说明：
- `ccmini/CCMINI_PROFILES_ZH.md`

机器人 tool 接入说明：
- `ccmini/CCMINI_ROBOT_TOOL_INTEGRATION_ZH.md`
