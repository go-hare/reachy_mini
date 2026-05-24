# v4 Phase 1 落地计划

本文档把 v4 架构拆成可执行的 Phase 1 实施步骤。

Phase 1 是增量迁移：新增 `action_runtime/`、`reachy_brain/`、`pipeline/`，同时保留当前 `core/`、`front/`、`runtime/scheduler.py`、`companion/` 和 app/profile 结构。

## Phase 1 目标

交付一个显式 opt-in 的 v4 runtime path，证明：

- robot action 是一等对象，可以脱离 LLM 独立运行；
- Brain 只输出动作意图，不直接调用 SDK；
- Pipecat 能路由 speech、text、action、interrupt frame；
- 长任务 worker 不阻塞 main agent loop；
- motor lock 冲突行为确定且可观测。

## 明确范围

本阶段包含：

- 新增 `src/reachy_mini/action_runtime/`。
- 新增 `src/reachy_mini/reachy_brain/`。
- 新增 `src/reachy_mini/pipeline/`。
- 新增 v4 opt-in CLI 或 runtime entrypoint。
- 为四个 Phase 1 smoke case 增加单元测试和 smoke 测试。
- 保持 `profiles/` 原样，new brain runtime 只读 profile。

本阶段不包含：

- 删除 `front/`、`core/`、`runtime/scheduler.py` 或 `companion/`。
- 重写 `reachy_mini.py`、`motion`、`kinematics`、`io`、`media` 或 daemon。
- 修改生成 app project 的结构。
- 把 profiles 迁到 `pyproject.toml`。
- 在 smoke case 通过前把 Pipecat 设为默认 runtime。

## 目标文件树

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
│       ├── set_antenna.py
│       └── play_emotion.py
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
│   └── prompts/
│       ├── system.md
│       └── worker.md
│
├── pipeline/
│   ├── __init__.py
│   ├── frames.py
│   ├── runner.py
│   ├── brain_processor.py
│   ├── speech_presenter.py
│   ├── action_dispatcher.py
│   ├── stt_funasr.py
│   └── tts_kokoro.py
```

测试目录：

```text
tests/unit_tests/action_runtime/
  test_motor_lock.py
  test_action_registry.py
  test_action_executor.py
  test_builtin_actions.py

tests/unit_tests/reachy_brain/
  test_agent_config.py
  test_tool_adapter.py
  test_worker_runtime.py

tests/unit_tests/pipeline/
  test_frames.py
  test_action_dispatcher.py
  test_speech_presenter.py
  test_brain_processor.py
```

## 实施切片

### Slice 1：Action Runtime 核心

目标：不依赖 LLM 或 Pipecat，也能运行机器人动作。

文件：

- `action_runtime/action.py`
- `action_runtime/metadata.py`
- `action_runtime/errors.py`
- `action_runtime/registry.py`
- `action_runtime/motor_lock.py`
- `action_runtime/executor.py`

公开 API：

```python
from reachy_mini.action_runtime import (
    ActionExecutor,
    ActionRegistry,
    ActionSpec,
    RobotAction,
)
```

`ActionMetadata`（`metadata.py`）字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | `str` | 全局唯一动作名，作为 `ActionSpec.name` 的目标。kebab-case 不允许，统一用 snake_case。 |
| `description` | `str` | 给 LLM 看的能力说明。一句话，描述意图与可观察效果，**禁止暴露电机/插值实现**。 |
| `parameter_schema` | `dict[str, Any]` | JSON Schema（draft-07 子集）。dispatcher 在调用前用它校验 `ActionSpec.params`。 |
| `required_locks` | `frozenset[str]` | 申请该动作时需要拿的锁集合，引用架构文档"Lock 名称表"。 |
| `default_priority` | `int` | 该动作的默认优先级（与 `RobotAction.priority` 字段同义；元数据层冗余记录便于不实例化也能查询）。 |
| `default_interruptible` | `bool` | 默认可中断性。 |
| `default_duration_s` | `float \| None` | 估计执行秒数，用于 deadline 推断。 |
| `tags` | `list[str]` | 可选标签，如 `"head"`、`"emotion"`、`"locomotion"`，便于 UI/筛选。 |
| `safety_notes` | `str` | 安全相关说明（夹手、近距离风险），仅日志/审计用，LLM 不可见。 |

`ActionRegistry` 行为：

| 方法 | 输入 | 输出 / 副作用 |
|---|---|---|
| `register(metadata, builder)` | `metadata: ActionMetadata`、`builder: Callable[[ActionSpec], RobotAction]` | 把 metadata 与 builder 存入内部表。同名重复注册抛 `DuplicateActionError`。 |
| `get_metadata(name)` | `name: str` | 返回 `ActionMetadata`，未注册抛 `UnknownActionError`。 |
| `build(spec)` | `spec: ActionSpec` | 校验 schema，调用 builder 返回 `RobotAction`；schema 失败抛 `ActionParamError`。 |
| `list_metadata()` | — | 返回所有 metadata 的浅拷贝列表，供 tool adapter 与 CLI 读取。 |

`errors.py` 暴露的异常族：

| 异常 | 触发场景 |
|---|---|
| `ActionRuntimeError` | 父类，所有运行时错误都继承自它。 |
| `UnknownActionError` | `ActionSpec.name` 未注册。 |
| `DuplicateActionError` | 注册时重复 name。 |
| `ActionParamError` | `params` 不满足 schema。 |
| `LockBusyError` | 锁冲突且不可抢占。 |
| `LockTimeoutError` | 等待锁超过 `wait_timeout_s`。 |
| `ActionRunError` | `RobotAction.run()` 内部抛错的包装，`__cause__` 指向原异常。 |
| `ActionCancelledError` | 被抢占或外部取消。 |

`ActionExecutor` 主流程：

```text
submit(spec)
  -> registry.build(spec)         # 校验 + 实例化
  -> lock_manager.acquire(...)    # 拿全 required_locks
  -> action.prepare(ctx)
  -> action.run(ctx)              # 同步等待或异步轮询
  -> action.cleanup(ctx)          # 必跑
  -> lock_manager.release_all(lease_handles)
  -> emit ActionResult
