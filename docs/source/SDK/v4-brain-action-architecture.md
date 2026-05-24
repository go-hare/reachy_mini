# Reachy Mini v4 大脑与动作架构

本文档用于锁定 Reachy Mini v4 agent runtime 的架构边界。

核心结论：

> `profiles/` 提供 app/profile 配置；Pipecat 统一实时事件流；Brain 使用单一 agent loop 做决策并按需派 worker；Action Runtime 把动作意图解析成一等机器人动作，完成锁仲裁后落到现有 Reachy Mini Python SDK。

## 目标

- 保留 `profiles/<app>/` 作为 app、persona、tools、runtime config 的产品边界。
- 用单一 Brain agent loop 替换当前 front/kernel 双 LLM 分层。
- 用 Pipecat 承担 VAD、turn detection、barge-in、streaming、STT、TTS 和 frame routing。
- 把机器人动作做成 SDK 侧一等对象，使动作可以脱离 LLM 独立运行。
- 保持底层 SDK 稳定：`reachy_mini.py`、`motion`、`kinematics`、`io`、`embodiment`、安全限位、media、tracking 不在 Phase 1 重写。

## 非目标

- 不把 Claude Code 内部代码翻译成 Python。
- 不让 Brain 直接调用电机或 SDK 运动接口。
- 不把 LLM tool call 作为动作执行的唯一入口。
- 不在 Phase 1 重写现有 Reachy Mini SDK。
- 不在 Phase 1 修改现有 profile 文件格式。

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
│ Main Agent Loop                                                  │
│ - 消费 frame                                                     │
│ - 决定 reply_text                                                │
│ - 产出 ActionSpec[]                                              │
│ - 长任务 spawn worker                                            │
│                                                                  │
│ Workers                                                          │
│ - 异步执行长任务                                                  │
│ - 回传 task result                                               │
│ - 只产出 ActionSpec 意图，不直接调用 SDK                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ frames
┌──────────────────────────▼──────────────────────────────────────┐
│ L2 Pipecat Pipeline                                              │
│                                                                  │
│ mic -> VAD / turn detection -> FunASR -> ContextAggregator        │
│ camera -> VisionFrame                                            │
│ timer -> TickFrame                                               │
│                                                                  │
│ BrainProcessor                                                   │
│ - 把 brain runtime 包成 Pipecat FrameProcessor                    │
│ - 内部调用 Claude Agent SDK / model runtime                      │
│ - 输出 BrainReplyFrame                                           │
│                                                                  │
│ SpeechPresenter -> Kokoro -> speaker                             │
│ ActionDispatcher -> ActionExecutor                               │
│                                                                  │
│ Pipecat 负责实时问题：barge-in、streaming、routing、backpressure、 │
│ turn detection 和 interruption。                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ ActionSpec / RobotAction
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

Brain 不执行动作，只输出意图。

```text
Brain
  -> BrainReplyFrame { reply_text, speech_style, actions[] }
  -> ActionSpec { name, params, reason }
  -> ActionDispatcher
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
| `parent_request_id` | `str \| None` | 否 | 若该 spec 是 worker 派生的子动作，指向 spawn worker 的那个 request_id，用于 trace 串联。 |

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

### BrainReplyFrame

`BrainReplyFrame` 是 `BrainProcessor` 的主要输出，表示"Brain 对一个 turn 的完整决策"。它从 L3 流向 L2，由 `SpeechPresenter` 和 `ActionDispatcher` 分别消费 reply 与 actions。

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrainReplyFrame:
    reply_text: str
    speech_style: dict[str, Any] = field(default_factory=dict)
    actions: list[ActionSpec] = field(default_factory=list)
    worker_decision: dict[str, Any] | None = None
    turn_id: str = ""
    request_id: str = ""
    is_final: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
```

字段说明：

