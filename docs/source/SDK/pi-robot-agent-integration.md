# Pi Robot Agent 接入与 L3 后端切换

本文档记录将 **earendil-works/pi** 作为可选 L3 Brain harness 接入 Reachy Mini 的产品决策、目标架构与落地契约。

它与下列文档配套阅读：

| 文档 | 关系 |
|---|---|
| [企业级 RobotRuntime 架构规划](./robot-runtime-enterprise-plan.md) | 身体 System 1：Intent → plan → safety → adapter |
| [V4 大脑与动作架构](./v4-brain-action-architecture.md) | 历史 v4 锁定：L3 = Claude Agent SDK only（见下文差异） |
| 邻居仓库 `D:\work\py\pi\packages\robot-agent\README.md` | Pi extension 包与 runbook |

实现计划原文（会话批准版）备份于：

`C:\Users\Administrator\.claude\plans\crystalline-kindling-wall.md`

---

## 1. Executive Summary

目标：在 **不重写 `RobotRuntime`、不 fork Pi 核心** 的前提下，把 L3 从「仅 Claude Agent SDK」扩展为可切换后端：

```text
claude_sdk  → BrainAgent + 同进程 MCP robot tools
pi_rpc      → PiBinaryBrain + pi --mode rpc + TS robot tools + HTTP bridge
```

产品形态：

- **一个大脑**（single agent）
- **机器人通用 agent**：body-agnostic intent tools + **coding tools 默认开启**
- **身体执行与安全仍在 Python** `RobotRuntime` / `RobotIntentTools`

默认 backend 仍是 **`claude_sdk`**。`pi_rpc` 为 opt-in。

---

## 2. 为什么要接 Pi（产品决策）

| 决策 | 说明 |
|---|---|
| 魔改方向 | 把 Pi 变成 robot general agent，而不是在 Python 再造一套 coding agent |
| 工具落点 | 机器人工具以 **TS 内置 extension** 暴露给模型；不是再叠一层「模型可见 MCP」作为主路径 |
| Coding | Pi builtin coding tools **默认 ON**（companion / builder / ops） |
| 身体边界 | 控身只能走 Intent API；禁止经 bash/文件直驱电机、Live2D 资源、adapter id |
| 安全归属 | safety / schedule / capability resolve 留在 `RobotRuntime` |
| 兼容 | 不删除 Claude 路径；session 可切换 |

这是对 v4「L3 只能是 Claude Agent SDK」合同的 **有意演进**：身体层已按 enterprise plan 收口到 Intent API 后，L3 harness 可以替换，**身体契约不变**。

---

## 3. 目标架构

```text
┌──────────────────────────────────────────────────────────┐
│ RuntimeSession (pipeline)                                │
│  STT/TTS · frames · OutputBus · optional ActionRuntime   │
└───────────────┬────────────────────────────┬─────────────┘
                │ turns                      │ speech/events
                ▼                            ▼
┌───────────────────────────────┐   ┌──────────────────────┐
│ L3 Brain (switchable)         │   │ SpeechPresenter / UI │
│                               │   └──────────────────────┘
│  claude_sdk: BrainAgent       │
│    + MCP reachy_robot         │
│                               │
│  pi_rpc: PiBinaryBrain        │
│    pi --mode rpc -e tools.ts  │
└───────────────┬───────────────┘
                │ emit_embodied_intent / query_*
                │
     ┌──────────┴──────────┐
     │ claude: in-proc MCP │
     │ pi: HTTP :8787      │──► PiToolBridge ──► RobotIntentTools
     └──────────┬──────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│ RobotRuntime (System 1)                                  │
│ intent → policy → schedule → resolve → safety → adapter  │
│ live2d / mujoco / reachy / avatar3d / ros2               │
└──────────────────────────────────────────────────────────┘
```

### 3.1 模块映射

| 职责 | 路径 |
|---|---|
| TS extension 入口 | `pi/packages/robot-agent/extensions/robot-tools.ts` |
| TS Intent tools | `pi/packages/robot-agent/src/tools/*` |
| TS HTTP/mock bridge | `pi/packages/robot-agent/src/bridge/*` |
| Python bridge 路由 | `reachy_mini/reachy_brain/pi_tool_bridge.py` |
| Python bridge 服务 | `reachy_mini/reachy_brain/pi_tool_bridge_server.py` |
| Pi JSONL RPC 客户端 | `reachy_mini/reachy_brain/pi_rpc_client.py` |
| Brain 宿主 | `reachy_mini/reachy_brain/pi_binary_brain.py` |
| Session 切换 | `reachy_mini/pipeline/session.py` |
| Intent API | `reachy_mini/robot_runtime/tools.py` |

