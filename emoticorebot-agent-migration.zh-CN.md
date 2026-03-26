# Reachy Mini 接入 emoticorebot 大脑改造方案

## 1. 文档目标

本文档定义 `D:/work/py/reachy_mini` 的 Agent 改造方向：

- 保留 Reachy Mini 原项目的机器人基础能力
- 退役旧的 conversation / realtime agent 主流程
- 将 `emoticorebot` 作为新的唯一大脑
- 保留旧 conversation app 中值得复用的动作、工具、人格素材和视觉能力
- 最终形成“Reachy Mini 身体 + emoticorebot 大脑”的单一架构

本文档是迁移设计文档，不是最终实现代码。

配套的阶段性执行文档见：

- `emoticorebot-agent-migration-stages.zh-CN.md`

对话通道与 WebSocket 事件流的补充决策见：

- `emoticorebot-agent-dialogue-websocket.zh-CN.md`

目录命名收口结论：

- `src/reachy_mini/apps/`
- `src/reachy_mini/runtime/`
- `src/reachy_mini/core/`

其中当前代码中的 `agent_runtime/` 与 `agent_core/` 视为过渡命名，目标统一收口到 `runtime/` 与 `core/`。

## 2. 结论先行

本次改造不建议重写机器人底层，也不建议把旧 conversation app 完整保留为并行系统。

正确方向是：

1. 保留 `reachy_mini` 的机器人 SDK、daemon、motion、media、io、app 生命周期。
2. 删除或降级旧 agent 主流程，不再以 `OpenAI realtime + 旧 conversation profile 配置` 作为主脑。
3. 将 `emoticorebot` 的 `front + runtime + brain_kernel + memory + affect + companion` 引入当前仓库。
4. 复用旧 conversation app 中已经验证过的机器人动作能力、工具能力和人物设定素材。
5. 用 `emoticorebot` 生成的 `surface_state / reply / tool call` 驱动 Reachy Mini 原有动作接口。

一句话总结：

`Reachy Mini 继续做身体，emoticorebot 接管大脑。`

## 3. 当前现状分析

### 3.1 当前仓库的核心职责

当前仓库 `/Users/apple/work-py/reachy_mini` 主要是 Reachy Mini SDK 和运行底座，核心在：

- `src/reachy_mini/reachy_mini.py`
- `src/reachy_mini/daemon/`
- `src/reachy_mini/media/`
- `src/reachy_mini/motion/`
- `src/reachy_mini/io/`
- `src/reachy_mini/apps/manager.py`

这些模块负责：

- 机器人连接
- 头部/天线/身体控制
- 摄像头与音频
- daemon 生命周期
- App 启停与运行时管理

这部分不是 Agent 脑子，应该保留。

### 3.2 当前仓库中和旧 Agent 最接近的部分

当前仓库中和旧 conversation agent 最相关的遗留代码曾经主要有：

- `src/reachy_mini/apps/app.py`
- 旧 `src/reachy_mini/apps/fork_conversation.py`
- 旧 `src/reachy_mini/apps/templates/fork_conversation/`

这些代码的职责不是实现完整 Agent，而是：

- 创建 app
- fork 外部 `reachy_mini_conversation_app`
- 生成 profile / tools / README / static 页面

其中旧的 `fork_conversation` 入口和模板目录现在已经从仓库中删除。

也就是说，当前仓库并没有一个完整、可直接替换的旧 Agent 内核。旧脑子主要存在于外部 conversation app 仓库中。

### 3.3 旧 conversation app 的能力边界

经分析，上游 `reachy_mini_conversation_app` 主要包含：

- `openai_realtime.py`
- `moves.py`
- `camera_worker.py`
- `tools/`
- `profiles/`
- `prompts/`

其特点是：

- 有实时会话上下文
- 有 transcript 输出
- 有 personality/profile 机制
- 有工具白名单机制
- 有机器人动作与视觉工具

但它没有 `emoticorebot` 那种完整的独立记忆架构，例如：

- 独立 `memory.py`
- 多层 memory view
- run store
- sleep/reflection
- 长短期记忆存储