| 字段 | 类型 | 含义 |
|---|---|---|
| `reply_text` | `str` | 给 TTS 的文本内容。空串表示该 turn 不说话（仅做动作或仅 spawn worker）。允许包含 SSML/标点提示，但不应包含模型 chain-of-thought。 |
| `speech_style` | `dict[str, Any]` | TTS 风格参数。约定 keys：`voice`（覆盖 profile 默认 voice）、`speed`（0.5–2.0）、`emotion`（"neutral"/"happy"/"sad"/...）、`pause_after_ms`。未识别的 key 由 `SpeechPresenter` 忽略并打 warning。 |
| `actions` | `list[ActionSpec]` | 本 turn 要执行的动作列表，按列表顺序进入 dispatcher。空列表表示无动作。多个 action 是否并行由各自的 `required_locks` 决定，不由列表顺序决定。 |
| `worker_decision` | `dict \| None` | 长任务决策。`None` 表示不动 worker。结构见下方"worker_decision 结构"。 |
| `turn_id` | `str` | 对话 turn 的全局 ID，由 ContextAggregator 在收到 user input 时生成，整个 turn 内所有 frame 共享同一 ID。 |
| `request_id` | `str` | 该 reply 对应的 brain decision request id。下游 ActionSpec 的 `request_id` 默认继承这个值。 |
| `is_final` | `bool` | true 表示该 turn 已结束，可以发 TTS。false 表示这是流式中间帧（Phase 1 暂不启用流式，保留字段）。 |
| `metadata` | `dict[str, Any]` | 调试用透传字段（model name、token usage、latency 分段）。Executor 不读这个字段。 |

`worker_decision` 结构：

```python
{
    "op": "spawn" | "update" | "cancel",
    "task_id": str,
    "task_type": str,        # 注册过的 worker 类型，如 "patrol"
    "task_params": dict,     # 透传给 worker 的初始化参数
    "summary": str,          # 给用户/日志的可读说明
}
```

| 字段 | 含义 |
|---|---|
| `op` | `spawn` 创建新 worker；`update` 给已有 worker 喂新参数；`cancel` 终止 worker。 |
| `task_id` | worker 的全局唯一 ID，`spawn` 时由 Brain 生成；`update`/`cancel` 时引用已有 ID。 |
| `task_type` | 必须命中 worker registry，未注册类型直接拒绝。 |
| `task_params` | 仅 `op=spawn` 或 `update` 时使用，必须 JSON 可序列化。 |
| `summary` | 仅日志用，不影响行为。 |

规则：

- `reply_text` 进入 `SpeechPresenter`。
- `actions` 进入 `ActionDispatcher`。
- `worker_decision` 创建或更新长任务 worker。
- 一个 frame 三段（reply / actions / worker）可以任意组合，但至少要有一段非空，否则视为空 turn 并丢弃。

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
| `BrainReplyFrame` | 见上节 | `SpeechPresenter` 取 reply_text 与 speech_style；`ActionDispatcher` 取 actions；`WorkerCoordinator` 取 worker_decision。 |
| `TextFrame` | `text: str`、`turn_id: str` | 调试/日志/UI 透传，不送 TTS。 |
| `TTSAudioFrame` | `pcm: bytes`、`sample_rate: int`、`turn_id: str`、`is_final: bool` | speaker 播放；`is_final` 标记最后一个 chunk。 |
| `ActionSpecFrame` | `spec: ActionSpec`、`turn_id: str` | 进入 `ActionDispatcher`。 |
| `ActionResultFrame` | `request_id: str`、`name: str`、`status: "ok"\|"cancelled"\|"error"`、`error: str\|None`、`duration_ms: int` | 反馈给 Brain memory，便于下一轮决策。 |
| `WorkerEventFrame` | `task_id: str`、`event: str`（`"started"`/`"progress"`/`"completed"`/`"failed"`）、`payload: dict`、`ts_ms: int` | Brain coordinator 跟踪 worker 状态。 |

短任务标准流：

```text
mic
  -> VAD / turn detection
  -> FunASR TranscriptionFrame("hello")
  -> ContextAggregator
  -> BrainProcessor
  -> BrainReplyFrame(reply_text="Hi", actions=[ActionSpec("nod")])
  -> SpeechPresenter -> Kokoro -> speaker
  -> ActionDispatcher -> ActionExecutor -> SDK
```

长任务标准流：

