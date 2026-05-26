# v4 Phase 2 落地计划

本文档把 v4 从 opt-in 单轮 smoke path 推到默认 runtime——**完全切到架构图所述的三层模型**：旧 RuntimeScheduler / front / companion / BrainKernel 路径直接删除，前端 websocket 协议同步换成 v4 frame 序列化，不保留 compat shim、不保留 `--legacy-runtime` flag。

Phase 2 的前置条件来自架构文档：v4 opt-in path 稳定、四个 smoke case 通过、profile loader 兼容性已验证、旧 runtime regression 用例仍绿。

## Phase 2 目标

- L3/L2/L1 是**唯一**实现：`reachy_brain.agent.BrainAgent` + `pipeline.session.RuntimeSession` + `action_runtime.ActionExecutor`。
- `reachy-mini-agent agent` / `reachy-mini-agent web` / daemon / resident runtime / browser websocket 全部走 `RuntimeSession`。
- 浏览器 ↔ runtime 协议直接传 SDK-first frame：入站 `BrowserInputFrame` / `AudioFrame` / `SpeechActivityFrame`，出站 `SDKMessageFrame` / `ActionResultFrame` / `WorkerEventFrame` / `TTSAudioFrame` / `SpeechPresenterFrame`，序列化规则由 `pipeline/wire.py` 锁定。
- 旧前端 JS（`apps/templates/app/static/main.js.j2` 与 `profiles/*/<name>/static/main.js`）改写到新协议。
- 删除 `front/`、`companion/intent.py`、`runtime/scheduler.py`、`core/agent.py`、`core/kernel.py`、`core/_compat.py`，并清理依赖它们的所有 import 与测试。
- `profiles/sim_front_app` 与 `reachy-mini-agent create __probe__` 生成的 app 在新默认路径下端到端可用（CLI text、Web UI、live mic→speaker）。

## 非目标

- 不重写 `reachy_mini.py`、`motion`、`kinematics`、`io`、`media`、`runtime/embodiment/coordinator.py`。
- 不修改 `profiles/<app>/profiles/config.jsonl` 文件格式。
- 不替换 FunASR / Kokoro / Pipecat 的实现选型。
- 不引入 MCP server / 远端协议 / 多机部署（Phase 3）。
- 不保留 `RuntimeScheduler` 的 alias / shim（任何形式）。
- 不保留 `front_hint_chunk` / `front_final_chunk` / `front_decision` / `front_tool_result` 旧协议（任何形式）。

## 范围

包含：

- v4 多轮 session runtime（live、worker、barge-in 实际编排）
- 新 websocket frame 协议（双向）+ 服务端发送循环
- 新前端 JS（`main.js.j2` + `profiles/*/static/main.js` 的 v4 重写）
- daemon / resident runtime / mic / speaker 接入 v4
- profile 兼容性回归
- 旧层删除与公开 API 收敛
- 文档（README / AGENTS.md / docs/source）随代码同步

不包含：

- Phase 1 已交付的 ActionRuntime / Brain / Pipeline frame contract（仅可补丁，不改字段名）
- Phase 1 已交付的 5 个内置动作
- profile 文件格式迁移
- Pipecat / Claude Code Python SDK 的版本升级
- React `ui/robot-workbench/` 的桌面 UI（不消费 app websocket，不在本计划范围；其改造在 Phase 3）

## 当前已知差距

| 能力 | Phase 1 状态 | Phase 2 必须做到 |
|---|---|---|
| 单轮 text 模式 | `pipeline/runner.py:run_text_turn` 已能跑 | 保留作为 smoke 入口，复用同一 BrainProcessor |
| 多轮 session | 无 | 新增 `pipeline.session.RuntimeSession` |
| Worker 跨 turn | Claude Code Python SDK `AgentDefinition` / task message 已接入 | 在 session 内常驻，独立于 main loop；不新增 Reachy 侧任务调度模块 |
| Live mic→speaker | `pipeline/stt_funasr.py`/`tts_kokoro.py` 是 stub | 串成 session loop（live_io.py） |
| Barge-in 路径 | frame contract 已定，executor 支持 cancel | session 把 `SpeechActivityFrame` 路由到 `InterruptFrame` |
| Browser ↔ runtime 协议 | 旧协议 `front_*` | 直接 frame 序列化（见 §Wire 协议） |
| Browser 端 JS | 旧协议 + 旧 stage bubble UI | 新协议 + 新渲染（reply_text / actions / worker_events） |
| `apps/app.py` | 通过 `RuntimeScheduler` 持有 runtime | 直接 `RuntimeSession` |
| Daemon | 间接持 RuntimeScheduler | 直接 RuntimeSession |
| CLI | `agent` 走旧、`v4` 是 opt-in | `agent` 直接走 v4，`v4` 子命令删除 |