因此，旧系统中真正值得保留的是“机器人能力”和“人格素材”，不是旧大脑本身。

## 4. 改造原则

### 4.1 保留身体，不保留旧脑子

凡是解决“机器人怎么动、怎么播、怎么连”的模块，优先保留。

凡是解决“机器人怎么想、怎么决定、怎么组织对话”的模块，由 `emoticorebot` 替换。

### 4.2 优先复用成熟动作能力

旧 conversation app 中已经验证过的动作能力不应轻易抛弃，例如：

- 头部动作
- 天线动作
- emotion / dance 播放
- camera worker
- head tracking
- speech reactive wobble

这些都应作为新大脑可调用的能力层继续存在。

### 4.3 `profiles/<name>/` 升级为用户创建的 App 项目目录

改造后，profile 不再以旧的 `instructions.txt`、`tools.txt` 为主结构，而是统一采用 `profiles/<name>/` 目录来承载用户创建的 app 项目。

当前项目中的结构建议如下：

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

其中：

- `profiles/<name>/`
  是用户看到的 app 项目根目录，负责安装、启动和静态壳文件
- `profiles/<name>/<name>/`
  是 app 的 Python 启动包和静态前端目录
- `AGENTS.md`
  定义系统行为原则、约束和 Agent 规则
- `USER.md`
  定义用户画像、关系上下文和用户相关固定信息
- `SOUL.md`
  定义人格核心、价值观和长期稳定气质
- `TOOLS.md`
  定义工具策略、权限和工具使用规则
- `FRONT.md`
  定义前台表达风格和对用户可见的话术约束
- `config.jsonl`
  定义 profile 级运行配置和结构化配置项
- `memory/`
  放置 profile 独立记忆数据
- `skills/`
  放置 profile 私有 skills 或 skill 配置
- `session/`
  放置 profile 级会话状态和运行期数据
- `tools/`
  放置 profile 私有工具实现
- `prompts/`
  放置 profile 私有提示素材

本项目的 profile 结构中不包含 `HEARTBEAT.md`。

现有的 `profile loader` 可以继续保留，但其读取目标应改为上述新结构中的内部 profile 包，即 `profiles/<name>/profiles/`。同时，为了兼容当前项目生成出来的 app 项目根目录，loader 也应允许从 `profiles/<name>/` 进入后自动解析到内部 profile 包。

真正要替换的是 profile loader 后面接入的主脑：

- 旧系统中，profile 最终驱动的是 realtime 主流程
- 改造后，profile loader 的输出改为接入 `emoticorebot`

### 4.4 不保留双脑并行

最终系统中不应长期存在：

- 一个旧 OpenAI realtime 脑子
- 一个 `emoticorebot` 脑子

并行双脑会造成：

- prompt 来源冲突
- tool 调度冲突
- 行为状态冲突
- 调试困难

目标是只保留一个脑子：`emoticorebot`。

## 5. 模块保留/删除/改造总表

### 5.1 当前仓库中保留的模块

这些模块属于 Reachy Mini 基础能力，应保留：

- `src/reachy_mini/reachy_mini.py`
- `src/reachy_mini/daemon/`
- `src/reachy_mini/media/`
- `src/reachy_mini/motion/`
- `src/reachy_mini/io/`
- `src/reachy_mini/apps/manager.py`
- `src/reachy_mini/apps/app.py` 中与 app 生命周期有关的通用部分

### 5.2 当前仓库中要退役或删除的模块

这些模块代表旧 conversation 路线，应退役：

- 旧 `src/reachy_mini/apps/fork_conversation.py`
- 旧 `src/reachy_mini/apps/templates/fork_conversation/`
- `src/reachy_mini/apps/app.py` 中旧的 `conversation/legacy-conversation` 模板创建分支
- `reachy-mini-app-assistant` 中的 `check` / `publish` 子命令

注意：

- 如果短期内仍需要兼容旧 fork 流程，可以先“降级为 legacy”，不要立刻物理删除。
- 如果决定彻底切换，可以在第二阶段删除。

