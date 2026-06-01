# Reachy Mini v4 大脑与动作架构

本文档用于锁定 Reachy Mini v4 agent runtime 的架构边界。

核心结论：

> `profiles/` 提供 app/profile/persona/tools/runtime config 产品边界；L3 Brain 使用 Claude Agent SDK (Python，也就是 Claude Code Python SDK，包名 `claude-agent-sdk`) 跑单一 main agent loop 并按需派 SDK worker/sub-agent；L2 Pipecat 统一实时事件流、barge-in、TTS/STT 和 frame routing；L1 Action Runtime 把动作意图解析成一等 `RobotAction`，完成锁仲裁后落到现有 Reachy Mini Python SDK。

## 锁版三层职责

这一版文档的 source of truth 是下面三层，后续设计和实现不能再换成其它抽象。

| 层级 | 目录 | 核心资产 | 负责 | 不负责 |
|---|---|---|---|---|
| L3 Brain | `src/reachy_mini/reachy_brain/` | Claude Agent SDK (Python) + `BrainAgent` | 决策：说什么、选择哪些动作、是否派 SDK worker/sub-agent。输出以 SDK 原生 message stream、SDK MCP tool call、SDK sub-agent message 为准。 | 不实现动作、不做电机时序、不调 `goto_target()` / `set_target()`、不重写 Claude Code 内部 coordinator。 |
| L2 Pipecat Pipeline | `src/reachy_mini/pipeline/` | `BrainProcessor`、`SpeechPresenter`、`ActionDispatcher` | `mic -> VAD -> FunASR -> ContextAggregator -> BrainProcessor`；把 SDK text 送 TTS，把 SDK action tool call 送 L1；处理 barge-in、streaming、routing、backpressure。 | 不重新定义 L3 任务协议，不把动作实现塞进 frame processor。 |
| L1 SDK / Action Runtime | `src/reachy_mini/action_runtime/` + 现有 SDK | `RobotAction`、`ActionRegistry`、`ActionExecutor`、`MotorLockManager` | 动作是一等对象；校验参数、申请锁、执行/取消/清理动作；调用现有 Reachy Mini Python SDK。 | 不做 LLM 决策，不读取 profile persona，不承载 worker 任务模型。 |

从 L3 视角，“做哪些动作”只表现为 SDK MCP action tool call 的动作名和参数；真正的 `RobotAction` 对象只在 L1 由 `ActionExecutor` 根据 `ActionRegistry` 实例化。LLM 只看动作元数据和工具 schema，看不到电机、插值、锁细节。

## 目标

- 保留 `profiles/<app>/` 作为 app、persona、tools、runtime config 的产品边界。
- 用 Claude Agent SDK (Python / Claude Code Python SDK) 提供的单一 Brain agent loop 替换当前 front/kernel 双 LLM 分层。
- 用 Pipecat 承担 VAD、turn detection、barge-in、streaming、STT、TTS 和 frame routing。
- 把机器人动作做成 SDK 侧一等对象，使动作可以脱离 LLM 独立运行。
- 保持底层 SDK 稳定：`reachy_mini.py`、`motion`、`kinematics`、`io`、`embodiment`、安全限位、media、tracking 不在 Phase 1 重写。

## 非目标

- 不把 Claude Code 内部代码翻译成 Python。
- 不把 worker/coordinator 任务模型改写成 Reachy 自研调度器；只通过 Claude Agent SDK (Python) 的公开 worker/sub-agent 能力复用。
- 不用 mock brain 替代 Claude Agent SDK (Python) 接入；mock 只能用于测试和离线 smoke。
- 不让 Brain 直接调用电机或 SDK 运动接口。
- 不把 LLM tool call 作为动作执行的唯一入口。
- 不删除 `profiles/`，也不把 profile/persona/tools/runtime config 挪到 L3 之外。
- 不在 Phase 1 重写现有 Reachy Mini SDK。
- 不在 Phase 1 修改现有 profile 文件格式。

## Claude Agent SDK (Python) 边界

L3 Brain 的 source of truth 是 Claude Agent SDK (Python)。它就是本文所说的 Claude Code Python SDK 接入点，不能被 mock、手写 wrapper 或翻译版 Claude Code TS 代码替代：

- Docs: `https://code.claude.com/docs/en/agent-sdk/python`
- GitHub: `https://github.com/anthropics/claude-agent-sdk-python`
- Package: `claude-agent-sdk`
- Import: `claude_agent_sdk`

v4 Brain 只能通过 SDK 公开接口接入 Claude Code：

