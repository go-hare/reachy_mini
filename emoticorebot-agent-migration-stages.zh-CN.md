# Reachy Mini 接入 emoticorebot 分阶段改造执行文档

## 1. 文档定位

本文档是 `D:/work/py/reachy_mini` 的阶段性改造执行文档。

它不重复展开完整架构设计，而是回答下面几个问题：

- 先做什么
- 后做什么
- 每个阶段的边界是什么
- 每个阶段的交付物是什么
- 到什么程度算这个阶段完成

对应的架构基线、模块取舍和设计背景，见：

- `emoticorebot-agent-migration.zh-CN.md`

对话通道与 WebSocket 事件流的补充决策，见：

- `emoticorebot-agent-dialogue-websocket.zh-CN.md`

目录命名目标统一为：

- `src/reachy_mini/apps/`
- `src/reachy_mini/runtime/`
- `src/reachy_mini/core/`

其中当前实现里的 `agent_runtime/` 与 `agent_core/` 视为过渡命名。

## 2. 当前已确认基线

在进入分阶段执行前，以下事项已经确认：

1. 新架构同时保留两套理解口径：
   - 产品口径：
     - `UI + front + kernel + robot runtime`
   - 运行时细化口径：
     - 信息输入层
     - `front` 外显层
     - `kernel` 任务层
     - 执行协调层
     - 身体输出层
     - App/Profile 配置面
2. `reachy_mini` 继续承担身体输出层的底座职责：
   - SDK
   - daemon
   - motion
   - media
   - io
   - app 生命周期
3. `UI` 只是产品壳层：
   - 负责 app 创建、配置、会话展示和机器人模拟
   - 不承担核心智能决策
4. 旧 conversation / realtime 主脑不再作为未来主干保留。
5. 新系统只保留一个主脑体系：`emoticorebot`。
6. `front` 已被明确为“外显大脑”：
   - 负责外显复杂任务
   - 负责陪伴感、情绪、外显状态和外显工具决策
   - 后续要对齐的“原版能力”，核心就是这一层
7. `kernel` 已被明确为“任务大脑”：
   - 负责推理、记忆、任务拆解、任务工具调用和事实结果生成
   - 负责 Codex 风格的命令、工具、run 生命周期
   - 负责多任务调度和任务状态管理
8. 工具归属已经明确：
   - 外显类工具归 `front`
   - 任务类工具归 `kernel`
9. `profiles/<name>/` 不再只是旧 conversation 的提示词目录，而是用户创建的 app 项目目录。
10. 当前 app 项目的标准目录形态为：
   - `profiles/<name>/README.md`
   - `profiles/<name>/pyproject.toml`
   - `profiles/<name>/.gitignore`
   - `profiles/<name>/index.html`
   - `profiles/<name>/style.css`
   - `profiles/<name>/<name>/main.py`
   - `profiles/<name>/<name>/static/`
   - `profiles/<name>/profiles/AGENTS.md`
   - `profiles/<name>/profiles/USER.md`
   - `profiles/<name>/profiles/SOUL.md`
   - `profiles/<name>/profiles/TOOLS.md`
   - `profiles/<name>/profiles/FRONT.md`
   - `profiles/<name>/profiles/config.jsonl`
   - `profiles/<name>/profiles/memory/`
   - `profiles/<name>/profiles/skills/`
   - `profiles/<name>/profiles/session/`
   - `profiles/<name>/profiles/tools/`
   - `profiles/<name>/profiles/prompts/`
11. `profile` 结构中不包含 `HEARTBEAT.md`。
12. `reachy-mini-app-assistant` 的 `check` / `publish` 命令已经不再作为主路径。
13. `emoticorebot` 的 `desktop / desktop-dev` 入口暂不纳入首批迁移主路径。
14. 当前代码现实是：
   - resident runtime 文本主链路已经存在
   - `BrainKernel` 已经接入
   - `surface_state` 已经能产出
   - `MovementManager`、`CameraWorker`、`HeadWobbler` 已有第一版接线
   - 但 `front` 还没有真正长成接近原版的外显大脑
   - 外显类工具与任务类工具也还没有彻底完成职责拆分

## 3. 总体阶段图

本次改造建议收口为六个阶段：