当前状态：

- 上述 `fork_conversation` 文件与模板目录已删除
- `conversation/legacy-conversation` 模板别名已从 `app.py` 中移除

### 5.3 从旧 conversation app 保留并迁入的能力

以下内容建议保留，但不再以旧主流程运行：

- `moves.py`
  作为机器人动作调度层保留
- `camera_worker.py`
  作为视觉输入层保留
- `tools/`
  作为机器人能力工具层保留
- `profiles/`
  升级为新 Agent 的 app 项目目录
- `prompts/`
  保留可复用提示词素材

以下内容不应作为主干继续保留：

- `openai_realtime.py`
- 旧 realtime session 管理逻辑
- 旧基于 OpenAI realtime 的主对话循环

### 5.4 从 emoticorebot 迁入的核心模块

建议迁入当前仓库的核心模块：

- `emoticorebot/brain_kernel/`
- `emoticorebot/runtime/`
- `emoticorebot/front/`
- `emoticorebot/affect/`
- `emoticorebot/companion/`
- `emoticorebot/providers/`
- `emoticorebot/config/`
- `emoticorebot/app/`

建议按需迁入的模块：

- `emoticorebot/tools/`
  仅保留当前项目需要的基础工具抽象和必要工具
- `emoticorebot/cli/` 中的 `agent` 命令
  作为文本级调试与运行入口迁入

建议暂不迁入的模块：

- `emoticorebot/channels/`
- `emoticorebot/desktop/`
- `emoticorebot/cron/`
- `emoticorebot` CLI 中的 `desktop`
- `emoticorebot` CLI 中的 `desktop-dev`
- 各种第三方平台接入层

原因是这些不是机器人大脑迁移的核心路径，会明显增加依赖复杂度。

## 6. 目标架构

### 6.1 目标分层

改造后系统分为四层：

1. Reachy 基础层
   - 机器人连接
   - daemon
   - motion
   - media
   - io

2. Reachy 能力层
   - 旧 conversation app 中可复用的动作管理
   - camera worker
   - 机器人工具

3. emoticorebot 大脑层
   - front
   - runtime
   - brain kernel
   - memory
   - affect
   - companion

4. 输出执行层
   - 将 `surface_state` 映射到 Reachy 动作
   - 将 `reply` 映射到音频/TTS
   - 将脑子的工具调用映射到 Reachy 机器人能力

### 6.2 数据流

目标数据流如下：

1. 用户输入进入 `emoticorebot.runtime`
2. `front` 先给出用户可感知的前台回复
3. `brain_kernel` 进行真正的决策、工具调用、记忆写入
4. `brain_kernel` 产出：
   - `reply`
   - `surface_state`
   - tool 调用
5. Reachy 输出执行层将其翻译为：
   - 头部动作
   - 天线动作
   - body yaw
   - 音频播放/TTS
   - 视觉工具与追踪能力

## 7. 推荐目录规划

### 7.1 当前仓库中的新目录建议

建议在当前仓库内增加以下目录：

- `src/reachy_mini/core/`
  放置迁入的 `emoticorebot` 核心能力或适配后的大脑层
- `src/reachy_mini/core/memory.py`
- `src/reachy_mini/core/`
  以及后续收口后的 kernel / memory / affect / companion / providers 等核心能力

建议增加 Reachy 专属执行层目录：

- `src/reachy_mini/runtime/`

建议增加正式 profile 目录：

- `profiles/`

其中建议放置：

- `surface_driver.py`
- `speech_driver.py`
- `robot_tools.py`
- `legacy_assets.py`
- `main.py`

### 7.2 旧 conversation app 资产建议归档方式

建议将旧 conversation app 中复用资产放在单独目录，避免与新脑子混杂：

- `src/reachy_mini/legacy_conversation_assets/`

可包含：

- `moves.py`
- `camera_worker.py`
- `prompts/`
- `tools/`

这样可以明确表达：

- 这些内容仍被使用
- 但它们不再是主脑，只是资产层

注意：