## 目标文件树

新增：

```text
src/reachy_mini/pipeline/
├── session.py            # RuntimeSession：多轮 + SDK worker/sub-agent 常驻 + barge-in 编排
├── output_bus.py         # session 出站 frame 的多订阅者总线
├── live_io.py            # MicrophoneSource / SpeakerSink，复用 runtime/audio + reply_audio
├── wire.py               # frame ↔ websocket JSON 双向序列化
└── ws_app.py             # FastAPI websocket route，与 RuntimeSession 直接绑定

src/reachy_mini/apps/templates/app/static/
└── main.js.j2            # 改写为 v4 协议 + v4 渲染

profiles/sim_front_app/sim_front_app/static/main.js   # 改写
profiles/my_app/my_app/static/main.js                 # 改写
profiles/demo_web/demo_web/static/main.js             # 改写
```

删除（Slice 2-S6 完成后）：

```text
src/reachy_mini/front/                  # 整个目录
src/reachy_mini/companion/intent.py
src/reachy_mini/runtime/scheduler.py
src/reachy_mini/core/agent.py
src/reachy_mini/core/kernel.py
src/reachy_mini/core/_compat.py
```

`core/` 其它文件去留：

| 文件 | 处理 |
|---|---|
| `core/agent.py` | 删除整个文件。`BrainKernel` / `AgentHarnessKernel` 由 `reachy_brain.agent.BrainAgent` 取代。 |
| `core/kernel.py` | 删除（薄封装）。 |
| `core/_compat.py` | 删除。 |
| `core/routing.py` | Slice 2-S6 起步审计：仅被 BrainKernel 使用 → 删除；其它依赖方 → 迁到 `pipeline/session.py` 私有模块。 |
| `core/turns.py`、`core/memory.py`、`core/run_store.py`、`core/resident.py`、`core/tooling.py`、`core/models.py`、`core/sleep_agent.py`、`core/message_utils.py` | Phase 2 保留。S6 必须保证它们不再依赖被删除的 BrainKernel；它们的 v4 替换在 Phase 3 评估。 |
| `companion/intent.py` | 删除。 |
| `companion/expression.py`、`companion/runtime_surface.py`、`companion/models.py` | Phase 2 保留，仅断开对 `intent.py` 的引用。 |
| `runtime/main.py` | 改写：保留 `create` / `agent` / `web` 三个子命令；`agent` 与 `web` 默认走 v4；删除 `v4` 子命令；删除所有 `--*-provider` / `--kernel-*` flag（这些只针对旧 front/kernel 双 LLM）。 |

测试目录：

```text
tests/unit_tests/pipeline/
├── test_runtime_session.py
├── test_output_bus.py
├── test_live_io.py
├── test_wire.py
└── test_ws_app.py

tests/unit_tests/migration/
├── test_v4_default_cli.py
├── test_profile_compat_sim_front_app.py
├── test_profile_compat_generated_app.py
└── test_legacy_layers_absent.py
```

被删除模块对应的旧测试一并删除（清单见 §Slice 2-S6）。

## Wire 协议（浏览器 ↔ runtime）

每条 websocket message 是一个 JSON 对象，强制字段：

```json
{
  "type": "<frame_type>",
  "ts_ms": 1717000000000,
  "payload": { "...frame fields..." }
}
```

入站（browser → runtime）：

| `type` | payload 结构 | 对应内部 frame |
|---|---|---|
| `browser_input` | `{ "kind": "text"\|"button", "payload": {...}, "session_id": str }` | `BrowserInputFrame` |
| `audio_chunk` | `{ "pcm_b64": str, "sample_rate": int, "channels": int }` | `AudioFrame` |
| `audio_stop` | `{}` | session 通知 mic source 停 |
| `speech_activity` | `{ "state": "start"\|"end" }` | `SpeechActivityFrame`（浏览器 VAD 触发；可选） |
| `ping` | `{}` | 仅心跳，runtime 回 `pong` |