| 阶段 | 名称 | 核心目标 | 当前状态 |
|------|------|----------|----------|
| 0 | 基线冻结 | 固定方向、分层、工具归属、文档口径 | 已完成 |
| 1 | Resident Runtime 与 App/Profile 收口 | 理顺 app 文件包、CLI、profile loader、resident runtime 主路径 | 已完成第一版 |
| 2 | 文本双脑闭环 | 跑通 `app 文件包 -> front -> kernel -> front` 的文本主链路 | 已完成第一版 |
| 3 | Front 外显大脑建设 | 让 `front` 向原版外显能力靠近，并承接外显类工具 | 已完成第一版 |
| 4 | 执行协调层与身体输出接回 | 建立 coordinator / driver，把外显决策稳定落到机器人身体 | 已开始第一段 |
| 5 | 旧资产迁移与主干收口 | 迁移剩余 legacy 资产，清理旧路径，完成语义收口 | 后续阶段 |

## 4. 阶段 0：基线冻结

### 4.1 目标

- 固定“Reachy 做身体，emoticorebot 做双脑体系”的主方向
- 固定 `UI + front + kernel + robot runtime` 的产品口径
- 固定 `5` 层 + `1` 配置面的运行时细化模型
- 固定 `UI` / `front` / `kernel` / 协调层 / 身体层的职责边界
- 固定外显工具与任务工具的归属

### 4.2 本阶段交付物

- 主设计文档：
  - `emoticorebot-agent-migration.zh-CN.md`
- 分阶段执行文档：
  - `emoticorebot-agent-migration-stages.zh-CN.md`

### 4.3 本阶段验收标准

- 团队对最终目标没有方向性歧义
- 分层口径已经固定
- `front` / `kernel` 边界已明确
- “原版能力对齐”被明确解释为 `front` 外显能力对齐
- 外显工具归 `front`、任务工具归 `kernel` 已成为统一基线

## 5. 阶段 1：Resident Runtime 与 App/Profile 收口

### 5.1 目标

先把 app 文件包、CLI 和 resident runtime 主路径理顺，让新系统有稳定宿主。

核心目标：

- 将 `profiles/<name>/` 明确为用户创建的 app 项目目录
- 将 `profiles/<name>/profiles/` 明确为 app 内部的 profile 文件包
- 让 `profile loader` 面向这套 app 文件包结构工作
- 用 `reachy-mini-agent create/agent/web` 作为 `UI` / CLI 壳层与 resident runtime 主路径
- 让 `kernel` 承担 Codex 风格的命令、工具和 run 生命周期宿主职责
- 退役旧 app 发布流相关 CLI

### 5.2 本阶段做什么

- 整理 `profiles/` 目录约定
- 收口 `profile loader` 的输入与输出边界
- 建立 resident runtime 启动链路
- 清理 `check / publish` 与旧发布流的主路径表述

### 5.3 本阶段交付物

- 新 app 项目目录约定落地
- `profile loader` 的新职责说明
- resident runtime 主入口落地
- CLI 口径统一

### 5.4 本阶段验收标准

- `profiles/<name>/` 项目结构已经成为主文档的一部分
- `reachy-mini-agent create/agent/web` 成为正式入口
- 仓库不再把 `check / publish` 当作主路径

### 5.5 当前实现状态

截至 2026-03-27，阶段 1 已完成第一版：

- 已新增 `profiles/<name>/` app 项目初始化能力
- app 内部 profile 文件包位于 `profiles/<name>/profiles/`
- 已新增 `reachy-mini-agent` CLI
- `kernel` 已作为 resident runtime 的任务宿主进入主路径
- 当前主命令形态为：
  - `reachy-mini-agent create <app_name>`
  - `reachy-mini-agent agent <app_name|app_path>`
  - `reachy-mini-agent web <app_name|app_path>`

## 6. 阶段 2：文本双脑闭环

### 6.1 目标

跑通最小可用的新脑子链路：

`app 文件包 -> front -> BrainKernel -> front`

这一阶段的目标不是让机器人已经“像原版一样活”，而是先让文本双脑稳定工作。

### 6.2 本阶段做什么

- 建立 `front` 与 `kernel` 的文本主链路
- 让 profile 真正驱动 `front`、`kernel` 和 memory
- 让 resident runtime 以常驻方式运行 `BrainKernel`
- 跑通文本级前台接住、内核处理、最终呈现