---

## 4. Backend 解析

`RuntimeSession.from_profile` / `_resolve_brain_backend` 优先级：

1. overrides：`brain.backend` 或 `brain_backend`
2. 环境变量：`REACHY_BRAIN_BACKEND`
3. profile extras 中 `brain` / `agent` 记录的 `backend`
4. 默认：`claude_sdk`

合法值：

- `claude_sdk` — 现有 Claude Agent SDK 路径  
- `pi_rpc` — Pi 子进程 RPC 路径  

---

## 5. 环境变量

| 变量 | 含义 | 默认 / 示例 |
|---|---|---|
| `REACHY_BRAIN_BACKEND` | brain 后端 | `claude_sdk` |
| `REACHY_PI_BIN` | pi 可执行文件 | `pi`（Windows 建议 `...\npm\pi.cmd`） |
| `REACHY_PI_EXT` | robot-tools 扩展路径 | 候选含 `D:\work\py\pi\...\robot-tools.ts` |
| `REACHY_RUNTIME_URL` | 给 Pi 子进程的 bridge URL | `http://127.0.0.1:8787` |
| `REACHY_RUNTIME_HOST` / `PORT` | bridge 监听 | `127.0.0.1` / `8787` |
| `REACHY_RUNTIME_TOKEN` | 可选 Bearer | 空则不校验 |
| `REACHY_BRIDGE_MODE` | TS 侧 mock/http | host 注入 `http` |

模型 key / base_url 仍来自 profile `kind:model`（`AgentConfig`），由 `PiBinaryBrain` 写入子进程 env（`ANTHROPIC_*` / `OPENAI_*` 等）。

**Claude Code / 自定义网关注意：**

| 点 | 说明 |
|---|---|
| Auth | Claude Code 常用 `ANTHROPIC_AUTH_TOKEN`（Bearer）。`PiBinaryBrain` 会同时注入 `ANTHROPIC_AUTH_TOKEN` 与 `ANTHROPIC_API_KEY`。 |
| Base URL | Pi **不会**只靠 `ANTHROPIC_BASE_URL` 改 endpoint。自定义网关 / 非目录模型（如 `grok-4.5`）时，宿主会临时写 `PI_CODING_AGENT_DIR/models.json`（`prepare_pi_agent_dir_for_model`）。 |
| 代理 | 继承父进程 `HTTP(S)_PROXY` / `NO_PROXY`（若已设置）。 |
| Speech | 仅 `text_delta` 进气泡；`thinking_delta` 不进 TTS/Live2D。 |

---

## 6. HTTP Intent 契约（Pi TS ↔ Python）

| Method | Path | Python |
|---|---|---|
| GET | `/health`, `/robot/health` | health |
| POST | `/robot/emit_embodied_intent` | `RobotIntentTools.emit_embodied_intent` |
| GET | `/robot/state` | `query_robot_state` |
| GET | `/robot/trace` | `query_robot_trace` |
| GET | `/robot/metrics` | `query_robot_metrics` |
| GET | `/robot/behavior_tree` | `query_robot_behavior_tree` |
| GET | `/robot/structured_log?limit=` | `query_robot_structured_log` |
| POST | `/robot/request_task` | `request_robot_task` |
| POST | `/robot/cancel_task` | `cancel_robot_task` |

约定：

- body 缺 `turn_id` 时，bridge 注入当前 turn  
- 可选 `Authorization: Bearer <token>`  
- body-agnostic 校验在 Python `RobotIntentTools`（TS 侧另有快拒）

---

## 7. 生命周期

### `pi_rpc` start 顺序

1. register robot adapters  
2. `RobotRuntime.start()`  
3. `PiToolBridgeServer.start()`（绑定 `RobotIntentTools`）  
4. `PiBinaryBrain.start()` → 拉起 `pi --mode rpc`  
5. Pipecat / 其它 pipeline 组件  