- `ClaudeSDKClient` 承载主 agent loop、streaming、interrupt、session 和 tool call；
- `ClaudeAgentOptions` 承载 profile 映射后的 model、system prompt、cwd、tools、agents、permission、hooks；
- `tool()` + `create_sdk_mcp_server()` 把 `ActionRegistry` 暴露成同进程 SDK MCP tools；
- `AgentDefinition` 定义 worker/sub-agent。

如果环境里没有 `claude-agent-sdk`，v4 Brain 必须启动失败并提示安装命令，不能静默退回 mock。离线 smoke 和单元测试只能显式注入 `OfflineSDKClient` / fake SDK client，不能让 mock 成为默认 fallback。

## 三层模型

```text
┌─────────────────────────────────────────────────────────────────┐
│ L3 Brain                                                         │
│                                                                  │
│ profiles/<app>/ 保持产品边界                                     │
│     profiles/config.jsonl                                        │
│     profiles/AGENTS.md / SOUL.md / TOOLS.md / USER.md            │
│                                                                  │
│ AgentConfig 适配层只读 profiles                                  │
│                                                                  │
│ Claude Agent SDK (Python / Claude Code Python SDK)               │
│   package: claude-agent-sdk                                      │
│   import : claude_agent_sdk                                      │
│                                                                  │
│ ClaudeSDKClient                                                  │
│ - 接收 prompt / streaming input                                  │
│ - 输出 SDK Message stream                                        │
│ - 调用 SDK MCP tools                                             │
│ - 长任务交给 SDK background subagent                             │
│                                                                  │
│ AgentDefinition                                                  │
│ - 使用 Claude Agent SDK (Python) 原生 worker/sub-agent 定义       │
│ - 不在 Reachy 侧重写 SDK 字段协议                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SDK Message stream wrapped by Pipecat
┌──────────────────────────▼──────────────────────────────────────┐
│ L2 Pipecat Pipeline                                              │
│                                                                  │
│ mic -> VAD / turn detection -> FunASR -> ContextAggregator        │
│ camera -> VisionFrame                                            │
│ timer -> TickFrame                                               │
│                                                                  │
│ BrainProcessor                                                   │
│ - 把 brain runtime 包成 Pipecat FrameProcessor                    │
│ - 内部调用 Claude Agent SDK (Python)                             │
│ - 转发 SDK Message stream                                        │
│                                                                  │
│ SpeechPresenter -> Kokoro -> speaker                             │
│ ActionDispatcher -> ActionExecutor                               │
│                                                                  │
│ Pipecat 负责实时问题：barge-in、streaming、routing、backpressure、 │
│ turn detection 和 interruption。                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SDK MCP tool call / RobotAction
┌──────────────────────────▼──────────────────────────────────────┐
│ L1 SDK / Action Runtime                                          │
│                                                                  │
│ Action Runtime                                                   │
│ - RobotAction 是一等对象                                          │
│ - ActionRegistry 暴露动作元数据                                   │
│ - ActionExecutor 负责解析、调度、加锁、运行、取消                   │
│ - MotorLockManager 仲裁机器人资源                                 │
│                                                                  │
│ 现有 SDK 保持稳定                                                 │
│ - reachy_mini.py                                                  │
│ - motion / kinematics / io                                        │
│ - runtime/embodiment/coordinator.py 和 ExplicitMotionClaim        │
│ - safety limits                                                   │
│ - YOLO head tracker / antenna / IMU                               │
└──────────────────────────────────────────────────────────────────┘
```

## 关键边界

Brain 不执行底层电机动作，也不定义自己的 tool_call/worker 字段。动作入口是 Claude Agent SDK (Python) MCP tool；tool handler 进入 Action Runtime facade。

```text
Brain
  -> ClaudeSDKClient / SDK Message stream
  -> SDK MCP action tool
  -> ActionRuntime facade
  -> ActionRegistry.resolve()
  -> RobotAction
  -> ActionExecutor
  -> MotorLockManager
  -> ReachyMini SDK
```

因此动作执行可以被多种入口复用：

- LLM tool call
- 单元测试
- CLI 工具
- 键盘遥控
- 录像回放
- 舞蹈库
- 后续 UI 控件

## 核心数据契约

### ActionSpec

`ActionSpec` 是 Brain 和 Pipeline 边界上传递的可序列化动作意图。它是"我想做这个动作"的声明，**不携带 SDK 引用、不持有锁、也不能被执行**。`ActionExecutor` 在 SDK 侧把 `ActionSpec` 解析为 `RobotAction` 后才能落到电机。

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    request_id: str = ""
    owner_id: str = ""
    priority: int = 40
    interruptible: bool = True
    deadline_s: float | None = None
    parent_request_id: str | None = None