### 6.3 本阶段交付物

- `front` 已参与前台回复
- `BrainKernel` 已参与主决策
- 文本主链路稳定
- app 文件包能真正影响当前 agent 的行为与配置

### 6.4 本阶段验收标准

- 文本输入已由新 resident runtime 接管
- 文本回复已经过 `front -> kernel -> front`
- 旧 realtime 主流程已退出文本主链路

### 6.5 当前实现状态

截至 2026-03-27，阶段 2 已完成第一版：

- 已跑通 `app 文件包 -> front -> BrainKernel -> front`
- `BrainKernel` 已直接使用 app 文件包下的：
  - `memory/`
  - `session/`
  - `USER.md`
  - `SOUL.md`
  - `TOOLS.md`
- resident runtime scheduler 已接入
- `web` 启动方式已可在不连硬件时跑浏览器 UI 和 `/ws/agent`
- `config.jsonl` 的模型配置当前统一使用 `api_key`

## 7. 阶段 3：Front 外显大脑建设

### 7.1 目标

这是当前推荐直接进入的下一阶段。

核心目标不是继续增强 `kernel`，而是让 `front` 向原版的外显会话能力靠近：

- 会听
- 会等
- 会接话
- 会在说话时动起来
- 会在 idle 时自然待着
- 会把动作、情绪、陪伴感组织成一套持续外显能力

### 7.2 本阶段做什么

- 让 `front` 从“文本包装层”升级为“外显导演层”
- 明确 `front` 的外显决策输出：
  - 外显回复
  - 外显状态切换
  - 外显类工具调用
- 将外显类工具从当前 `kernel` 默认工具平面中拆出来，逐步迁到 `front`
- 让 `front` 开始消费与外显相关的信息输入事件，例如：
  - 用户说话开始/结束
  - 助手语音开始/音频 delta/结束
  - idle tick
  - 视觉/关注点变化
- 形成接近原版的“实时外显会话层”雏形

### 7.3 本阶段不做什么

- 不追求一次性完成所有身体调优
- 不在这一阶段解决全部 legacy 资产迁移
- 不接入 `desktop / desktop-dev`

### 7.4 建议落点

- `src/reachy_mini/front/service.py`
- `src/reachy_mini/front/prompt.py`
- `src/reachy_mini/runtime/scheduler.py`
- `src/reachy_mini/runtime/tool_loader.py`
- `src/reachy_mini/apps/app.py`
- 视实现需要新增：
  - `src/reachy_mini/front/tool_registry.py`
  - 或等价的 `front` 外显工具装配层

### 7.5 本阶段交付物

- `front` 不再只是文本润色层
- `front` 拥有外显状态和外显动作决策能力
- 外显类工具有独立于 `kernel` 的归属模型
- 当前系统开始具备“像原版一样会表现”的基础

### 7.6 本阶段验收标准

- `front` 已能稳定驱动 listening / replying / settling / idle 的外显状态
- 外显类工具不再继续作为 `kernel` 的默认决策工具集混用
- “原版能力对齐”开始体现在 `front` 行为上，而不只是文档描述

### 7.7 当前代码现实与 Stage 3 的真正缺口

截至 2026-03-27，当前代码里与 Stage 3 最相关的现实是：

1. `front` 当前仍主要是文本层
   - `src/reachy_mini/front/service.py`
   - 目前核心能力仍集中在：
     - `reply(...)`
     - `present(...)`
     - `run(...)`
   - 也就是说，它现在更像“文本前台”，还不是“外显导演层”

2. `kernel` 当前仍默认持有全部系统工具
   - `src/reachy_mini/runtime/scheduler.py`
   - 通过 `build_runtime_tool_bundle(...)` 把工具装进 `BrainKernel`
   - 当前 Reachy 机器人工具仍混在 `build_system_tools(...)` 里

3. 外显类工具与任务类工具还没有完成拆分
   - `src/reachy_mini/runtime/tools/__init__.py`
   - 当前文件工具与 Reachy 外显工具仍在同一个系统工具集合中

4. `front` 还没有显式事件入口
   - 当前还没有“用户说话开始/结束、助手音频开始/结束、idle tick、视觉关注变化”这类统一的 `front` 事件消费面