- `profiles/` 不建议继续作为 legacy 资产归档目录
- `profiles/` 应升级为当前系统的正式 app 项目目录，内部 profile 文件包位于 `profiles/<name>/profiles/`

## 8. 旧资产如何接入新大脑

### 8.1 profiles 的处理方式

改造后，`profiles/<name>/` 应作为当前系统中用户创建 app 的正式项目目录，而不是旧 conversation app 的兼容残留。

标准结构建议如下：

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

其中不包含 `HEARTBEAT.md`。

`profile loader` 的职责应调整为：

- 从 `profiles/<name>/` 项目根目录或 `profiles/<name>/profiles/` 内部包目录加载
- 读取内部 profile 包中的 profile 文件
- 读取内部 profile 包中的 `config.jsonl`
- 绑定内部 profile 包中的 `memory/`、`skills/`、`session/` 等 profile 级目录
- 校验 profile 结构是否完整
- 对缺失文件应用默认值或空值策略
- 将 profile 内容装配到 `emoticorebot` 的运行时输入

这里的重点不是“保留旧 profile 文本格式”，而是把这套 profile 文件升级为当前 app 项目中的原生内部包模型。

一句话总结：

profile 不再是旧 conversation 的提示词目录，而是新 Agent 的完整 persona/config/app 文件包。

### 8.2 prompts 的处理方式

旧 `prompts/` 中可复用的提示词片段建议保留，并优先迁入 `profiles/<name>/profiles/prompts/`，用于：

- 构建 persona
- 构建动作风格提示
- 构建特定角色语气提示

但不建议继续维持原有“占位符拼装 + 文本引用”作为主机制，后续应统一到 `emoticorebot` 的 prompt builder。

### 8.3 tools 的处理方式

`tools` 现在需要明确分成两层，而不是继续混在一个旧 conversation 目录里：

- 系统级工具：
  放在 `src/reachy_mini/runtime/tools/`
- app/profile 私有工具：
  放在 `profiles/<name>/profiles/tools/`

当前项目里这两层的语义已经确定：

- `src/reachy_mini/runtime/tools/`
  是所有 app 共享的系统工具目录
- `profiles/<name>/profiles/tools/`
  是当前 app 自己增加的私有工具目录

运行时加载顺序也应按这个模型固定：

1. 先加载系统级工具
2. 再加载当前 app 的 profile 私有工具
3. 最终把两者合并后交给 `BrainKernel`

这里的重点不是继续兼容旧 `tools.txt`，而是让新 runtime 直接读取目录。

截至 2026-03-26，当前实现已经有第一版落地：

- `src/reachy_mini/runtime/tool_loader.py`
  负责合并系统工具与 profile 工具
- `src/reachy_mini/runtime/tools/__init__.py`
  负责构建系统工具实例
- `profiles/<name>/profiles/tools/`
  已作为 profile 私有工具的标准目录

当前默认已接入的系统工具是工作区文件工具，具备真实文件读写能力，不再只是模型口头声称“已经创建文件”。

#### 8.3.1 旧 emoticorebot 系统工具的去向

从 `emoticorebot/emoticorebot/tools` 迁来的内容，属于系统级工具，应归入：

- `src/reachy_mini/runtime/tools/`

这一层是 runtime 自己的工具底座，不属于用户 profile。

#### 8.3.2 旧 conversation app tools 的分流规则

`/Users/apple/Downloads/reachy_mini_conversation_app-main/src/reachy_mini_conversation_app/tools/`
里的内容不能原封不动并入当前 runtime，需要先分流。

第一组：不应直接迁入当前主运行时

- `__init__.py`
- `core_tools.py`

原因：

- 这套加载逻辑建立在旧 `tools.txt` 和旧 conversation app 动态导入约定上
- 当前项目已经改为 `runtime/tool_loader.py + profiles/<name>/profiles/tools/`

第二组：保留能力语义，但不要原封不动照搬

- `background_tool_manager.py`
- `task_status.py`
- `task_cancel.py`
- `tool_constants.py`

原因：