出站（runtime → browser）：

| `type` | payload 结构 | 对应内部 frame |
|---|---|---|
| `sdk_message` | Claude Code Python SDK `Message` 的可序列化 payload，字段名保持 SDK 原样 | `SDKMessageFrame` |
| `action_result` | `{ "request_id", "name", "owner_id", "status", "duration_ms", "error" }` | `ActionResultFrame` |
| `worker_event` | `{ "task_id", "event", "payload": { "note", "progress", "metrics", "error" }, "ts_ms" }` | `WorkerEventFrame` |
| `tts_audio` | `{ "pcm_b64": str, "sample_rate": int, "turn_id": str, "is_final": bool }` | `TTSAudioFrame` |
| `transcription` | `{ "text", "is_final", "turn_id", "lang" }` | `TranscriptionFrame`（用于 UI 流式转写预览） |
| `vision_event` | `{ "event": str, "payload": dict, "ts_ms": int }` | `VisionEventFrame` |
| `pipeline_error` | `{ "component": str, "reason": str }` | `PipelineErrorFrame` |
| `pong` | `{}` | 心跳响应 |

序列化规则全部在 `pipeline/wire.py` 锁定：

| 字段类型 | JSON 形式 |
|---|---|
| `bytes`（PCM） | base64 字符串，字段名以 `_b64` 结尾 |
| `frozenset`、`set` | JSON array，sorted |
| `Path` | 字符串，POSIX 风格 |
| `Enum` | `.value` |
| 不可序列化对象 | 抛 `WireSerializationError`，不发送 |

兼容性：旧协议 `type` 值（`front_hint_chunk` 等）在 `wire.py` 内**显式拒绝**——Slice 2-S6 之后浏览器若再发这些 type，runtime 回 `pipeline_error(reason="legacy_protocol_rejected")`。

## 实施切片

### Slice 2-S1：RuntimeSession

目标：v4 能跑超过一个 turn，Claude Code Python SDK session 与 SDK worker/sub-agent 在 session 内常驻。

文件：

- `pipeline/session.py`
- `pipeline/output_bus.py`
- `tests/unit_tests/pipeline/test_runtime_session.py`
- `tests/unit_tests/pipeline/test_output_bus.py`

`RuntimeSession` 公开 API：

```python
class RuntimeSession:
    @classmethod
    def from_profile(
        cls,
        profile_path: Path,
        *,
        overrides: dict[str, Any] | None = None,
        clock: Clock | None = None,
        mini_factory: Callable[[AgentConfig], Any] | None = None,
    ) -> "RuntimeSession": ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def submit_browser_input(self, frame: BrowserInputFrame) -> str: ...
    async def submit_text(self, text: str, *, turn_id: str | None = None) -> str: ...
    async def submit_audio_chunk(self, frame: AudioFrame) -> None: ...
    async def submit_speech_activity(self, frame: SpeechActivityFrame) -> None: ...

    def subscribe(self, *, filter: Callable[[Any], bool] | None = None) -> "OutputSubscription": ...
    def unsubscribe(self, sub: "OutputSubscription") -> None: ...

    async def wait_for_turn_idle(self, turn_id: str) -> None: ...
```

字段约定：

| 方法 | 含义 |
|---|---|
| `from_profile` | `AgentConfig.from_profile` + 注册内置动作 + 实例化 BrainProcessor / SpeechPresenter / ActionDispatcher / OutputBus；worker/sub-agent 由 Claude Code Python SDK 承载。 |
| `start` / `stop` | 启停 long-running tasks（SDK client/session、tick、output bus、live_io）。idempotent。 |
| `submit_*` | 入站 frame 的统一入口，立即返回（异步 schedule）。`submit_text` 是 `submit_browser_input(kind="text")` 的便捷包装。 |
| `subscribe` | 返回 `OutputSubscription`；同一份 frame fan-out 给所有订阅者。 |
| `wait_for_turn_idle` | 等该 turn 的所有 action / worker / TTS 都停。CLI 串行化用。 |

`OutputSubscription`：

| 字段 | 含义 |
|---|---|
| `queue` | `asyncio.Queue` |
| `id` | 订阅 ID |
| `filter` | `Callable[[Frame], bool] | None` |