```

字段说明：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `name` | `str` | 是 | 已注册的 action 名称，必须命中 `ActionRegistry`。Brain 看到的 tool 名等于此值。 |
| `params` | `dict[str, Any]` | 否 | 动作参数。结构由该动作的 `ActionMetadata.parameter_schema` 定义，必须可 JSON 序列化（基本类型 + list/dict）。不允许嵌入回调、SDK 句柄、numpy 数组等不可序列化对象。 |
| `reason` | `str` | 否 | 自然语言原因，仅用于日志与可观测性。Executor 不读这个字段。 |
| `request_id` | `str` | 否 | 由 Brain 或 worker 生成的 ULID/UUID。一个 request 对应"一次决策产生的一组动作"，用于在日志、frame、lock lease 之间做关联。空串表示 Executor 自动生成。 |
| `owner_id` | `str` | 否 | 调用方身份。约定值：`main-agent`、`worker:<task_id>`、`manual-test`、`cli`、`mcp:<server>`。Lock manager 用它做归属和优先级裁决。 |
| `priority` | `int` | 否 | 0–100。落到 lock 仲裁时的优先级（见下方"优先级建议"表）。默认 40 = main agent 短动作。 |
| `interruptible` | `bool` | 否 | true 表示更高优先级请求可以抢占；false 表示一旦 run 就要跑完（紧急 stop 除外）。 |
| `deadline_s` | `float \| None` | 否 | 相对于入队时刻的超时秒数。超时后 Executor 调 `cancel()` 并释放锁。`None` 表示按 `RobotAction.duration_s` 推断或不设上限。 |
| `parent_request_id` | `str \| None` | 否 | 若该 spec 是 SDK task/subagent 派生的子动作，指向触发该任务的 request_id，用于 trace 串联。 |

规则：

- Brain 和 worker 只产出 `ActionSpec`，不产出 `RobotAction`。
- `ActionSpec` 必须可 JSON 序列化（用于跨进程传输、日志、回放）。
- `owner_id` 必须非空——空 owner 的 spec 在 dispatcher 侧直接拒绝。
- 未注册的 action name 在进入 SDK 前失败，错误以 `UnknownActionError` 抛出，不落电机。
- `params` 在 dispatcher 侧用 `ActionMetadata.parameter_schema` 校验，校验失败抛 `ActionParamError`，不落电机。

### RobotAction

`RobotAction` 是 SDK 侧可执行动作对象。它**只在 L1 层存在**，由 `ActionExecutor` 通过 `ActionRegistry` 实例化，绑定一个 `ActionContext` 后才能跑。Brain 永远看不到这个对象。

```python
from dataclasses import dataclass
from typing import Any, Protocol


class ActionContext(Protocol):
    mini: Any
    cancel_token: Any
    logger: Any
    request_id: str
    owner_id: str
    lease_handles: dict[str, Any]
    clock: Any


@dataclass
class RobotAction:
    name: str
    required_locks: set[str]
    duration_s: float | None
    priority: int
    interruptible: bool

    async def prepare(self, context: ActionContext) -> None:
        ...

    async def run(self, context: ActionContext) -> Any:
        ...

    async def cancel(self, context: ActionContext) -> None:
        ...

    async def cleanup(self, context: ActionContext) -> None:
        ...