- 这组文件绑定的是旧的后台工具编排模型
- 它们依赖旧的 dispatch 入口、idle 通知、后台任务状态机
- 当前 resident runtime 还没有完全等价的后台工具管理层

这组内容如果后续要接，应该按当前 runtime 生命周期重写，而不是直接复制。

建议落点：

- 原始资产归档：
  `src/reachy_mini/legacy_conversation_assets/tools/`
- 真正给 runtime 使用的重写版本：
  统一放到 `src/reachy_mini/runtime/tools/`

第三组：优先迁移的机器人能力工具

- `move_head.py`
- `play_emotion.py`
- `dance.py`
- `camera.py`
- `head_tracking.py`
- `stop_dance.py`
- `stop_emotion.py`
- `do_nothing.py`

这一组的价值在于“机器人会什么”，不是“旧 conversation 主流程怎么调度它们”。

因此建议保留：

- 工具名
- 参数语义
- 能力边界

但要改造：

- import 路径
- 依赖注入方式
- 返回值结构
- 对旧 `movement_manager`、`camera_worker`、`vision_processor`、`dance_emotion_moves` 的耦合方式

#### 8.3.3 旧 conversation app tools 的迁移顺序

建议顺序如下：

1. 先保留当前已接入的系统文件工具，保证 kernel 具备真实文件操作能力
2. 再迁移上面那批机器人能力工具
3. 最后再决定是否需要重建后台工具管理层

不建议一开始就迁：

- `background_tool_manager.py`
- `task_status.py`
- `task_cancel.py`

因为这会把旧 conversation 的后台执行模型一起带回来，容易把当前 runtime 再拉回旧架构。

#### 8.3.4 config.jsonl 的模型密钥约定

当前 runtime 的模型配置已经统一使用：

- `api_key`

不再使用：

- `api_key_env`

也就是说，`profiles/<name>/profiles/config.jsonl` 中的 `front_model` 和 `kernel_model` 记录，当前都应直接写 `api_key`。

### 8.4 moves 的处理方式

`moves.py` 建议完整保留，原因是它承载的是“机器人怎么动”，不是“机器人怎么想”。

建议将其定位为：

- 新大脑的动作编排引擎
- `surface_state` 执行后端
- `tool call` 的动作执行后端

### 8.5 camera_worker 的处理方式

`camera_worker.py` 建议保留。

它在新系统中的角色是：

- 摄像头输入服务
- head tracking 的输入层
- 视觉工具的数据源

但不应再与旧 `openai_realtime.py` 紧耦合。

## 9. surface_state 到 Reachy 动作的映射方案

这是本次改造的核心执行层。

### 9.1 输入来源

输入来自 `emoticorebot.runtime` 或 `front/companion` 生成的状态，包括但不限于：

- `phase`
- `mode`
- `presence`
- `expression`
- `motion_hint`
- `body_state`
- `breathing_hint`
- `linger_hint`
- `emotion_primary`
- `emotion_intensity`

### 9.2 输出目标

输出到 Reachy 原有控制接口：

- 头部姿态
- 天线姿态
- 身体旋转
- 呼吸式微动
- 音频播放

### 9.3 建议的映射规则

#### `motion_hint`

- `small_nod`
  轻微点头
- `nod`
  标准点头
- `bounce`
  更活跃的上下/天线联动
- `stay_close`
  低幅度贴近感动作，保持陪伴感
- `minimal`
  尽量不动，只保留轻微 breathing
- `small_tilt`
  轻微歪头

#### `phase`

- `listening`
  偏静态，少量 nod / tilt
- `replying`
  开启更明显的 speech wobble、点头和回应动作
- `settling`
  从回复过渡到安静状态，动作收束
- `idle`
  呼吸感、低干扰待机动作

#### `presence`

- `near`
  更贴近、更前倾
- `beside`
  偏安静陪伴
- `forward`
  更主动、更明显
- `steady`
  重心稳定、动作更克制

#### `expression`

可映射为：

- 头部幅度参数
- 天线幅度参数
- 节奏快慢
- 收尾停留时间