行为契约：

- `subscribe` 必须支持 N 个并发订阅者，所有订阅者按相同顺序收到同一份 frame 的拷贝（不可变 frozen dataclass，按引用即可）。
- `RuntimeSession` 拥有一个 long-lived `ClaudeSDKClient` 或 SDK resident session；worker/sub-agent 跨 turn 存活。
- 收到 `SpeechActivityFrame(state="start")` 且当前正发 TTS：session 在 ≤ 300 ms 内向 ActionDispatcher 与 SpeechPresenter 各发一个 `InterruptFrame`，scope 分别为 `actions` 与 `speech`。
- `stop()` 必须 cancel 全部进行中的 action 和 SDK worker/sub-agent，等待 cleanup 完成才返回。

验收：

- 连发 3 个 text turn，第二个 turn 能看到第一个 turn 的 brain memory。
- 一个 SDK worker/sub-agent 跨 turn 跑，第二个 turn 期间 `WorkerEventFrame(event="progress")` 仍能被订阅者收到。
- `SpeechActivityFrame(state="start")` 触发 InterruptFrame，可中断 action 在 300 ms 内 cancel。
- `stop()` 在 SDK worker/sub-agent 跑到一半时被调用，能在 ≤ 1 s 内退出且无 lingering task。

### Slice 2-S2：Wire 协议 + WS App

目标：浏览器 websocket 直接收发 v4 frame；服务端只在 `wire.py` 一处做 dataclass ↔ JSON。

文件：

- `pipeline/wire.py`
- `pipeline/ws_app.py`
- `tests/unit_tests/pipeline/test_wire.py`
- `tests/unit_tests/pipeline/test_ws_app.py`

`pipeline/wire.py` 公开 API：

```python
def encode_frame(frame: Any) -> dict[str, Any]: ...
def decode_inbound(message: dict[str, Any]) -> Any: ...

class WireSerializationError(Exception): ...
class WireDecodeError(Exception): ...
```

| 函数 | 行为 |
|---|---|
| `encode_frame` | 输入任意架构文档定义的出站 frame，输出 `{type, ts_ms, payload}` dict。未识别类型抛 `WireSerializationError`。 |
| `decode_inbound` | 输入 websocket message dict，按 §Wire 协议入站表分派；未知 type 抛 `WireDecodeError`。 |

`pipeline/ws_app.py` 提供：

```python
def build_ws_app(session: RuntimeSession) -> FastAPI: ...
async def run_ws_app(app: FastAPI, *, host: str, port: int, startup_timeout: float) -> None: ...
```

WS endpoint 行为：

- 路径 `/ws/agent`，沿用现有 url（前端不需要改 url）。
- accept 后立即开三个 task：
  - **recv loop**：`receive_json` → `decode_inbound` → `session.submit_*`。
  - **send loop**：`session.subscribe()` 拿到 queue，`encode_frame` → `send_json`。
  - **heartbeat**：每 15 s 发 `ping`，5 s 没收到 `pong` 关闭 socket。
- close 时取消订阅、释放 microphone 缓冲。

验收：

- `test_wire.py`：每条架构文档定义的入站/出站 frame 都有 round-trip 用例（encode→decode→equal 或 decode→encode→equal）。
- `test_ws_app.py`：用 `httpx.AsyncClient` + `websockets`，发 `browser_input(kind="text", payload={"text": "hi"})` 收到 SDK 原生 `sdk_message`，全程不出现旧协议 type。
- runtime 收到 `front_hint_chunk` 等旧 type 时，回 `pipeline_error(reason="legacy_protocol_rejected")` 并关闭 socket。

### Slice 2-S3：前端 JS 重写

目标：`main.js.j2` 与三个 profile 下的 `static/main.js` 全部按 v4 wire 协议重写；不留任何 `front_*` 字符串。

文件：

- `src/reachy_mini/apps/templates/app/static/main.js.j2`
- `profiles/sim_front_app/sim_front_app/static/main.js`
- `profiles/my_app/my_app/static/main.js`
- `profiles/demo_web/demo_web/static/main.js`

UI 行为契约：