所以，Stage 3 的真正任务不是“继续给 front 加提示词”，而是：

- 给 `front` 增加外显决策接口
- 把外显类工具从 `kernel` 的默认工具平面中拆出来
- 让 `front` 开始消费实时外显事件

### 7.8 Stage 3 具体实现清单

建议按下面五个子任务落地。

#### 7.8.1 子任务 A：拆分工具平面

目标：

- 让 `kernel` 只保留任务类工具
- 让 `front` 拥有独立的外显类工具集合

建议动作：

- 在 `src/reachy_mini/runtime/tool_loader.py` 中，把当前单一 `RuntimeToolBundle` 拆成两类语义：
  - `kernel_tools`
  - `front_tools`
- 在 `src/reachy_mini/runtime/tools/__init__.py` 中，拆分当前 `build_system_tools(...)`：
  - `build_kernel_system_tools(...)`
  - `build_front_system_tools(...)`
- 将下面这些工具先视为默认外显类工具：
  - `move_head`
  - `do_nothing`
  - `head_tracking`
  - `camera`
  - `play_emotion`
  - `dance`
  - `stop_emotion`
  - `stop_dance`
- 将文件、工作区、system、exec、mcp、web 等工具保留在 `kernel`

完成信号：

- `BrainKernel` 的默认工具集里不再混入外显类 Reachy 工具
- `front` 已能拿到一组明确的外显工具引用

#### 7.8.2 子任务 B：给 `front` 增加外显事件接口

目标：

- 让 `front` 能像原版的实时会话层一样，消费“会影响外显表现”的事件

建议动作：

- 在 `src/reachy_mini/front/` 下建立一个轻量事件模型，形式可以是：
  - `front/events.py`
  - 或 `front/signals.py`
- 第一批事件先收口为：
  - `user_speech_started`
  - `user_speech_stopped`
  - `assistant_audio_started`
  - `assistant_audio_delta`
  - `assistant_audio_finished`
  - `idle_tick`
  - `vision_attention_updated`
  - `turn_started`
  - `turn_settling`
- `front` 不一定要直接消费原始底层对象，建议先统一成轻量 dataclass / dict 事件

完成信号：

- `front` 不再只吃 `user_text + kernel_output`
- `front` 已经具备消费实时外显事件的正式入口

#### 7.8.3 子任务 C：给 `front` 增加外显决策 API

目标：

- 让 `FrontService` 不只是生成文本，还能产出外显动作决策

建议动作：

- 在 `src/reachy_mini/front/service.py` 中新增面向外显层的方法，形式可以是：
  - `plan_expression(...)`
  - `handle_signal(...)`
  - `decide_front_action(...)`
  - 或等价命名
- 这些 API 的输出建议至少能表达三类东西：
  - 文本相关决策
  - 状态相关决策
  - 工具相关决策
- 第一版不必上复杂 planner，先允许规则优先、模型辅助

建议的第一版输出结构：

- `reply_text`
- `surface_patch`
- `tool_calls`
- `lifecycle_state`
- `debug_reason`

完成信号：

- `FrontService` 具备“外显判断”而不只是“文字润色”
- 同一轮 `front` 能同时给出文本和外显动作意图

#### 7.8.4 子任务 D：在 runtime 中接线

目标：

- 让 resident runtime 把输入事件、front 外显决策、kernel 任务结果串起来

建议动作：

- 在 `src/reachy_mini/runtime/scheduler.py` 中增加 `front` 事件分发与外显输出通道
- 在 `src/reachy_mini/apps/app.py` 中为 WebSocket / resident runtime 增加更明确的外显事件入口
- 第一版即使前端还没全发这些事件，也应先把 runtime 内部事件接起来：
  - turn started
  - listening state entered
  - kernel final received
  - settling entered
  - idle entered

建议原则：

- 不让 `front` 直接控制机器人 SDK
- 只让 `front` 产出“意图”
- 真正执行仍留给后续协调层

完成信号：

- runtime 已能在一轮对话里同时承载：
  - `front` 文本输出
  - `front` 外显状态输出
  - `front` 外显工具输出
  - `kernel` 任务结果输出

#### 7.8.5 子任务 E：补测试与观测

目标：

- 避免 Stage 3 做成只能靠肉眼试的“黑盒手感工程”

建议动作：