### 9.4 技术落点建议

建议建立独立模块：

- `surface_driver.py`

职责：

- 接收 `surface_state`
- 做状态去抖和节流
- 转换为具体动作命令
- 调用老的 `moves.py` 或 `reachy_mini.py` 接口

## 10. reply 到音频输出的映射方案

### 10.1 保留原有音频能力

你已经明确希望保留原项目的头部、天线、音频动作控制，因此音频输出能力也建议沿用旧项目的成熟路径。

### 10.2 新流程建议

新的 `reply` 流程：

1. `emoticorebot.front` 生成对用户可见的自然回复
2. 回复文本传给音频输出层
3. 音频输出层使用旧项目的音频/语音机制播放
4. 播放期间可触发头部和天线的 speech reactive wobble

### 10.3 不建议保留的旧部分

不建议继续保留旧 `openai_realtime.py` 负责：

- 文本生成
- 回复决策
- session.update
- conversation.item.create

这些都是旧大脑的主干。

## 11. 记忆系统迁移策略

### 11.1 旧系统情况

旧 conversation app 只有：

- profile
- prompt
- transcript
- realtime session context

没有完整 memory 架构。

### 11.2 新系统策略

记忆应完全交给 `emoticorebot`：

- `JsonlMemoryStore`
- `MemoryView`
- `RunStore`
- `SleepAgent`
- `long-term memory`

### 11.3 兼容建议

如果你觉得旧 transcript 或对话记录有价值，可以作为冷启动素材导入，但不必做复杂迁移。

建议做法：

- 把旧 transcript 当作历史文本输入
- 生成少量 persona / user summary
- 写入 `emoticorebot` 的 memory store

但这不是迁移阻塞项，可以后做。

## 12. 依赖与版本策略

### 12.1 Python 版本

当前仓库 `pyproject.toml` 为：

- `requires-python = ">=3.10"`

`emoticorebot` 当前为：

- `requires-python = ">=3.11"`

建议：

- 将当前项目统一提升到 Python 3.11

否则后续依赖整合会持续存在问题。

### 12.2 依赖整合建议

建议分两阶段进行：

#### 阶段一：临时依赖接入

先允许当前项目通过本地路径或开发依赖引用 `emoticorebot`，快速验证脑子替换路线。

#### 阶段二：正式收编

等架构稳定后，再将所需模块真正迁入当前仓库，并清理无关依赖。

### 12.3 不建议一开始全量收编

不要一开始就把 `emoticorebot` 全部目录和全部依赖无差别并入当前仓库。

原因：

- 风险过高
- 依赖过重
- 调试困难
- 容易把机器人底座污染为通用聊天框架

## 13. 分阶段实施计划

### 阶段 0：冻结范围

目标：

- 明确保留模块
- 明确保留的旧 conversation 资产
- 明确最终只保留一个脑子

交付物：

- 本文档
- 模块保留/退役清单

### 阶段 1：先接入 front 文本层

目标：

- 让当前项目能启动文本级主入口
- 跑通 `app 文件包 -> front -> 文本回复`

本阶段不做：

- 复杂动作映射
- 视觉联动
- 大规模旧资产迁移
- `emoticorebot` 现有 `desktop / desktop-dev` 入口接入
- 音频/TTS 输出

成功标准：

- app 文件包已真正驱动 `front`
- 文本回复链路已经接通 `front`
- `agent` 命令行入口已可用于文本级启动和验证

当前第一版实现已经落到：

- `src/reachy_mini/runtime/main.py`
- `src/reachy_mini/runtime/config.py`
- `src/reachy_mini/front/service.py`
- `src/reachy_mini/front/prompt.py`
- `src/reachy_mini/runtime/model_factory.py`
- `src/reachy_mini/runtime/scheduler.py`
- `src/reachy_mini/core/memory.py`
- `src/reachy_mini/runtime/project.py`

当前命令形态为：

- `reachy-mini-agent create <app_name>`
- `reachy-mini-agent agent <app_name|app_path>`

### 阶段 2：再接入 kernel