```

实现要点：

- `ActionSpec` 不可变且可 JSON 序列化。
- `RobotAction` 拥有动作生命周期：`prepare`、`run`、`cancel`、`cleanup`。
- `ActionRegistry` 注册动作元数据和 builder。
- `ActionExecutor` 将 `ActionSpec` 解析为 `RobotAction`，获取锁，执行动作，并保证释放锁。
- `MotorLockManager` 支持 owner、priority、interruptibility、expiration 和清晰冲突错误。

验收：

- 单元测试覆盖锁获取、冲突、抢占、异常释放、未知动作。
- fake `ReachyMini` 对象可以运行 `nod`、`shake_head`、`set_antenna`。

### Slice 2：内置动作库

目标：提供第一批 SDK 原生动作。

文件：

- `action_runtime/library/look_at.py`
- `action_runtime/library/nod.py`
- `action_runtime/library/shake_head.py`
- `action_runtime/library/set_antenna.py`
- `action_runtime/library/play_emotion.py`

最小动作集：

| Action | Required Locks | 说明 |
|---|---|---|
| `look_at` | `{"head"}` | 使用现有 SDK pose helper 和 `goto_target`。 |
| `nod` | `{"head"}` | 简单 head pitch sequence。 |
| `shake_head` | `{"head"}` | 简单 yaw sequence。 |
| `set_antenna` | `{"antenna_left", "antenna_right"}` | 支持单侧或双侧 antenna target。 |
| `play_emotion` | action-defined | 可用时包装现有 recorded moves 路径。 |

各动作 `params` schema：

`look_at`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `target` | `[float, float, float]` | 是 | 目标点的机器人坐标系坐标 (x, y, z)，单位米。 |
| `duration_s` | `float` | 否 | 插值时长，默认 0.8。范围 [0.2, 5.0]。 |
| `interpolation` | `"linear"\|"minjerk"\|"ease_in_out"\|"cartoon"` | 否 | 插值方式，默认 `minjerk`。 |
| `keep_after` | `bool` | 否 | true 表示动作完成后保持目标姿态，false 表示完成即释放（默认 false）。 |

`nod`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `cycles` | `int` | 否 | 点头次数，默认 2，范围 [1, 5]。 |
| `amplitude_deg` | `float` | 否 | pitch 振幅角度，默认 12，范围 [3, 25]。 |
| `period_s` | `float` | 否 | 单次周期秒，默认 0.6，范围 [0.3, 1.5]。 |

`shake_head`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `cycles` | `int` | 否 | 摇头次数，默认 2。 |
| `amplitude_deg` | `float` | 否 | yaw 振幅角度，默认 18。 |
| `period_s` | `float` | 否 | 单次周期秒，默认 0.7。 |

`set_antenna`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `left_deg` | `float \| None` | 否 | 左天线目标角度（度），`None` 表示不动该侧。范围 [-180, 180]。 |
| `right_deg` | `float \| None` | 否 | 右天线目标角度。 |
| `duration_s` | `float` | 否 | 到位时长，默认 0.5。 |

> 实现细节：当 `left_deg`/`right_deg` 仅其一非 None，`required_locks` 在 builder 中收窄为单侧锁，避免不必要冲突。

`play_emotion`：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `name` | `str` | 是 | RecordedMoves 库中的 emotion 名（如 `"happy"`、`"sad"`、`"yes"`）。 |
| `library` | `str` | 否 | HF 路径，默认 `pollen-robotics/reachy-mini-emotions-library`。 |
| `initial_goto_duration_s` | `float` | 否 | 起始过渡时长，默认 1.0。 |
| `speed` | `float` | 否 | 回放速度倍率，默认 1.0，范围 [0.5, 2.0]。 |

> `required_locks` 在 builder 中根据所选 emotion 的轨迹自动推断（含 head/body/antenna 子集），具体逻辑沿用 `RecordedMoves.get(name).motors`。

验收：

- 不连接机器人也能读取动作元数据。
- 能从 `ActionSpec` 实例化动作。
- 能在 fake SDK 对象上运行动作。
- head 动作相互冲突；antenna 动作可与 head 动作独立运行。

### Slice 3：Profile 到 AgentConfig 适配

目标：保持 profiles 不变，同时给新 Brain 一个干净配置对象。

文件：

- `reachy_brain/config.py`

契约：

```python
@dataclass(frozen=True)
class AgentConfig:
    model: ModelConfig
    speech: SpeechConfig
    speech_input: SpeechInputConfig
    vision: VisionConfig
    extras: dict[str, Any]