| 收到的 frame | UI 处理 |
|---|---|
| `sdk_message` | 对 `AssistantMessage/TextBlock` 追加 assistant 气泡；其它 SDK message 进入 debug/worker/tool 面板。 |
| `action_result` | 在 status 行显示 `{name} {status} {duration_ms}ms`。失败时高亮。 |
| `worker_event` | 在右侧 worker 面板更新对应 `task_id` 的状态行。 |
| `tts_audio` | 用 `AudioContext.decodeAudioData`（pcm wrap 成 wav，或直接 PCM16 喂 `ScriptProcessor` / `AudioWorkletNode`）播放。 |
| `transcription` | `is_final=false` → 流式预览；`is_final=true` → 落入对话区作为 user 气泡。 |
| `vision_event` | debug 区显示。 |
| `pipeline_error` | toast/红色 banner。 |
| `pong` | 心跳确认，不渲染。 |

发送：

| 触发 | 发送 type |
|---|---|
| 文本输入提交 | `browser_input(kind="text", payload={"text": ..., "thread_id": THREAD_ID})` |
| 麦克风采到一帧 | `audio_chunk(pcm_b64, sample_rate, channels)` |
| 麦克风停止 | `audio_stop({})` |
| 浏览器侧 VAD（如启用） | `speech_activity(state="start"/"end")` |
| 心跳 | 每 10 s `ping({})` |

UI 改造范围：

- 删除 `updateStageBubble` 的 `hint`/`final` 双 stage 概念——v4 以 SDK message stream 为准，不再造聚合式 Brain 输出对象。
- `setStatus` 文案随之收敛到一份。
- 保留 `surface_state` 与 `speech_preview` 的显示位置，但事件来源改成 `transcription` 流式预览（surface_state 不在新协议范围；如需要，在 `worker_event` 通道里包一个 `event="surface_state"`）。

> 旧 `surface_state` / `speech_preview` 在 v4 没有等价的 frame。改造期我们认 surface 状态只通过 `worker_event(payload.kind="surface_state")` 这一种途径回传，由 `RuntimeSession` 在 Slice 2-S5 注入。

验收：

- 浏览器开页面、发一句文本 → 收到 `sdk_message`，对话区出现 SDK AssistantMessage/TextBlock 回复。
- 麦克风开 → 浏览器持续发 `audio_chunk`，收到 `transcription` 流式预览 + `sdk_message`。
- 任何 grep `front_hint_chunk|front_final_chunk|front_decision|front_tool_result` 在 `apps/templates/` 与 `profiles/*/static/` 下零命中。
- `ts/eslint`-style 静态扫描（手测）：`main.js` 不再 import / 引用任何旧协议常量。

### Slice 2-S4：Live IO + Daemon / Resident 接入

目标：daemon 启动 app 时构造 `RuntimeSession`，麦克风/扬声器接到 session。

文件：

- `pipeline/live_io.py`
- `apps/app.py`（改）
- `runtime/web.py`（改）
- `daemon/app/main.py`（评估改动）

`pipeline/live_io.py`：

```python
class MicrophoneSource:
    async def start(self, on_frame: Callable[[AudioFrame], Awaitable[None]]) -> None: ...
    async def stop(self) -> None: ...

class SpeakerSink:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def consume(self, frame: TTSAudioFrame) -> None: ...
```

| 字段/方法 | 含义 |
|---|---|
| `MicrophoneSource.start` | 复用 `runtime/audio/*` 的 device 选择；callback 收 `AudioFrame`。 |
| `SpeakerSink.consume` | 复用 `runtime/reply_audio.py` 的播放器；按 `turn_id` 分流 `is_final`。 |
| 两者均要可用 fake | 单测用 `FakeMic`/`FakeSpeaker`。 |

`apps/app.py` 改造：

- 删除 `from reachy_mini.runtime.scheduler import RuntimeScheduler`。
- `build_runtime` 改为构造 `RuntimeSession`，签名收敛：去掉 `provider`/`api_key`/`kernel_*` 等老 override（这些要么走 profile 文件、要么走 `--override` 通用参数）。
- `_build_runtime_microphone_bridge` 替换为 `MicrophoneSource`。
- WebSocket recv/send loop 完全替换：直接调用 `pipeline.ws_app.build_ws_app(session)`。

`runtime/web.py`：

- 删掉 `front_*` 相关的所有路由 / 适配代码（如有）。
- 沿用 `/ws/agent` 与 `/static/*` 路径。