### stop 顺序

pipecat → brain → tool bridge → robot runtime → bus close  

### `pi_rpc` 工具策略

- **不**再 dual-mount Claude MCP `reachy_robot`  
- robot tools 只在 TS extension 注册  
- coding tools 不在 Python 侧禁用（不传 `--no-builtin-tools`）  

---

## 8. Pipeline 兼容

- `BrainProcessor` 依赖 `agent.run_turn`，不绑死 `BrainAgent` 类型  
- `PiBinaryBrain` yield duck-type `AssistantMessage` / `TextBlock`（按 **类名** 兼容 speech 提取）  
- `pipecat_bridge.extract_text_blocks` 接受 Pi duck-type  
- 重型 import（session / pipecat）对轻量模块 lazy，避免循环依赖  

Windows 注意：`asyncio.create_subprocess_exec("pi")` 可能 `FileNotFoundError`。  
`pi_rpc_client.resolve_pi_executable` 会解析到 `shutil.which("pi")`（通常为 `pi.CMD`）。

---

## 9. 与 v4 文档的差异

| v4 原文（历史） | 现状 |
|---|---|
| L3 source of truth **只能** Claude Agent SDK | L3 **可切换**：默认 Claude，opt-in Pi |
| 机器人工具走 SDK MCP | Claude 路径仍可 MCP；Pi 路径走 TS+HTTP |
| ActionRuntime 为一等动作落点 | Intent 模式下身体主路径是 `RobotRuntime`；Action 为遗留/并行 |

企业级身体契约（Intent / safety / multi-body）以 `robot-runtime-enterprise-plan.md` 为准。  
L3 harness 选择以 **本文 + session 实现** 为准。

---

## 10. 运行与验收

### 软冒烟（不启 Pi 推理）

```bash
cd D:\work\py\reachy_mini
python scripts/smoke_pi_robot_brain.py
```

### 单测

```bash
pytest tests/unit_tests/reachy_brain/test_pi_rpc_client.py \
       tests/unit_tests/reachy_brain/test_pi_tool_bridge.py -q
```

### 启用 Pi brain session

```bash
set REACHY_BRAIN_BACKEND=pi_rpc
set REACHY_PI_BIN=%APPDATA%\npm\pi.cmd
set REACHY_PI_EXT=D:\work\py\pi\packages\robot-agent\extensions\robot-tools.ts
set REACHY_RUNTIME_URL=http://127.0.0.1:8787
# profile: robot_runtime.enabled=true, intent_tools_enabled=true
```

### 验收清单

1. extension mock 下 pi 能注册 8 个 robot tools，coding tools 仍在  
2. bridge `/health` + `emit_embodied_intent` 到达 `RobotIntentTools`  
3. `PiBinaryBrain.start()` 能拉起 RPC 进程（Windows 含 `pi.cmd` 解析）  
4. session 可在 `pi_rpc` / `claude_sdk` 间切换  
5. 有模型 key 时：文本 turn 可产生 intent 事件并落到 adapter（如 Live2D greet）  
6. 相关单测通过  

---

## 11. 明确非目标

- fork 修改 Pi 核心默认工具策略  
- 把 `RobotRuntime` 重写为 TypeScript  
- 第一阶段做独立 `robot-agent` 发行 binary 品牌壳  
- 多 agent 编排  
- 删除 Claude Agent SDK 路径  

---

## 12. 状态（实现跟踪）

| 项 | 状态 |
|---|---|
| robot-agent package | 已落地（邻居 pi 仓库） |
| Python bridge + RPC host + session switch | 工作区已实现（以 git 为准） |
| 单元测试 / soft smoke | 已有 |
| Windows `pi` 可执行解析 | 已修（`resolve_pi_executable`） |
| 真模型 E2E greet（Claude 网关 + grok-4.5） | 已通：`intent_count=1`，`emit_embodied_intent` 成功 |
| Live2D body 全链路（headless adapter） | 已通：注册 `Live2DAdapter` 后 `adapter_success_count>=1`，frame=`live2d_motion/HuiShou`，`capability_unresolved=0` |
| 合入默认 profile | 未默认；需 env/profile 显式开启 |
| 浏览器可见 Live2D 渲染 | 需完整 sim_front_app session + 前端；headless E2E 不覆盖 |