```

`RobotAction` 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | `str` | 动作注册名，与 `ActionSpec.name` 对应。 |
| `required_locks` | `set[str]` | 该动作执行期间必须独占的资源集合，取自下方"Motor Lock 模型"表。Executor 在 `prepare` 之前一次性拿全，拿不全直接拒绝。空集合表示无锁动作（极少见，例如纯查询）。 |
| `duration_s` | `float \| None` | 期望执行秒数，用于 deadline 推断和 lease 续约。`None` 表示长动作或不可估，必须自己处理 cancel 信号。 |
| `priority` | `int` | 该动作在 lock 抢占时的基线优先级。如果 `ActionSpec.priority` 比它高，取 spec 的值；反之取 action 的值（防止 LLM 把低危动作伪装成高优先级）。 |
| `interruptible` | `bool` | 是否允许被更高优先级请求抢占。与 `ActionSpec.interruptible` 取 AND——只要任一侧为 false，就视为不可中断。 |

`ActionContext` 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `mini` | `ReachyMini` | SDK 主对象。所有电机/媒体调用必须经它，禁止直接访问 `mini.io` 内部。 |
| `cancel_token` | `CancelToken` | 异步取消信号。`run()` 内必须周期性 `await cancel_token.checkpoint()` 或在 `goto_target` 之间检查。被抢占时 Executor set 这个 token。 |
| `logger` | `logging.Logger` | 已绑定 `request_id`、`owner_id`、`action_name` 的结构化 logger。 |
| `request_id` | `str` | 来自 `ActionSpec.request_id`，用于 trace。 |
| `owner_id` | `str` | 来自 `ActionSpec.owner_id`。 |
| `lease_handles` | `dict[str, LockLease]` | 已持有的锁租约，按 lock name 索引。`run()` 不应该手动释放，Executor 在 `cleanup()` 后统一释放。 |
| `clock` | `Clock` | 注入的时钟，便于测试时 fake 时间。 |

生命周期方法：

| 方法 | 调用时机 | 失败处理 |
|---|---|---|
| `prepare(ctx)` | 锁拿到之后、`run()` 之前。校验参数和硬件状态，可读 SDK 但禁止下发电机指令。 | 抛异常 → 跳过 `run`，进入 `cleanup`，整体失败。 |
| `run(ctx)` | `prepare` 成功后。真正执行动作的地方，可下发电机指令、可 await TTS。 | 抛异常 → 进入 `cleanup`，向上抛 `ActionRunError`。 |
| `cancel(ctx)` | 收到抢占/超时/外部 stop 时，由 Executor 在另一个 task 中调用，与 `run` 并发。 | 实现要 idempotent；抛异常仅记日志，仍走 `cleanup`。 |
| `cleanup(ctx)` | 无论成功/失败/取消，Executor 必调一次。释放临时缓冲、停 idle motion、清 LED。 | 抛异常仅记日志，锁仍由 Executor 释放。 |

规则：

- `prepare()` 校验参数和硬件状态。
- `run()` 通过 SDK 执行动作。
- `cancel()` 处理可中断动作被抢占的情况。
- `cleanup()` 释放临时状态，即使失败也必须调用。

### SDK Native Message Stream

L3 不定义 Reachy 专属 Brain 输出结构或任务字段。`agent.py` 的输出就是 Claude Agent SDK (Python) 原生 message stream：

```python
from collections.abc import AsyncIterator
from typing import Any

async def run_turn(turn_input: BrainTurnInput) -> AsyncIterator[Any]:
    # yields SDK-native messages: AssistantMessage, ResultMessage,
    # TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage, etc.
    ...
```

L3 只认识 SDK 原生类型：

- `AssistantMessage` / `TextBlock`
- `ToolUseBlock` / `ToolResultBlock`
- `TaskStartedMessage` / `TaskProgressMessage` / `TaskNotificationMessage`
- `SubagentStartHookInput` / `SubagentStopHookInput`
- `ResultMessage` / `SystemMessage`

`BrainProcessor` 是 L2 wrapper，职责是把 SDK stream 接进 Pipecat pipeline。它可以为了 TTS、UI、日志生成 frame，但不能在 L3 重新发明一套 task/worker 字段协议。

## Frame 流

Frame 是 Pipecat pipeline 中的传输单元。所有 frame 都不可变（frozen dataclass），跨 processor 时按值传递，禁止携带可变共享状态。

入站 frame（外部输入 → BrainProcessor）：

| Frame | 来源 | 主要字段 | 用途 |
|---|---|---|---|
| `AudioFrame` | mic 采集 | `pcm: bytes`、`sample_rate: int`、`channels: int`、`ts_ms: int` | 喂给 VAD 和 STT。 |
| `SpeechActivityFrame` | VAD | `state: "start"\|"end"`、`ts_ms: int` | 触发 turn 边界、barge-in 检测。 |
| `TranscriptionFrame` | STT (FunASR) | `text: str`、`is_final: bool`、`turn_id: str`、`lang: str` | 喂给 ContextAggregator；非 final 用于流式预测。 |
| `VisionEventFrame` | 摄像头/tracker | `event: str`（如 `"face_detected"`）、`payload: dict`、`ts_ms: int` | 给 Brain 视觉上下文，例如 face id、bbox。 |
| `TickFrame` | 内部计时器 | `ts_ms: int`、`tick_id: int` | 周期性激活 idle motion、proactive behavior。 |
| `BrowserInputFrame` | Web UI | `kind: str`（如 `"text"`/`"button"`）、`payload: dict`、`session_id: str` | 用于 web 端文字输入或按钮事件。 |

出站 frame（BrainProcessor → 下游）：

| Frame | 字段 | 消费者 |
|---|---|---|
| `TextFrame` | 从 SDK `AssistantMessage` / `TextBlock` 得到的文本 | `SpeechPresenter` 与 UI 消费。 |
| `TTSAudioFrame` | `pcm: bytes`、`sample_rate: int`、`turn_id: str`、`is_final: bool` | speaker 播放；`is_final` 标记最后一个 chunk。 |
| `ActionResultFrame` | Action Runtime 执行结果 | 返回 SDK MCP `tool_result`，同时给日志/UI。 |
| `WorkerEventFrame` | SDK task/subagent message 的原始 payload + 最小 envelope | 供 pipeline/UI 观察；不重新定义 SDK worker 协议。 |

短任务标准流：

```text
mic
  -> VAD / turn detection
  -> FunASR TranscriptionFrame("hello")
  -> ContextAggregator
  -> BrainProcessor
  -> ClaudeSDKClient.receive_response()
  -> AssistantMessage/TextBlock -> SpeechPresenter -> Kokoro -> speaker
  -> SDK MCP action tool -> ActionDispatcher -> ActionExecutor -> SDK