目标：

- 在 front 文本层稳定后接入 `kernel`
- 跑通更完整的文本主链路

成功标准：

- `kernel` 已接入主文本链路
- 旧 realtime 主流程不再承担主脑职责

当前 2026-03-26 的阶段性结果：

- 已将 `emoticorebot` 的 standalone 内核代码直接迁入当前仓库：
  - `src/reachy_mini/core/`
- `reachy-mini-agent` 默认链路已经完全切到 resident kernel
- 当前默认文本链路为：
  - `app 文件包 -> front -> BrainKernel -> front`
- resident runtime scheduler 已接入，内核以常驻生命周期运行，不再保留旧回退参数

### 阶段 3：接回机器人动作与音频输出

目标：

- `reply` -> 音频
- `surface_state` -> 头部/天线动作
- 基础工具调用 -> Reachy 能力

成功标准：

- 机器人能根据新脑子进行自然动作反馈
- 原有动作控制层继续工作

### 阶段 4：迁移旧 conversation 资产

目标：

- 迁移 `moves.py`
- 迁移 `tools/`
- 迁移 `camera_worker.py`
- 迁移 `profiles/` 和 `prompts/`

成功标准：

- 旧资产全部转为新大脑的可用资源
- 不再依赖旧 conversation 主流程

### 阶段 5：清理旧脑子

目标：

- 清理 `fork_conversation` 相关入口
- 清理旧 realtime 主流程
- 文档改写为新架构

成功标准：

- 当前仓库语义上已经是“Reachy Mini + emoticorebot”
- 旧脑子完全退出主干

## 14. 具体文件级改造建议

### 14.1 当前仓库中需要修改的文件

- `pyproject.toml`
  - 升级 Python 版本
  - 引入新脑子需要的依赖
- `src/reachy_mini/apps/app.py`
  - 删除旧的 `conversation/legacy-conversation` 模板入口
  - 删除 `check` / `publish` 子命令，以及相关 `--publish`、`--private` 等发布参数
- `docs/source/index.mdx`
  - 后续重写 AI/Conversation 说明
- `docs/source/SDK/integration.md`
  - 后续重写 AI 集成说明

### 14.2 当前仓库中建议新增的文件

- `src/reachy_mini/runtime/main.py`
- `src/reachy_mini/runtime/surface_driver.py`
- `src/reachy_mini/runtime/speech_driver.py`
- `src/reachy_mini/runtime/profile_loader.py`
- `src/reachy_mini/runtime/tool_loader.py`
- `src/reachy_mini/runtime/web.py`

### 14.3 当前仓库中建议新增的目录

- `src/reachy_mini/core/`
- `src/reachy_mini/legacy_conversation_assets/`
- `profiles/`
- `src/reachy_mini/runtime/tools/`

### 14.4 旧资产迁入后建议位置

- 旧 `moves.py` -> `src/reachy_mini/legacy_conversation_assets/moves.py`
- 旧 `camera_worker.py` -> `src/reachy_mini/legacy_conversation_assets/camera_worker.py`
- 旧 conversation app 原始 `tools/` 资产归档 -> `src/reachy_mini/legacy_conversation_assets/tools/`
- 系统级共享工具与迁入的 Reachy 机器人工具 -> `src/reachy_mini/runtime/tools/`
- 旧 `profiles/` 内容 -> 重构为 `profiles/<name>/profiles/AGENTS.md`、`USER.md`、`SOUL.md`、`TOOLS.md`、`FRONT.md`、`config.jsonl`
- profile 级记忆数据 -> `profiles/<name>/profiles/memory/`
- profile 级 skills -> `profiles/<name>/profiles/skills/`
- profile 级会话数据 -> `profiles/<name>/profiles/session/`
- 旧 profile 私有工具 -> `profiles/<name>/profiles/tools/`
- 旧 profile 私有提示素材 -> `profiles/<name>/profiles/prompts/`

## 15. 风险清单

### 15.1 Python 与依赖冲突

风险：

- `emoticorebot` 与当前仓库 Python 版本不一致
- LangChain 依赖链会引入新的复杂度