```text
TranscriptionFrame("patrol the room")
  -> BrainProcessor
  -> worker_decision(spawn patrol worker)
  -> BrainReplyFrame(reply_text="I will start patrolling.")
  -> worker emits ActionSpec("scan_room")
  -> ActionDispatcher -> ActionExecutor
  -> worker writes task progress to MemoryStore
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
| `model` | `ModelConfig` | Brain 主 LLM 配置；按"模型合并规则"从 `kernel_model`/`front_model` 推导。 |
| `speech` | `SpeechConfig` | TTS 输出配置。 |
| `speech_input` | `SpeechInputConfig` | STT 输入配置。 |
| `vision` | `VisionConfig` | 视觉输入与跟踪配置。 |
| `extras` | `dict[str, Any]` | profile 中未识别的 `kind` 行原样透传，用于实验性字段，不参与稳定 API。 |

`ModelConfig`（对应 profile 中 `kernel_model`/`front_model` 行）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `provider` | `str` | LLM 提供方，约定值：`openai`、`anthropic`、`deepseek`、`ollama`、`mock`。 |
| `model` | `str` | 模型 ID，如 `deepseek-chat`、`claude-opus-4-7`。 |
| `base_url` | `str \| None` | 自定义 endpoint；`None` 表示用 SDK 默认。 |
| `api_key_ref` | `str` | API key 的环境变量名或 vault 引用（如 `env:DEEPSEEK_API_KEY`）。**不接受明文 key**——Phase 1 起 profile 必须用引用。 |
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
| `voice` | `str` | 默认音色（如 `zf_001`），可被 `BrainReplyFrame.speech_style.voice` 覆盖。 |
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

模型合并规则：

1. 优先使用 `kernel_model`。
2. 没有 `kernel_model` 时回退到 `front_model`。
3. Brain runtime 内部不暴露 `ProfileBundle`。
4. Brain 只读 profile，不写 profile。
5. 任何字段缺失时使用上方表格中的默认值，并在 logger 中打 `config_default_used` warning。

## 建议目录结构

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
│       ├── look_at.py
│       ├── nod.py
│       ├── shake_head.py
│       ├── play_emotion.py
│       ├── play_dance.py
│       ├── set_antenna.py
│       ├── scan_room.py
│       └── face_track.py
│
├── reachy_brain/
│   ├── __init__.py
│   ├── agent.py
│   ├── config.py
│   ├── worker.py
│   ├── coordinator.py
│   ├── memory.py
│   ├── tool_adapter.py
│   ├── pipecat_bridge.py
│   ├── mcp_server.py
│   └── prompts/
│       ├── system.md
│       └── worker.md
│
├── pipeline/
│   ├── __init__.py
│   ├── runner.py
│   ├── frames.py
│   ├── brain_processor.py
│   ├── speech_presenter.py
│   ├── action_dispatcher.py
│   ├── stt_funasr.py
│   ├── tts_kokoro.py
│   ├── vision_frame.py
│   └── tick_frame.py
│
├── core/       # Phase 1 保留，Phase 2 退役
├── front/      # Phase 1 保留，Phase 2 用 SpeechPresenter 替换
├── runtime/    # Phase 1 保留旧入口，Phase 2 退役 scheduler
└── reachy_mini.py / motion / kinematics / io / embodiment
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
  -> Claude Agent SDK tools adapter
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
| 期望出站 frame | `BrainReplyFrame(reply_text 非空, actions=[ActionSpec(name="nod")])` |
| 期望 ActionResultFrame | `status="ok"`，`name="nod"`，`duration_ms <= 1500` |
| 端到端断言 | `t(TranscriptionFrame.is_final=True)` 到 `t(TTSAudioFrame.is_final=True)` 差值 ≤ 2000 ms（mock STT/TTS 环境） |
| 失败信号 | reply_text 为空 / actions 为空 / 超时 / Lock 冲突 |

### SC-2：长任务 worker

| 项 | 值 |
|---|---|
| 输入 1 | `TranscriptionFrame(text="巡视一圈")` |
| 期望 frame 序列 | `BrainReplyFrame(worker_decision={"op":"spawn","task_type":"patrol",...})` → 多个 `ActionSpecFrame(spec.owner_id="worker:<id>")` |
| 输入 2（5 秒后） | `TranscriptionFrame(text="现在几点")` |
| 期望并发性断言 | 输入 2 的 `BrainReplyFrame` 必须在 worker 仍发出 `WorkerEventFrame(event="progress")` 的窗口内产生 |
| 终止断言 | `WorkerEventFrame(event="completed")` 对应一条 JSONL 写入 `memory/<task_id>.jsonl`，含 `status`、`summary`、`memory_refs` |

### SC-3：Barge-In

| 项 | 值 |
|---|---|
| 前置 | TTS 正在播放，`SpeechPresenter` 已发出至少一个 `TTSAudioFrame(is_final=False)` |
| 触发 | `SpeechActivityFrame(state="start")` 后跟 `TranscriptionFrame(text="等等")` |
| 时延断言 | 从 barge-in `SpeechActivityFrame` 到 `TTSAudioFrame` 流停止 ≤ 300 ms |
| 锁断言 | 当前持锁 action 若 `interruptible=True` → `cancel_token` 在 300 ms 内 set 并 `cleanup()` 执行；若 `interruptible=False` → 不取消，记录 `barge_in_blocked_by_uninterruptible` 日志 |
| 后续 | 新 turn 的 `BrainReplyFrame.turn_id` 与被打断 turn 不同 |

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