- 增加 `front` 外显事件与输出的单元测试
- 增加“外显类工具已从 kernel 工具集剥离”的测试
- 增加 runtime 对 `front` 外显输出分发的测试

建议优先新增的测试文件：

- `tests/unit_tests/test_front_expression_decision.py`
- `tests/unit_tests/test_front_tool_split.py`
- `tests/unit_tests/test_runtime_front_expression_events.py`

完成信号：

- Stage 3 的关键行为可以通过测试验证
- 不再只能靠“跑起来感觉像不像原版”判断

### 7.9 Stage 3 推荐实施顺序

建议严格按下面顺序推进：

1. 先做工具平面拆分
   - 先把“谁能调哪些工具”定清楚
2. 再做 `front` 事件模型
   - 先把外显事件入口定清楚
3. 再做 `FrontService` 外显决策 API
   - 让 `front` 真正开始产出外显意图
4. 再做 runtime 接线
   - 把事件和输出挂到 resident runtime 里
5. 最后补测试
   - 防止后面 Stage 4 改协调层时把 Stage 3 逻辑冲掉

### 7.10 Stage 3 完成后的状态判断

如果 Stage 3 做完，系统应该达到下面这个状态：

- `front` 已经像原版那样开始承担“外显会话层”的职责
- `kernel` 已经从外显工具调度中退出主决策平面
- runtime 已经能够同时承载“文本表现”和“外显表现”
- 但身体执行仍然是下一阶段的重点

也就是说，Stage 3 完成后，系统会先变成：

- “会决定怎么表现”

而 Stage 4 负责把它继续变成：

- “真的能稳定表现出来”

### 7.11 Stage 3 的原版对照来源

在真正动手实现 Stage 3 之前，已经再次确认过原版 `reachy_mini_conversation_app`。

这一步的结论是：

- Stage 3 不应该先凭空设计
- 应该优先把原版里“外显会话层”真正有效的语义抽出来
- 然后再按当前新架构重写

也就是说：

- 先借原版的“能力形状”
- 不照搬原版的“协议壳”

### 7.12 原版里可以直接借语义的部分

#### 7.12.1 原版外显工具本体

下面这些文件都很薄，主要价值在于：

- 工具名
- 参数语义
- 动作边界
- 返回结构

它们不强绑定复杂会话协议，因此最适合先作为 Stage 3 的“外显工具原型来源”：

- `move_head.py`
- `play_emotion.py`
- `dance.py`
- `camera.py`
- `head_tracking.py`
- `do_nothing.py`

这些文件当前位于：

- `C:/Users/Administrator/Downloads/reachy_mini_conversation_app-main/reachy_mini_conversation_app-main/src/reachy_mini_conversation_app/tools/`

建议迁移原则：

- 保留工具名
- 保留参数 schema 语义
- 保留动作能力边界
- 不照搬旧 import 路径
- 不继续挂在旧 `core_tools.py` 上
- 改挂到新的 `front` 外显工具平面

#### 7.12.2 原版外显事件语义

下面这些事件处理段是 Stage 3 最值得直接借语义的来源：

1. 用户开始说话：
   - `input_audio_buffer.speech_started`
   - 原版动作：
     - 清理队列
     - `head_wobbler.reset()`
     - `movement_manager.set_listening(True)`

2. 用户停止说话：
   - `input_audio_buffer.speech_stopped`
   - 原版动作：
     - `movement_manager.set_listening(False)`

3. 助手语音输出：
   - `response.output_audio.delta`
   - 原版动作：
     - `head_wobbler.feed(event.delta)`
     - 更新活动时间

4. idle 行为触发：
   - `send_idle_signal(...)`
   - 原版动作：
     - 通过一条 idle 提示把“可以跳舞、做情绪、看周围、什么都不做”显式交给外显层
     - 并强制 `tool_choice="required"`

这些逻辑当前主要在：

- `C:/Users/Administrator/Downloads/reachy_mini_conversation_app-main/reachy_mini_conversation_app-main/src/reachy_mini_conversation_app/openai_realtime.py`

建议在 Stage 3 中先复用这些“事件 -> 外显状态/工具意图”的语义，而不是先自己重新猜一套。

#### 7.12.3 原版人格入口

原版默认人格入口主要来自：

- `prompts/default_prompt.txt`