`daemon/app/main.py`：

- 现状是通过 app 间接拿 RuntimeScheduler；改成通过 app 间接拿 RuntimeSession，签名向下游不变。

验收：

- `python -m reachy_mini.runtime.main agent profiles/sim_front_app --message "hi"` 输出 reply。
- `reachy-mini-agent web profiles/sim_front_app` 起 web，浏览器 `/ws/agent` 能跑通文本对话与麦克风音频。
- 旧测试 `tests/unit_tests/test_resident_runtime_host.py`、`test_app.py`、`test_web_agent_runner.py` 改写后绿（断言对象从 `RuntimeScheduler` 换成 `RuntimeSession`，packet type 换成 v4）。

### Slice 2-S5：Surface State 兼容点 + Profile 兼容回归

目标：把 v4 缺的 UI 信号通道补上，并验 profile 兼容性。

文件：

- `pipeline/session.py`（补丁，发 `worker_event(payload.kind="surface_state")`）
- `tests/unit_tests/migration/test_profile_compat_sim_front_app.py`
- `tests/unit_tests/migration/test_profile_compat_generated_app.py`
- `reachy_brain/config.py`（仅补丁）

Surface state 通道：

- `RuntimeSession` 内部维护一个轻量状态机：`idle` / `listening` / `listening_wait` / `replying` / `acting` / `settling`。
- 状态切换时发 `WorkerEventFrame(task_id="__surface__", event="state", payload={"kind":"surface_state","state":{"phase":...}})`。
- 前端把 `worker_event.task_id == "__surface__"` 当 surface_state 处理（在 Slice 2-S3 已对齐）。

测试矩阵：

| 测试 | 输入 | 断言 |
|---|---|---|
| `test_profile_compat_sim_front_app` | 加载 `profiles/sim_front_app` | `from_profile` 不抛错；`AgentConfig.speech_input.provider == "funasr"`；`vision.no_camera` 与 profile 一致；`RuntimeSession.start/stop` 正常 |
| `test_profile_compat_generated_app` | `reachy-mini-agent create __probe__` 后立即加载 | 同上；`submit_text("ping")` 至少收到 1 个 SDK `AssistantMessage` |
| `test_legacy_layers_absent` | — | Slice 2-S6 之后启用：`importlib.util.find_spec("reachy_mini.front") is None` 等。 |

验收：

- 上述两个 profile 测试 + `tests/unit_tests/test_profile_loader.py` 全绿。
- 浏览器 UI 在文本对话过程中能看到 surface_state 切换（`idle` → `replying` → `acting` → `idle`）。

### Slice 2-S6：CLI 切换 + 删除旧层

目标：CLI 默认行为切到 v4，旧层一次删干净。

文件：

- `runtime/main.py`（改写）
- 删除清单（见下表）

CLI 变化：

| 子命令 | 行为 |
|---|---|
| `reachy-mini-agent create` | 不变 |
| `reachy-mini-agent agent <app>` | 走 `RuntimeSession`；删除 `--provider`/`--model`/`--api-key`/`--kernel-*` 等旧 flag；保留 `--message` / `--thread-id` / `--override` |
| `reachy-mini-agent web <app>` | 走 `pipeline.ws_app`；保留 `--host`/`--port`/`--startup-timeout` |
| `reachy-mini-agent v4` | **删除** |

删除清单：

| 路径 | 处理 |
|---|---|
| `src/reachy_mini/front/` | 整目录删除 |
| `src/reachy_mini/companion/intent.py` | 删除 |
| `src/reachy_mini/runtime/scheduler.py` | 删除 |
| `src/reachy_mini/core/agent.py` | 删除 |
| `src/reachy_mini/core/kernel.py` | 删除 |
| `src/reachy_mini/core/_compat.py` | 删除 |
| `src/reachy_mini/core/routing.py` | 起步审计后决议；仅 BrainKernel 引用则删除 |
| `src/reachy_mini/runtime/main.py` | 改写：移除 `--provider`/`--kernel-*`/`v4` 子命令 |
| `src/reachy_mini/companion/__init__.py` | 移除 `intent` re-export |
| `src/reachy_mini/runtime/__init__.py` | 移除 `RuntimeScheduler` re-export |
| `src/reachy_mini/core/__init__.py` | 移除 `BrainKernel` / `AgentHarnessKernel` re-export |
| `src/reachy_mini/__init__.py` | 移除旧 export，新增 `RuntimeSession` / `BrainAgent` / `AgentConfig` / `ActionRegistry` / `ActionExecutor` / `ActionSpec` |