```

长任务标准流：

```text
TranscriptionFrame("patrol the room")
  -> BrainProcessor
  -> ClaudeSDKClient starts background SDK subagent
  -> AssistantMessage/TextBlock -> SpeechPresenter
  -> SDK task/subagent action tool -> ActionDispatcher -> ActionExecutor
  -> SDK task/subagent messages -> WorkerEventFrame(raw SDK payload)
  -> main agent stays available for new input
```

## Motor Lock 模型

机器人资源不是文件路径，锁模型必须按机器人动作重新设计。锁的粒度有两层：**细粒度物理锁**（单关节）和**粗粒度组合锁**（语义资源），二者通过下方"包含关系"互斥。

锁名称表（lock name 全集）：

| Lock | 粒度 | 含义 | 与其它锁的包含关系 |
|---|---|---|---|
| `head` | 组合 | 整个 Stewart 平台 + head yaw | 持有 `head` 等于持有 `head_yaw` ∪ `head_pitch` ∪ `head_roll`，三者不可被其它 owner 同时持有。 |
| `head_yaw` | 单轴 | 仅头部 yaw 轴 | 与 `head` 互斥；与 `head_pitch`/`head_roll` 不互斥。 |
| `head_pitch` | 单轴 | 仅头部 pitch 轴 | 与 `head` 互斥。 |
| `head_roll` | 单轴 | 仅头部 roll 轴 | 与 `head` 互斥。 |
| `body_yaw` | 单轴 | 身体旋转 | 独立资源，但与 `head` yaw 共同决定 yaw delta（≤65°）。 |
| `antenna_left` | 单轴 | 左天线电机 | 独立。 |
| `antenna_right` | 单轴 | 右天线电机 | 独立。 |
| `speaker` | 设备 | 扬声器（TTS/sound） | 独立；用于避免多 source 同时出声。 |
| `camera_focus` | 软资源 | 摄像头主动跟踪目标 | 独立；多 reader 是否允许由策略决定。 |
| `full_body` | 全锁 | 一切电机 + speaker | 持有 `full_body` 排斥所有上述锁。仅用于安全 stop / 录制 / 校准。 |

Action → 锁的 canonical alias 归一：

```text
look_at       -> {"head"}
nod           -> {"head"}
shake_head    -> {"head"}
set_antenna   -> {"antenna_left", "antenna_right"}  # 单侧时只取一个
play_emotion  -> action-defined locks
stop_all      -> {"full_body", "speaker"}
```

`LockLease` 字段：

```python
@dataclass
class LockLease:
    lock_name: str
    owner_id: str
    action_id: str
    request_id: str
    priority: int
    interruptible: bool
    acquired_at: float
    expires_at: float | None
    renew_count: int