它的价值不在于文本内容本身，而在于它说明了一件事：

- 原版的人格提示直接把“工具与动作规则”写进了第一层会话人格里

这和我们现在“让 `front` 成为外显大脑”的方向是一致的。

所以 Stage 3 可以借的不是原文案，而是这条原则：

- 外显规则应该进入 `front`
- 不应该继续只写在 `kernel` 的工具策略里

### 7.13 原版里不要直接照搬的部分

下面这些内容虽然重要，但不适合 Stage 3 直接复制：

1. `openai_realtime.py` 整体
   - 它绑定 OpenAI Realtime 协议
   - 当前项目不应把协议壳再搬回来

2. `core_tools.py`
   - 它绑定旧 `tools.txt`
   - 也绑定旧动态发现与 dispatch 方式
   - 当前项目已经是 `runtime/tool_loader.py + profiles/<name>/profiles/tools/`

3. `background_tool_manager.py`
   - 语义可以借
   - 但不适合 Stage 3 直接照搬
   - 因为当前首要任务是外显层建设，不是重建旧后台工具状态机

### 7.14 Stage 3 的原版优先复用顺序

建议按下面顺序借原版：

1. 先借外显事件语义
   - `speech_started`
   - `speech_stopped`
   - `output_audio.delta`
   - `idle signal`

2. 再借薄工具本体语义
   - `move_head`
   - `play_emotion`
   - `dance`
   - `camera`
   - `head_tracking`
   - `do_nothing`

3. 最后才借人格提示组织方式
   - 把外显规则写进 `front`

不建议的顺序是：

- 先照搬旧 realtime 外壳
- 或先照搬旧工具管理系统

### 7.15 Stage 3 现在可以明确的一句话策略

Stage 3 的策略已经可以收口成一句话：

- 用原版的外显事件语义和外显工具语义，重写当前项目的 `front` 外显层

而不是：

- 把原版整个 conversation app 直接嵌回当前项目

## 8. 阶段 4：执行协调层与身体输出接回

### 8.1 目标

在 `front` 已具备外显决策能力的前提下，把这些决策稳定落到 Reachy 身体。

核心目标：

- 建立统一协调入口
- 让 `front` 外显决策、`kernel` 任务结果、实时音频/视觉信号不互相打架
- 让机器人真正表现出 listening / replying / settling / idle 的连续状态

### 8.2 本阶段做什么

- 建立 `EmbodimentCoordinator`
- 建立 `surface_driver.py`
- 建立 `speech_driver.py`
- 扩展 `MovementManager`，承接连续姿态与显式动作的组合
- 接通：
  - `surface_state -> 姿态基线`
  - `front` 外显工具 -> 显式动作
  - assistant audio delta -> `HeadWobbler`
  - camera / tracking -> 次级偏移

### 8.3 本阶段建议落点

- `src/reachy_mini/runtime/embodiment/coordinator.py`
- `src/reachy_mini/runtime/surface_driver.py`
- `src/reachy_mini/runtime/speech_driver.py`
- `src/reachy_mini/runtime/moves.py`
- `src/reachy_mini/apps/app.py`
- `src/reachy_mini/runtime/scheduler.py`

### 8.4 本阶段交付物

- 统一的执行协调入口
- `surface_state`、外显工具、音频 wobble、视觉 tracking 的组合执行链
- Reachy 身体的阶段切换反馈

### 8.5 本阶段验收标准

- `listening / replying / settling / idle` 在机器人身体上已可观测
- 基本动作反馈自然且不明显抖动
- 说话、等待、空闲时的外显感已经开始接近原版体感

### 8.6 当前已具备的基础

截至 2026-03-27，下面这些底座已经存在：