应对：

- 尽早统一到 Python 3.11
- 分阶段引入依赖

### 15.2 动作层耦合过深

风险：

- 旧动作层可能隐式依赖旧 realtime handler

应对：

- 将动作执行和会话管理解耦
- 把 `moves.py` 当纯执行层收敛

### 15.3 profile 输出与新脑子接线冲突

风险：

- profile 各文件职责不清，容易在 `AGENTS.md`、`SOUL.md`、`TOOLS.md`、`FRONT.md` 之间出现重复和冲突

应对：

- 明确每个 profile 文件的职责边界
- 明确 `profile loader` 负责装配哪些运行时输入
- 避免在多处重复拼装 persona、front 风格和 tool policy

### 15.4 输出状态过于频繁

风险：

- `surface_state` 高频更新可能让机器人动作抖动

应对：

- 在 `surface_driver.py` 中增加节流、去抖、状态收束逻辑

## 16. 测试策略

### 16.1 单元测试

重点测试：

- `surface_state` 到动作命令的映射
- 工具调用适配层
- profile loader
- memory 写入是否正常

### 16.2 集成测试

重点测试：

- 文本输入 -> 新 brain -> reply
- reply -> 音频输出
- `motion_hint` -> 头部/天线动作
- tool 调用 -> 机器人执行

### 16.3 真机验证

重点验证：

- 说话时的动作同步
- listening / replying / idle 三种状态切换
- 视觉输入和 head tracking
- 情绪表达动作是否自然

## 17. 推荐实施顺序

建议严格按以下顺序执行：

1. 先接入新大脑，不动复杂动作
2. 先跑通 `app 文件包 -> front -> 文本回复`
3. 再接入 `kernel`
4. 再把 `reply` 接回音频
5. 再把 `surface_state` 接回头部/天线
6. 再接回旧 `tools/` 和 `moves.py`
7. 最后再处理 `profiles/`、`prompts/` 和 legacy 清理

不要一开始同时做：

- 依赖整合
- prompt 迁移
- 动作映射
- 视觉接入
- UI 重写

这样风险太高。

## 18. 最终目标定义

改造完成后，项目应满足以下标准：

- Reachy Mini 机器人控制能力完整保留
- 旧 conversation 主脑被 `emoticorebot` 完全替换
- 旧 conversation app 中的动作、工具、人格素材被新脑子复用
- 记忆系统由 `emoticorebot` 统一提供
- 系统只保留一个 Agent 主流程

最终的系统身份不再是：

- “Reachy Mini SDK + 可 fork 的旧 conversation app”

而应是：

- “Reachy Mini 机器人底座 + emoticorebot 大脑”

## 19. 本文档对应的实际决策

基于当前讨论，推荐立即执行的决策如下：

1. 保留 Reachy Mini 的基础控制层。
2. 保留旧 conversation app 的动作层、工具层、视觉层和人格素材层。
3. 不保留旧 conversation app 的 realtime 主流程作为主脑。
4. 将 `emoticorebot` 作为新的唯一大脑接入当前项目。
5. 用 `surface_state / reply / tool call` 接管旧控制层。
6. 退役 `reachy-mini-app-assistant` 的 `check` / `publish` 命令，不再保留旧发布流。
7. 迁入 `emoticorebot` 的 `agent` 命令行入口，作为文本级主入口之一。
8. 暂不接入 `emoticorebot` 现有的 `desktop / desktop-dev` 入口，先聚焦 Reachy 主路径。
9. 当前要接入主运行时的工具统一落到 `src/reachy_mini/runtime/tools/`。
10. `profiles/<name>/profiles/tools/` 继续保留给 app 私有扩展工具，不承载主运行时内置工具。
11. 旧 conversation app 的机器人能力工具按当前 runtime 重写接入，不直接复用旧 `core_tools.py` 和旧后台工具编排层。
12. `config.jsonl` 的模型配置统一使用 `api_key`，不再使用 `api_key_env`。

这就是本项目后续改造的统一基线。