```

| 字段 | 含义 |
|---|---|
| `lock_name` | 见上表，必须命中。 |
| `owner_id` | 来自 `ActionSpec.owner_id`。同一 owner 的多个 action 不会自动共享锁，仍需独立申请。 |
| `action_id` | Executor 给本次执行分配的内部 ID（区别于 `request_id`，因为一个 request 可能产生多个 action 实例，例如重试）。 |
| `request_id` | 透传自 `ActionSpec.request_id`，用于跨日志关联。 |
| `priority` | 抢占判定的实际优先级，等于 `max(ActionSpec.priority, RobotAction.priority)`。 |
| `interruptible` | 与 `ActionSpec.interruptible` 和 `RobotAction.interruptible` 取 AND。 |
| `acquired_at` | 拿到锁的单调时钟时间（秒）。 |
| `expires_at` | 过期时刻；`None` 表示无显式过期，依赖 `RobotAction.duration_s` 推断。 |
| `renew_count` | 续约次数，超过阈值（默认 5）会触发"长动作未释放"告警。 |

仲裁规则：

1. 申请锁时，逐个检查 `required_locks` 与现有 lease 是否冲突（含包含关系）。
2. 无冲突 → 直接授予，记录 lease。
3. 有冲突 → 按以下顺序判定：
   - 现有 lease 的 `interruptible == False` → 拒绝（`LockBusyError`），除非新申请 `priority == 100`（safety stop）。
   - 现有 lease `priority < 新申请 priority` 且 `interruptible == True` → 抢占：`cancel_token` set，等 `cancel_grace_ms`（默认 300ms），强制释放并授予新 lease。
   - 现有 lease `priority >= 新申请 priority` → 拒绝。
4. 抢占触发时记录结构化日志：`{owner, request_id, action_id, lock, decision, preempted_by}`。

优先级建议：

| Priority | 用途 |
|---:|---|
| 100 | safety stop / emergency |
| 80 | 用户 barge-in / 显式 interrupt |
| 60 | 语音同步表情 |
| 40 | main agent 短动作 |
| 20 | worker 自主动作 |
| 10 | idle / proactive motion |

规则：

- 高优先级可以抢占低优先级且可中断的动作。
- 不可中断动作不能被抢占，安全停止除外。
- 每个 lease 必须有 owner。
- 长动作需要 lease 续约或有明确过期时间。
- 动作失败也必须在 `cleanup()` 中释放锁。

## Profiles 保持稳定

Phase 1 不要求修改 profile 文件。

```text
profiles/<name>/
  README.md
  plan.md
  <app_package>/main.py
  profiles/
    AGENTS.md
    SOUL.md
    TOOLS.md
    USER.md
    config.jsonl
    memory/
```

`brain/config.py` 读取现有 profile 数据并生成 `AgentConfig`。

```python
@dataclass(frozen=True)
class AgentConfig:
    model: ModelConfig
    speech: SpeechConfig
    speech_input: SpeechInputConfig
    vision: VisionConfig
    extras: dict[str, Any]
```

`AgentConfig` 顶层字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `model` | `ModelConfig` | Brain 主 LLM 配置；对应 profile 中 `kind: "model"` 行（兼容旧 `kernel_model`/`front_model`）。 |
| `speech` | `SpeechConfig` | TTS 输出配置。 |
| `speech_input` | `SpeechInputConfig` | STT 输入配置。 |
| `vision` | `VisionConfig` | 视觉输入与跟踪配置。 |
| `extras` | `dict[str, Any]` | profile 中未识别的 `kind` 行原样透传，用于实验性字段，不参与稳定 API。 |

`ModelConfig`（对应 profile 中 `kind: "model"` 行）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `provider` | `str` | LLM 提供方，约定值：`openai`、`anthropic`、`deepseek`、`ollama`、`mock`。 |
| `model` | `str` | 模型 ID，如 `deepseek-chat`、`claude-opus-4-7`。 |
| `base_url` | `str \| None` | 自定义 endpoint；`None` 表示用 SDK 默认。 |
| `api_key_ref` | `str` | API key，支持 `env:VAR_NAME` 引用环境变量、`vault:` 前缀引用 vault、或直接明文。 |
| `temperature` | `float` | 采样温度，建议 0–1。 |
| `max_tokens` | `int \| None` | 单次最大输出 token 数；`None` 让 provider 默认。 |
| `system_prompt_path` | `str \| None` | 相对 profile 根目录的系统提示文件，例如 `profiles/AGENTS.md`。 |
| `tool_call_mode` | `"native"\|"json"\|"none"` | 工具调用方式。`native` 走 provider 工具协议，`json` 走结构化 JSON 解析，`none` 不暴露工具。 |

`SpeechConfig`（对应 `speech` 行）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `enabled` | `bool` | 关闭时整条 TTS 链路被旁路，`SpeechPresenter` 直接丢弃 reply。 |
| `provider` | `str` | TTS 提供方，Phase 1 仅 `kokoro`。 |
| `model` | `str` | 模型 ID，例如 `hexgrad/Kokoro-82M-v1.1-zh`。 |
| `voice` | `str` | 默认音色（如 `zf_001`），可被 SpeechPresenter 的 profile/turn style 覆盖。 |
| `speed` | `float` | 默认语速 0.5–2.0。 |
| `sample_rate` | `int` | 输出采样率，默认与 provider 匹配。 |

`SpeechInputConfig`（对应 `speech_input` 行）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `enabled` | `bool` | 关闭时 STT 链路旁路，仅接受 `BrowserInputFrame` 文本输入。 |
| `provider` | `str` | Phase 1 仅 `funasr`。 |
| `base_url` | `str` | FunASR websocket，例如 `ws://127.0.0.1:10096`。 |
| `model` | `str` | FunASR 模式，如 `2pass`。 |
| `language` | `str` | ISO 语言码，如 `zh`、`en`。 |
| `playback_block_cooldown_ms` | `int` | TTS 放完后多久再开 STT，避免自循环。 |
| `stream_chunk_size` | `list[int]` | FunASR 流式 chunk 配置 `[encoder, current, lookahead]`。 |
| `stream_chunk_interval` | `int` | 流式 chunk 间隔毫秒。 |
| `stream_encoder_chunk_look_back` | `int` | 编码端回看 chunk 数。 |
| `stream_decoder_chunk_look_back` | `int` | 解码端回看 chunk 数。 |
| `stream_finish_timeout_s` | `float` | 流式结束等待秒数。 |
| `stream_itn` | `bool` | 是否启用逆文本规整（数字、日期归一）。 |