- `surface_state` 已经能产出
- `MovementManager` 已接入第一版
- `CameraWorker` 已接入第一版
- `HeadWobbler` 已接入第一版
- `surface_driver.py` 第一版已建立，并已接通 `surface_state -> MovementManager listening/activity`
- `surface_driver.py` 当前已包含一层极薄的 `thread_id` 状态收束，用于避免多会话下最后写入直接覆盖身体状态
- `speech_driver.py` 第一版已建立，并已把 assistant audio delta / reset 语义正式收口到 `HeadWobbler`
- `speech_driver.py` 现在已开始正式管理 `HeadWobbler` 生命周期，并在 `replying` 阶段按音频新鲜度自动回收残留 speech motion
- `EmbodimentCoordinator` 第一版已建立，开始统一承接 `surface_driver + speech_driver`
- `front` 外显工具已开始优先通过 `EmbodimentCoordinator` 执行，不再全部直接下沉到 `movement_manager / camera_worker`
- coordinator 已具备第一条仲裁规则：显式动作窗口内暂停 head tracking，并清掉残留 speech motion，结束后再恢复期望 tracking 状态
- coordinator 已补入第二条仲裁规则：显式动作优先级暂定为 `move_head > emotion > dance`，高优先级可清队列抢占低优先级，低优先级在强动作窗口内会被延后
- `ReachyMiniApp` 的 WebSocket 与 `app.chat()` 已统一走同一条 `surface_state` 身体入口

当前缺的是：

- 更完整的 `speech_driver` 执行策略
- `front` 外显工具到身体执行层的更完整桥接
- 更细的 coordinator 仲裁策略与动作编排

## 9. 阶段 5：旧资产迁移与主干收口

### 9.1 目标

在新分层稳定后，再迁移剩余 legacy 资产，并完成旧路径语义上的退场。

### 9.2 本阶段做什么

- 继续迁移旧 conversation app 中仍有价值但尚未正式吸收的资产
- 按新分层重新分类 legacy 工具：
  - 外显类工具并入 `front`
  - 任务类工具并入 `kernel`
  - 后台工具状态模型按需重写，而不是直接照搬
- 迁移仍有价值的 prompts / profile 素材
- 清理旧 `fork_conversation` 和旧 realtime 主流程语义

### 9.3 旧 tools 迁移规则

针对旧 `reachy_mini_conversation_app/tools/`，建议按三组处理：

第一组：不要直接并入当前主运行时

- `__init__.py`
- `core_tools.py`

第二组：保留语义，但后置重写

- `background_tool_manager.py`
- `task_status.py`
- `task_cancel.py`
- `tool_constants.py`

第三组：优先按新分层吸收的机器人能力工具

- `move_head.py`
- `play_emotion.py`
- `dance.py`
- `camera.py`
- `head_tracking.py`
- `stop_dance.py`
- `stop_emotion.py`
- `do_nothing.py`

### 9.4 本阶段交付物

- legacy 资产完成按层归位
- 旧主脑路径彻底退出主干
- 项目语义稳定为“Reachy Mini 身体 + emoticorebot 双脑体系”

### 9.5 本阶段验收标准

- 外显类工具已经稳定归 `front`
- 任务类工具已经稳定归 `kernel`
- 协调层与身体层成为新的唯一执行主路径
- 项目不再依赖旧 realtime 主流程

## 10. 当前推荐推进顺序

当前建议不要再把注意力放在“kernel 还能不能更强”上，而是按下面顺序推进：

先确认当前产品层已经可以这样理解：

- `UI` 是壳层，负责创建、配置、展示和模拟
- `kernel` 负责 Codex 风格的命令、工具、run 生命周期和多任务
- 下一步真正缺的，是 `front` 的外显能力和 `robot runtime` 的稳定落地

1. 先做阶段 3：
   - 建立 `front` 的外显能力模型
   - 把外显类工具从 `kernel` 侧拆出来
2. 再做阶段 4：
   - 建 coordinator / driver
   - 把 `front` 决策稳定落到身体
3. 最后做阶段 5：
   - 把剩余 legacy 资产按新分层吸收进来

## 11. CLI 口径

### 11.1 当前明确退役

- `reachy-mini-app-assistant check`
- `reachy-mini-app-assistant publish`

### 11.2 当前明确暂不接入

- `emoticorebot desktop`
- `emoticorebot desktop-dev`

### 11.3 当前明确保留

- `reachy-mini-agent create`
- `reachy-mini-agent agent`
- `reachy-mini-agent web`

## 12. 一句话总结

当前已经完成的是一版 Codex 风格的 resident runtime 内核底座，下一步不该再纠结“先不先接 kernel”，而是应该直接进入：

`Stage 3: Front 外显大脑建设`

先把 `front` 做成接近原版的外显会话层，再通过协调层把它稳定落到 Reachy 身体上。