测试调整：

- 删除：`test_front_agent_runner.py`、`test_front_service_runtime.py`、`test_kernel_agent_runner.py`、`test_kernel_tool_execution.py`。
- 改写：`test_runtime_reachy_tools.py`（确认 v4 tool adapter 仍发等价 ActionSpec）、`test_resident_runtime_host.py`、`test_app.py`、`test_web_agent_runner.py`。
- 新增：`test_legacy_layers_absent.py`、`test_v4_default_cli.py`。

公开 API 收敛（`reachy_mini/__init__.py`）：

| Symbol | Phase 2 之后 |
|---|---|
| `ReachyMini` | 保留 |
| `RuntimeScheduler` / `BrainKernel` / `AgentHarnessKernel` | 删除 |
| `RuntimeSession` | 新 export |
| `BrainAgent` / `AgentConfig` | 新 export |
| `ActionRegistry` / `ActionExecutor` / `ActionSpec` | 新 export |

验收：

- v4 主路径 lint 无错误：`pipeline/`、`reachy_brain/`、`action_runtime/` 必须通过 ruff；`apps/` 与 `runtime/` 至少通过非 docstring 规则（历史 runtime 文档风格债不作为 L3/Phase 2 SDK-first 切换的兼容理由）。
- `pytest tests/unit_tests` 全绿。
- `grep -R "RuntimeScheduler\|BrainKernel\|companion\.intent\|reachy_mini\.front\|front_hint_chunk\|front_final_chunk\|front_decision\|front_tool_result" src/ tests/ profiles/ docs/` 仅命中本计划文档自身。
- `reachy-mini-agent agent profiles/sim_front_app --message "hi"` 真实走 Claude Code Python SDK；若本机缺 profile 引用的真实 secret（例如 `DEEPSEEK_API_KEY`），该 smoke 必须失败并暴露环境错误，不能静默注入 offline fake。
- 浏览器 `/ws/agent` 收发 v4 frame。

## Smoke Case 增量

Phase 1 SC-1 ~ SC-4 全部继续运行；Phase 2 在 `tests/unit_tests/migration/` 增加四个新 case。

### SC-5：多轮 session

| 项 | 值 |
|---|---|
| 输入 1 | `submit_text("你好", turn_id=T1)` |
| 输入 2 | T1 完成后 `submit_text("再来一次点头", turn_id=T2)` |
| 期望 | 两个 turn 都产出 SDK `AssistantMessage` 与 `ActionResultFrame(status="ok")`；T2 的 SDK session context 含 T1 事实 |
| 失败信号 | T2 reply 不引用 T1 / T2 阻塞 |

### SC-6：Worker 跨 turn 存活

| 项 | 值 |
|---|---|
| 输入 1 | `submit_text("巡视一圈")` → Claude Code Python SDK 启动 background subagent |
| 输入 2 | 5 s 后 `submit_text("现在几点")` |
| 期望 | T2 的 SDK `AssistantMessage` 在 worker 仍 progress 时产出 |
| 终止 | SDK completion message 原样进入 `WorkerEventFrame.payload`；下一轮 SDK context 能看到 completion summary |

### SC-7：WS 协议合规

| 项 | 值 |
|---|---|
| 输入 | 浏览器假客户端发 `browser_input(kind="text", payload={"text":"hi"})` |
| 期望 | runtime 发 `sdk_message` + `action_result` × n，且不出现任何 legacy inbound/outbound 协议 type |
| 反向 | 假客户端发 `front_hint_chunk` → runtime 发 `pipeline_error(reason="legacy_protocol_rejected")` 并关闭连接 |

### SC-8：CLI 默认是 v4

| 项 | 值 |
|---|---|
| 输入 | `reachy-mini-agent agent profiles/sim_front_app --message hi` |
| 期望 | exit code 0；stdout 含 reply 文本；不含 `DEPRECATION`；进程内不实例化 `BrainKernel`（用 `unittest.mock.patch` 监控） |