`VisionConfig`（对应 `vision` 行）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `no_camera` | `bool` | true 表示无摄像头 / 仿真，跳过相机进程。 |
| `head_tracker` | `str` | tracker 名，如 `yolo`、`mediapipe`、`none`。 |
| `local_vision` | `bool` | true 表示在 daemon 本机做推理；false 表示走网络流到上游。 |
| `frame_rate` | `int` | 期望帧率，仅作为 hint。 |
| `min_confidence` | `float` | 检测置信度阈值，低于不发 `VisionEventFrame`。 |

模型配置规则：

1. Profile 中使用 `{"kind": "model", ...}` 配置唯一的 LLM。
2. 兼容旧格式：`kernel_model` 或 `front_model` kind 仍可解析，kernel 优先。
3. Brain runtime 内部不暴露 `ProfileBundle`。
4. Brain 只读 profile，不写 profile。
5. 任何字段缺失时使用上方表格中的默认值，并在 logger 中打 `config_default_used` warning。

## 目录结构

```text
src/reachy_mini/
├── action_runtime/
│   ├── __init__.py
│   ├── action.py
│   ├── registry.py
│   ├── executor.py
│   ├── motor_lock.py
│   ├── metadata.py
│   ├── errors.py
│   └── library/
│       ├── __init__.py
│       ├── common.py
│       ├── look_at.py
│       ├── nod.py
│       ├── shake_head.py
│       ├── play_emotion.py
│       └── set_antenna.py
│
├── reachy_brain/
│   ├── __init__.py
│   ├── agent.py              # ClaudeSDKClient + ClaudeAgentOptions
│   ├── mcp_server.py         # ActionRegistry -> SDK MCP tools
│   ├── config.py             # profiles -> AgentConfig 适配
│   ├── pipecat_bridge.py     # SDK Message -> pipeline Frame 转换
│   ├── offline_sdk_client.py # 测试/smoke 用离线 SDK client
│   └── prompts/
│       ├── system.md         # 机器人人格 + system prompt
│       └── worker.md         # background worker prompt
│
├── pipeline/
│   ├── __init__.py
│   ├── session.py            # RuntimeSession：多轮编排 + barge-in
│   ├── runner.py             # CLI 入口（单轮 smoke）
│   ├── frames.py             # 所有 frame 定义（frozen dataclass）
│   ├── brain_processor.py    # BrainAgent -> SDKMessageFrame
│   ├── speech_presenter.py   # SDK text -> TTS chunk
│   ├── action_dispatcher.py  # ActionExecutor facade
│   ├── output_bus.py         # 多订阅者 fan-out
│   ├── wire.py               # Frame <-> JSON 序列化（WebSocket 传输）
│   ├── ws_app.py             # WebSocket 应用层
│   ├── live_io.py            # 麦克风/扬声器 I/O
│   ├── stt_funasr.py         # FunASR STT adapter
│   └── tts_kokoro.py         # Kokoro TTS adapter
│
├── runtime/    # profile loader、web host、embodiment coordinator
└── reachy_mini.py / motion / kinematics / io
```