```

字段映射规则（profile `kind` → AgentConfig 字段）：

| profile `kind` | 进入字段 | 缺失时行为 |
|---|---|---|
| `kernel_model` | `model`（首选） | 回退到 `front_model` |
| `front_model` | `model`（仅当无 `kernel_model`） | 报错 `MissingModelConfigError` |
| `speech` | `speech` | 用 `SpeechConfig` 默认值并打 warning |
| `speech_input` | `speech_input` | 用默认值并打 warning |
| `vision` | `vision` | 用默认值并打 warning |
| `profile`、`front` | 暂不进 `AgentConfig`（保留 profile 元数据，由 brain runtime 另行读取） | — |
| 其它未知 `kind` | `extras[kind]` | 透传，dispatcher 不消费 |

`from_profile()` 入口：

```python
def from_profile(
    profile_path: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> AgentConfig:
    ...
```

| 参数 | 含义 |
|---|---|
| `profile_path` | 指向 `profiles/<app>/profiles/` 目录或 `config.jsonl` 文件。两种形式都接受。 |
| `overrides` | 用于测试和 CLI 覆盖。键路径使用点号语法：`{"model.temperature": 0.0, "speech.enabled": False}`。覆盖发生在合并之后。 |

API key 处理（重要）：

- 严禁把明文 `api_key` 写进 `config.jsonl`。
- profile 中 `api_key` 字段必须形如 `env:VAR_NAME` 或 `vault:<ref>`。loader 在加载时读取对应环境变量；找不到 env var 抛 `MissingApiKeyError`。
- 现有含明文 key 的 profile 必须在 Phase 1 启动前迁移；loader 检测到明文 `sk-` 前缀时打 ERROR 日志并拒绝加载（可通过 `REACHY_ALLOW_PLAINTEXT_KEY=1` 临时绕过，仅供本地调试）。

规则：

- 读取现有 `profiles/<app>/profiles/config.jsonl`。
- 优先使用 `kernel_model`；没有时回退到 `front_model`。
- 复用现有 `speech`、`speech_input`、`vision` 段。
- 不把 `ProfileBundle` 暴露给 `reachy_brain.agent`。
- 不写 profile 文件。

验收：

- 现有 profile fixtures 无需修改即可加载（key 字段除外）。
- 缺少 `kernel_model` 时能回退到 `front_model`。
- 现有 speech 和 vision 字段映射后不丢失。
- 明文 key 在严格模式下被拒绝。

### Slice 4：Brain Tool Adapter

目标：把 action metadata 暴露给 Brain，但不暴露电机细节。

文件：

- `reachy_brain/tool_adapter.py`
- `reachy_brain/agent.py`

职责：

- 把 `ActionRegistry` metadata 转成 Claude Agent SDK tool definition。
- 把 tool call 转回 `ActionSpec`。
- 保持 Brain 输出结构为 `reply_text`、`speech_style`、`actions` 和可选 worker decision。

`ActionMetadata → ToolDefinition` 映射：

| ActionMetadata 字段 | ToolDefinition 字段 | 转换规则 |
|---|---|---|
| `name` | `name` | 原样。 |
| `description` | `description` | 原样。 |
| `parameter_schema` | `input_schema` | 原样（已是 JSON Schema）。 |
| `tags` | `metadata.tags` | 透传，不进入 LLM prompt。 |
| `required_locks` | — | **不暴露给 LLM**。 |
| `safety_notes` | — | **不暴露给 LLM**，仅记日志。 |
| `default_priority` | — | 不暴露；由 dispatcher 在生成 `ActionSpec` 时填入。 |

`tool_call → ActionSpec` 转换字段：

| tool_call 字段 | ActionSpec 字段 | 备注 |
|---|---|---|
| `name` | `name` | 命中 registry，否则 adapter 把 `tool_error` 回给 LLM。 |
| `arguments` (JSON) | `params` | 用 `parameter_schema` 校验后赋值。 |
| LLM 当前 turn id | `request_id` | adapter 注入。 |
| `"main-agent"` 或 `"worker:<id>"` | `owner_id` | adapter 注入。 |
| metadata 中的 `default_priority` | `priority` | adapter 注入。 |

Brain 输出契约（Slice 4 的 `BrainAgent.run(turn_input)`）：

```python
@dataclass(frozen=True)
class BrainTurnOutput:
    reply_text: str
    speech_style: dict[str, Any]
    actions: list[ActionSpec]
    worker_decision: dict[str, Any] | None
    raw_model_response: dict[str, Any]
```

| 字段 | 含义 |
|---|---|
| `reply_text` | 直接进入 `BrainReplyFrame.reply_text`。 |
| `speech_style` | 直接进入 `BrainReplyFrame.speech_style`。 |
| `actions` | tool call 解析后产出的 `ActionSpec` 列表，已经过 schema 校验。 |
| `worker_decision` | 从 LLM 的特定结构化输出（或专用 tool `spawn_worker`）解析。 |
| `raw_model_response` | 仅供 metadata/调试，BrainProcessor 不落到下游 frame。 |

规则：

- LLM 看到 action name、description、parameter schema。
- LLM 不看到 `required_locks`、SDK 调用、插值实现、电机时序细节，除非这些信息被提炼成高层能力说明。
- tool execution 不调用 SDK，只返回 `ActionSpec`。
- 非法参数在 adapter 内被拒绝（抛 `ActionParamError`），adapter 把错误以 `tool_result` 形式返回给 LLM 继续修正，不向下游发 `ActionSpec`。

验收：

- mock agent 能选择 `nod` 并产出 `ActionSpec("nod")`。
- mock agent 能只输出 reply text，不带 action。
- 非法 action 参数在进入 `ActionExecutor` 前被拒绝。

### Slice 5：Worker Runtime

目标：证明长任务不阻塞主 loop。

文件：

- `reachy_brain/worker.py`
- `reachy_brain/coordinator.py`
- `reachy_brain/memory.py`

`TaskSpec` 字段：

```python
@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_type: str
    params: dict[str, Any]
    parent_request_id: str
    spawned_at: float
    deadline_s: float | None
    memory_namespace: str
```

| 字段 | 含义 |
|---|---|
| `task_id` | 全局唯一 worker ID（ULID/UUID），由 main agent 生成，写入 `BrainReplyFrame.worker_decision.task_id`。 |
| `task_type` | 命中 worker registry，例如 `patrol`、`scan_room`。未注册类型直接拒绝。 |
| `params` | worker 初始化参数，必须 JSON 可序列化。 |
| `parent_request_id` | spawn 该 worker 的那个 brain decision request_id，用于 trace 串联。 |
| `spawned_at` | 单调时钟时间戳。 |
| `deadline_s` | 总超时秒数；超时由 coordinator 强制终止 worker。 |
| `memory_namespace` | JSONL 文件命名空间，默认 `memory/<task_id>.jsonl`，便于隔离。 |

`WorkerEventFrame` 字段（Slice 5 也定义其载荷）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `task_id` | `str` | 对应 `TaskSpec.task_id`。 |
| `event` | `str` | `"started"`/`"progress"`/`"completed"`/`"failed"`/`"cancelled"`。 |
| `payload.note` | `str` | 一句话状态描述。 |
| `payload.progress` | `float \| None` | 0–1 进度百分比；不可估时为 `None`。 |
| `payload.metrics` | `dict` | 自由结构的指标，如 `{"frames_scanned": 12}`。 |
| `payload.error` | `str \| None` | 仅 `failed` 事件填，错误简述。 |
| `ts_ms` | `int` | 单调时钟时间戳。 |

worker 内 `run()` 协议：

```python
async def run(self, ctx: WorkerContext) -> WorkerResult:
    ...
```

`WorkerContext` 字段：

| 字段 | 含义 |
|---|---|
| `spec` | 透传 `TaskSpec`。 |
| `emit_action(spec: ActionSpec) -> Awaitable[ActionResult]` | 把 action 送进 dispatcher 并 await 结果；spec 的 `owner_id` 自动覆盖为 `worker:<task_id>`，priority 限制在 ≤30。 |
| `emit_event(event, payload)` | 写一条 `WorkerEventFrame` 到 pipeline 与 memory。 |
| `memory.append(record: dict)` | 追加 JSONL 记录。 |
| `memory.read(filter)` | 读取本 namespace 下的历史记录。 |
| `cancel_token` | 异步取消信号；coordinator 在用户取消、deadline、或 main agent 决策 `cancel` worker 时 set。 |
| `clock` | 注入时钟，便于测试。 |

`WorkerResult` 字段：

```python
@dataclass(frozen=True)
class WorkerResult:
    status: str               # "completed" | "failed" | "cancelled"
    summary: str
    memory_refs: list[str]    # JSONL 行 ID 或文件路径
    metrics: dict[str, Any]
```

最小行为：

- Main agent 能用 `TaskSpec` spawn worker。
- Worker 有独立 context。
- Worker 产出 `ActionSpec` 和 `WorkerEventFrame`。
- Worker 将进度和结果写入 JSONL memory。
- Main agent 继续接收新输入。

结果协议（XML 序列化用于 LLM context 注入）：

```xml
<task-result>
  <status>completed</status>
  <summary>...</summary>
  <memory_refs>...</memory_refs>
</task-result>
```

| 元素 | 必填 | 含义 |
|---|---|---|
| `status` | 是 | 与 `WorkerResult.status` 同义。 |
| `summary` | 是 | 一段自然语言摘要，给 main agent 当下一轮 context。 |
| `memory_refs` | 否 | 列出 memory 中可被检索的行 ID/文件路径，供后续 turn 引用。 |

验收：

- fake patrol worker 产出一串 action spec。
- worker 运行时，main agent 能处理第二个输入。
- worker 完成后产出可解析的 task result。

### Slice 6：Pipecat Frame 层

目标：先定义 frame contract，再接 live audio。

文件：

- `pipeline/frames.py`
- `pipeline/brain_processor.py`
- `pipeline/action_dispatcher.py`
- `pipeline/speech_presenter.py`

最小 frame：

```text
BrainReplyFrame
ActionSpecFrame
ActionResultFrame
WorkerEventFrame
SpeechPresenterFrame
```

`SpeechPresenterFrame` 字段（Slice 6 引入的内部 frame）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `text` | `str` | 已经过分句/标点处理、准备进入 TTS 的文本片段。 |
| `style` | `dict[str, Any]` | 见架构文档 `BrainReplyFrame.speech_style`。 |
| `turn_id` | `str` | 与上游 `BrainReplyFrame.turn_id` 一致。 |
| `chunk_index` | `int` | 同一 turn 内的分片序号，从 0 开始。 |
| `is_final` | `bool` | true 表示本 turn 最后一片。 |

`BrainProcessor` 行为契约：

| 输入 frame | 处理 | 输出 frame |
|---|---|---|
| `TranscriptionFrame(is_final=True)` | 调 `BrainAgent.run` | `BrainReplyFrame` |
| `TranscriptionFrame(is_final=False)` | 累积，不触发 brain | — |
| `BrowserInputFrame(kind="text")` | 等价于 final transcription | `BrainReplyFrame` |
| `VisionEventFrame` | 进 brain context buffer | — |
| `SpeechActivityFrame(state="start")` 且当前在 TTS | 触发 barge-in 路径 | `InterruptFrame` |
| `WorkerEventFrame` | 进 brain context buffer | — |

`ActionDispatcher` 行为契约：

| 输入 | 行为 | 输出 |
|---|---|---|
| `BrainReplyFrame` | 解包 `actions`，逐个生成 `ActionSpecFrame` 顺序下发 | `ActionSpecFrame` × n |
| `ActionSpecFrame` | 提交 `ActionExecutor.submit(spec)`，等待结果 | `ActionResultFrame` |
| `InterruptFrame(scope="actions")` | 对当前所有可中断 action 调 `executor.cancel(request_id)` | — |

`SpeechPresenter` 行为契约：

| 输入 | 行为 | 输出 |
|---|---|---|
| `BrainReplyFrame` | 按标点/长度分片，附 style，逐片发出 | `SpeechPresenterFrame` × n |
| `InterruptFrame(scope="speech")` | 丢弃未发出的分片，向 TTS 发停止信号 | `TTSStopFrame` |
| 空 `reply_text` | 直接跳过，不产出 frame | — |

职责：

- `BrainProcessor` 调用 brain runtime 并输出 `BrainReplyFrame`。
- `ActionDispatcher` 把 `ActionSpec` 送进 `ActionExecutor`。
- `SpeechPresenter` 在 TTS 前处理 reply text。

验收：

- text input frame 可以通过 mock 产出 reply text 和 action spec。
- action frame 可以经 fake `ActionExecutor` 执行。
- barge-in signal 可以取消 pending speech 并请求 action cancellation。

### Slice 7：FunASR 和 Kokoro Adapter

目标：把当前 speech 能力接进 Pipecat path。

文件：

- `pipeline/stt_funasr.py`
- `pipeline/tts_kokoro.py`
- `pipeline/runner.py`

`FunASRAdapter` 行为：

| 输入 | 输出 frame |
|---|---|
| 来自 mic 或 mock 的 PCM bytes | `TranscriptionFrame(text, is_final, turn_id, lang)` |
| FunASR `sentence_end=True` | `TranscriptionFrame(is_final=True)` |
| 静音超时（`stream_finish_timeout_s`） | `TranscriptionFrame(is_final=True, text=已累积文本)` |
| 连接断开 | `PipelineErrorFrame(component="stt_funasr", reason=...)` |

构造参数（从 `SpeechInputConfig` 取）：

| 参数 | 含义 |
|---|---|
| `base_url` | FunASR websocket。 |
| `model` | `2pass` 等。 |
| `language` | ISO 语言码。 |
| `stream_*` 参数 | 与架构文档 `SpeechInputConfig` 字段同义，原样透传。 |
| `playback_block_cooldown_ms` | 收到 `TTSAudioFrame(is_final=True)` 后 N 毫秒内忽略 mic，避免自循环。 |

`KokoroAdapter` 行为：

| 输入 frame | 输出 |
|---|---|
| `SpeechPresenterFrame` | `TTSAudioFrame(pcm, sample_rate, turn_id, is_final)` |
| `TTSStopFrame` | 停止当前合成、清空待发 PCM 队列 |

构造参数（从 `SpeechConfig` 取）：

| 参数 | 含义 |
|---|---|
| `model` | Kokoro HF 模型 ID。 |
| `voice` | 默认 voice id；可被 frame 内 `style.voice` 覆盖。 |
| `speed` | 默认语速；可被 `style.speed` 覆盖。 |
| `sample_rate` | 输出采样率。 |

规则：

- 尽量复用当前 FunASR websocket 行为。
- 尽量复用当前 Kokoro/reply audio 行为。
- Phase 1 不删除 `runtime/speech_session.py` 或 `runtime/reply_audio.py`。
- Adapter 保持薄层，后续可替换为 Pipecat 原生集成。

验收：

- 单元测试不依赖真实音频硬件。
- integration smoke 可以用 mock frame 运行。
- 如果没有硬件，live audio smoke 可以标为手动验证。

### Slice 8：Opt-In Runtime Entry

目标：运行 v4，但不破坏旧 runtime。

候选入口：

```bash
conda run -n reachy reachy-mini-agent v4 profiles/sim_front_app
```

开发早期备用入口：

```bash
conda run -n reachy python -m reachy_mini.pipeline.runner profiles/sim_front_app
```

`pipeline.runner` CLI 参数：

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `profile_path` | 位置参数 | — | profile 目录或 `config.jsonl` 路径。 |
| `--mode` | `"live"\|"text"\|"mock"` | `live` | `live` 接 mic/speaker；`text` 仅 stdin/stdout；`mock` 用注入的 fake STT/TTS（测试用）。 |
| `--no-camera` | flag | False | 覆盖 `vision.no_camera`。 |
| `--override` | 多值 `key=value` | — | 覆盖任意 `AgentConfig` 字段，例如 `--override model.temperature=0.0`。 |
| `--log-level` | `str` | `INFO` | 日志级别。 |
| `--trace-file` | `path` | `None` | 启用 frame trace 输出 JSONL，便于回放。 |

复用现有 helper：

- 复用 `runtime/profile_loader.py:load_profile_bundle()` 解析 profile 路径。
- 复用 `runtime/project.py` 的 app 解析逻辑。
- `ProfileBundle` 仅在 `pipeline.runner` 内部使用，不传给 `reachy_brain.agent`。

规则：

- 旧命令继续可用。
- v4 path 必须显式启用。
- app path / profile path 解析尽量复用现有 runtime project/profile helper。

验收：

- 用 mock profile 运行 v4 entrypoint 能启动 pipeline。
- 现有 `reachy-mini-agent agent ...` 仍然走旧 runtime。

## Smoke Case 细节

每个 smoke case 给出输入 frame、期望 frame 序列、判定字段、超时窗口。这些 case 在 `tests/unit_tests/.../smoke/` 下落地为 pytest，用 mock STT/TTS、fake `ReachyMini` 跑。

### SC-1：短任务

输入：

```text
你好
```

输入 frame：`TranscriptionFrame(text="你好", is_final=True, turn_id=T1)`

期望 frame 序列：

| 序号 | Frame | 关键字段断言 |
|---|---|---|
| 1 | `BrainReplyFrame` | `turn_id == T1`、`reply_text != ""`、`len(actions) >= 1`、`actions[0].name in {"nod","look_at","play_emotion"}` |
| 2 | `SpeechPresenterFrame` × n | 全部 `turn_id == T1`，最后一个 `is_final=True` |
| 3 | `TTSAudioFrame` × n | 全部 `turn_id == T1`，最后一个 `is_final=True` |
| 4 | `ActionResultFrame` | `status == "ok"`、`name == actions[0].name`、`duration_ms <= 1500` |

时延断言：`t(BrainReplyFrame.emit) - t(TranscriptionFrame.is_final=True) <= 600 ms`，`t(TTSAudioFrame.is_final=True) - t(TranscriptionFrame) <= 2000 ms`（mock 环境）。

测试形态：

```text
tests/unit_tests/pipeline/test_short_turn_smoke.py
```

### SC-2：长任务 Worker

输入 1：`TranscriptionFrame(text="巡视一圈", is_final=True, turn_id=T1)`

期望 frame 序列：

| 阶段 | Frame | 关键字段断言 |
|---|---|---|
| spawn | `BrainReplyFrame` | `worker_decision.op == "spawn"`、`worker_decision.task_type == "patrol"`、`reply_text != ""` |
| 进行中 | `WorkerEventFrame` × ≥1 | `event in {"started","progress"}`、`task_id` 与 spawn 一致 |
| 进行中 | `ActionSpecFrame` × ≥1 | `spec.owner_id == "worker:<task_id>"`、`spec.priority <= 30`、`spec.parent_request_id == T1 brain decision request_id` |

输入 2（在 spawn 后 5s 内、worker 仍发 progress 时）：`TranscriptionFrame(text="现在几点", is_final=True, turn_id=T2)`

并发性断言：`t(BrainReplyFrame for T2) < t(WorkerEventFrame(event="completed") for T1)`。

终止断言：

| 项 | 期望 |
|---|---|
| `WorkerEventFrame(event="completed")` | 出现一次，`task_id` 一致 |
| `memory/<task_id>.jsonl` | 文件存在，最后一行可解析为 `WorkerResult`，`status == "completed"`，`summary != ""` |
| 注入 main agent 的 task-result XML | 含 `<status>completed</status>` 和 `<summary>` 节点 |

测试形态：

```text
tests/unit_tests/reachy_brain/test_worker_runtime.py
```

### SC-3：Barge-In

输入序列：

| 时刻 | Frame |
|---|---|
| t0 | `TranscriptionFrame(text="给我讲个故事", is_final=True, turn_id=T1)` |
| t1 | `TTSAudioFrame(turn_id=T1, is_final=False)` 已开始播放 |
| t2 | `SpeechActivityFrame(state="start", ts_ms=t2)` |
| t3 | `TranscriptionFrame(text="等等", is_final=True, turn_id=T2)` |

判定字段：

| 项 | 期望 |
|---|---|
| `TTSAudioFrame` 流停止时刻 | `t_stop - t2 <= 300 ms` |
| `InterruptFrame(scope="speech")` | 在 `[t2, t2+300ms]` 区间内出现一次 |
| 当前持锁 action 是否取消 | `interruptible=True` → `cancel()` 被调一次且 `cleanup()` 被调一次；`interruptible=False` → 都不调，日志含 `barge_in_blocked_by_uninterruptible` |
| 新 turn `BrainReplyFrame.turn_id` | `== T2 != T1` |
| 旧 turn 后续 frame | `t > t2` 后不再出现 `turn_id == T1` 的 `TTSAudioFrame` 或 `SpeechPresenterFrame` |

测试形态：

```text
tests/unit_tests/pipeline/test_barge_in_smoke.py
```

### SC-4：Motor Lock 冲突

前置：worker 已提交 `ActionSpec(name="look_at", owner_id="worker:W1", priority=20, interruptible=True, request_id=R_W)`，已拿到 `head` 锁，正在 `run`。

触发：main agent 提交 `ActionSpec(name="nod", owner_id="main-agent", priority=40, interruptible=True, request_id=R_M)`。

期望（抢占路径）：

| 项 | 期望 |
|---|---|
| Lock 决策日志 | 一条 `decision="preempted"`，含 `lock_name="head"`、`previous_owner="worker:W1"`、`new_owner="main-agent"` |
| `cancel_token` for W1 | 在抢占决策后 ≤ 300 ms 内 set |
| W1 的 `ActionResultFrame` | `status == "cancelled"`、`request_id == R_W` |
| M 的 `ActionResultFrame` | `status == "ok"`、`request_id == R_M` |
| SDK 调用断言 | 同一 100 ms 窗口内 fake `mini.head.set_target` / `goto_target` 的调用方 owner_id 唯一 |

反向用例：把 worker spec 改为 `interruptible=False`：

| 项 | 期望 |
|---|---|
| Lock 决策日志 | `decision="rejected"`，含 `reason="uninterruptible_higher_priority_blocked"` 或 `reason="uninterruptible_holding"` |
| M 的 `ActionResultFrame` | `status == "error"`、`error` 字段含 `LockBusyError` |
| W1 是否被中断 | 否，继续跑到自然结束 |

测试形态：

```text
tests/unit_tests/action_runtime/test_motor_lock.py
```

## 推荐实施顺序

1. 创建 `action_runtime` contract 和 fake-SDK 测试。
2. 实现 `MotorLockManager`。
3. 实现 `ActionRegistry` 和 `ActionExecutor`。
4. 增加 head 和 antenna 内置动作。
5. 增加 `AgentConfig.from_profile()`。
6. 增加 brain tool adapter 和 mock agent 测试。
7. 增加 worker runtime 和 fake long task。
8. 增加 pipeline frames 和 mock processors。
9. 增加 ActionDispatcher 和 SpeechPresenter。
10. 增加 FunASR/Kokoro adapter stub。
11. 增加显式 v4 entrypoint。
12. 跑通四个 smoke case。

不要从 Claude Agent SDK 或 live audio 集成开始。第一个稳定里程碑应是 SDK 原生 Action Runtime + 确定性测试。

## 验证命令

Phase 1 目标测试：

```bash
conda run -n reachy pytest tests/unit_tests/action_runtime
conda run -n reachy pytest tests/unit_tests/reachy_brain
conda run -n reachy pytest tests/unit_tests/pipeline
```

接线后需要保持绿色的旧回归测试：

```bash
conda run -n reachy pytest tests/unit_tests/test_runtime_reachy_tools.py
conda run -n reachy pytest tests/unit_tests/test_agent_profile_config.py
conda run -n reachy pytest tests/unit_tests/test_profile_loader.py
conda run -n reachy pytest tests/unit_tests/test_resident_runtime_host.py
```

如对 touched files 跑静态检查：

```bash
conda run -n reachy ruff check src/reachy_mini/action_runtime src/reachy_mini/reachy_brain src/reachy_mini/pipeline
```

## Phase 1 完成定义

同时满足以下条件，Phase 1 才算完成：

- `ActionRuntime` 可以在无 LLM 情况下运行内置动作。
- `Brain` 可以输出结构化 `BrainReplyFrame`。
- `ActionDispatcher` 可以通过 `ActionExecutor` 执行 `ActionSpec`。
- Worker 任务与 main-agent input handling 并发运行。
- mock pipeline 中 barge-in 可以取消 speech。
- motor lock 冲突行为有测试和清晰日志。
- 新 v4 runtime 是 opt-in，旧 runtime 命令仍可用。
- SC-1 到 SC-4 全部通过。

## Phase 2 进入条件

Phase 1 没完成前，不开始删除旧 runtime 层。

Phase 2 只能在以下条件满足后开始：

- v4 opt-in path 稳定；
- 四个 smoke case 通过；
- 至少用一个 generated app 和 `profiles/sim_front_app` 证明 profile compatibility；
- 旧 runtime regression tests 仍然通过。

之后 Phase 2 可以把默认 runtime 切到 v4，并退役：

- `front/`
- `companion/intent.py`
- `runtime/scheduler.py`
- `core/agent.py:BrainKernel`