四个新 smoke case + Phase 1 的 SC-1~4 全部通过，才允许执行 Slice 2-S6 的删除动作。

## 推荐实施顺序

1. **S1**：`RuntimeSession` + `OutputBus` + 单测。
2. **S2**：`wire.py` + `ws_app.py` + 单测；浏览器侧仍是旧 JS（暂时收不到任何东西，故只跑 backend test）。
3. **S3**：前端 JS 重写；这一步开始浏览器才重新可用。
4. **S4**：`live_io.py` + `apps/app.py` 切到 RuntimeSession + daemon/web 接入；live mic 通。
5. **S5**：surface state 通道补丁 + profile 兼容回归。
6. **SC-5 ~ SC-8 全绿**。
7. **S6**：CLI 改写 + 删除旧层 + 公开 API 收敛 + 文档同步。

不要先删旧层再补 v4 缺口——daemon/web/前端三条路径会同时断线。

## 验证命令

```bash
conda run -n reachy pytest tests/unit_tests/action_runtime
conda run -n reachy pytest tests/unit_tests/reachy_brain
conda run -n reachy pytest tests/unit_tests/pipeline
conda run -n reachy pytest tests/unit_tests/migration
conda run -n reachy pytest tests/unit_tests/test_resident_runtime_host.py
conda run -n reachy pytest tests/unit_tests/test_app.py
conda run -n reachy pytest tests/unit_tests/test_web_agent_runner.py
conda run -n reachy pytest tests/unit_tests/test_runtime_reachy_tools.py
conda run -n reachy pytest tests/unit_tests/test_profile_loader.py
```

静态检查：

```bash
conda run -n reachy ruff check src/reachy_mini/pipeline src/reachy_mini/reachy_brain src/reachy_mini/action_runtime
conda run -n reachy ruff check src/reachy_mini/apps src/reachy_mini/runtime --ignore D
```

CLI 烟测：

```bash
conda run -n reachy reachy-mini-agent agent profiles/sim_front_app --message "hi"
conda run -n reachy reachy-mini-agent web profiles/sim_front_app
```

说明：CLI / web smoke 是真实 SDK 路径验收；本机没有 `DEEPSEEK_API_KEY`
等 profile secret 时应失败并暴露环境错误，不允许用 hidden offline fake 兜底。

旧协议反向断言（必须零命中）：

```bash
grep -RInE "front_hint_chunk|front_final_chunk|front_decision|front_tool_result|RuntimeScheduler|BrainKernel|reachy_mini\.front|companion\.intent" src/ tests/ profiles/
```

## Phase 2 完成定义

同时满足才算完成：

- `RuntimeSession` 覆盖多轮 / live / barge-in / worker。
- `apps/app.py` / `runtime/web.py` / daemon 全部直接使用 `RuntimeSession`，没有 shim、没有 alias。
- 浏览器 ↔ runtime websocket 协议是 v4 frame 序列化，任何旧 `front_*` 字符串在 src/ 与 profiles/ 中零命中。
- `profiles/sim_front_app` 与一个 generated app 在新默认路径下端到端可用（CLI text、Web UI 文本、Web UI 麦克风）。
- `reachy-mini-agent agent` / `web` 默认走 v4；`v4` 子命令已删除；旧 `--provider`/`--kernel-*` flag 已删除。
- SC-1 ~ SC-8 全部通过。
- `front/`、`companion/intent.py`、`runtime/scheduler.py`、`core/agent.py`、`core/kernel.py`、`core/_compat.py` 已删除；`core/routing.py` 决议落地。
- `reachy_mini/__init__.py` 公开 API 收敛到 v4。
- 文档（README、AGENTS.md、docs/source）更新指向 v4。

## Phase 3 触发条件（备忘）

Phase 2 完成后才允许：

- 改造 React `ui/robot-workbench/`（与 app websocket 协议无关，但与 daemon 协议有关）。
- 评估 `core/turns.py` / `core/memory.py` / `core/run_store.py` 是否由 Claude Code Python SDK session APIs 或 profile/app memory 取代；不默认在 L3 新增任务记忆模块。
- 评估 `runtime/speech_session.py` / `runtime/reply_audio.py` 是否在 `live_io.py` 完全覆盖后退役。
- 引入 MCP server / 远端 agent / 多机部署。

Phase 3 不在本计划讨论范围。