测试使用仓库现有测试结构：

```text
tests/unit_tests/action_runtime/
tests/unit_tests/reachy_brain/
tests/unit_tests/pipeline/
```

## Adapter 边界

`ActionRegistry` 是可用动作的 source of truth。

各类 adapter 必须保持薄层：

```text
ActionRegistry
  -> Claude Agent SDK (Python) tools adapter
  -> MCP adapter
  -> CLI/test adapter
  -> Pipecat ActionDispatcher
```

MCP 只是动作暴露方式之一，不是 Action Runtime 的核心。

## 迁移策略

Phase 1 只新增 v4 runtime path，不破坏旧路径。

保留：

- `profiles/`
- `core/`
- `front/`
- `runtime/scheduler.py`
- 现有 app templates
- 现有 SDK modules

新增：

- `action_runtime/`
- `reachy_brain/`
- `pipeline/`
- 显式 opt-in 的 v4 entrypoint

Phase 2 才把默认 runtime 切到 v4，并在 smoke case 通过后删除旧层。

## Smoke Case

每个 smoke case 必须给出可量化的输入、输出、字段断言和判定字段。

### SC-1：短任务

| 项 | 值 |
|---|---|
| 输入 | `TranscriptionFrame(text="你好", is_final=True)` |
| 期望出站 | SDK `AssistantMessage` 含非空 `TextBlock`；如需动作，通过 SDK MCP action tool 调用 |
| 期望 ActionResultFrame | `status="ok"`，`name="nod"`，`duration_ms <= 1500` |
| 端到端断言 | `t(TranscriptionFrame.is_final=True)` 到 `t(TTSAudioFrame.is_final=True)` 差值 ≤ 2000 ms（mock STT/TTS 环境） |
| 失败信号 | reply_text 为空 / actions 为空 / 超时 / Lock 冲突 |

### SC-2：长任务 SDK worker/sub-agent

| 项 | 值 |
|---|---|
| 输入 1 | `TranscriptionFrame(text="巡视一圈")` |
| 期望事件序列 | Claude Agent SDK (Python) 产生 `TaskStartedMessage` / subagent start hook → `WorkerEventFrame(event="started")` → 多个 SDK MCP action tool calls |
| 输入 2（5 秒后） | `TranscriptionFrame(text="现在几点")` |
| 期望并发性断言 | 输入 2 的 SDK `AssistantMessage` 必须在 SDK worker 仍发出 `WorkerEventFrame(event="progress")` 的窗口内产生 |
| 终止断言 | `WorkerEventFrame(event="completed")` 来自 SDK completion message，含 `status` 与非空 summary |

### SC-3：Barge-In

| 项 | 值 |
|---|---|
| 前置 | TTS 正在播放，`SpeechPresenter` 已发出至少一个 `TTSAudioFrame(is_final=False)` |
| 触发 | `SpeechActivityFrame(state="start")` 后跟 `TranscriptionFrame(text="等等")` |
| 时延断言 | 从 barge-in `SpeechActivityFrame` 到 `TTSAudioFrame` 流停止 ≤ 300 ms |
| 锁断言 | 当前持锁 action 若 `interruptible=True` → `cancel_token` 在 300 ms 内 set 并 `cleanup()` 执行；若 `interruptible=False` → 不取消，记录 `barge_in_blocked_by_uninterruptible` 日志 |
| 后续 | 新 turn 的 SDK session/turn id 与被打断 turn 不同 |

### SC-4：Motor lock 冲突

| 项 | 值 |
|---|---|
| 前置 | worker 已持 `head` 锁执行 `look_at`，`priority=20`，`interruptible=True` |
| 触发 | main agent 提交 `ActionSpec(name="nod", priority=40)` |
| 期望决策 | Lock manager 抢占：worker action 收到 cancel，nod 在 300 ms 内拿到锁 |
| 反向用例 | 若 worker action `interruptible=False` 且 priority=20 → main agent 的 nod 被拒，返回 `LockBusyError`，`ActionResultFrame.status="error"` |
| 日志断言 | 出现一条结构化日志，键含 `decision in {"granted","preempted","rejected"}`、`owner`、`request_id`、`lock_name="head"` |
| SDK 断言 | 同一 100 ms 窗口内 `mini.head.goto_target` / `set_target` 的调用方只有一个 owner_id |

四个 smoke case 全部通过，Phase 1 才算完成。
