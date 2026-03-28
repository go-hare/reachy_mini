# Reachy Mini 接入 emoticorebot 大脑改造方案

## 1. 文档目标

本文档定义 `D:/work/py/reachy_mini` 的 Agent 改造方向：

- 保留 Reachy Mini 原项目的机器人基础能力
- 退役旧的 conversation / realtime agent 主流程
- 将 `emoticorebot` 作为新的唯一大脑
- 保留旧 conversation app 中值得复用的动作、工具、人格素材和视觉能力
- 最终形成“Reachy Mini 身体 + emoticorebot 大脑”的单一架构

本文档是迁移设计文档，不是最终实现代码。

本文档已按当前仓库实现同步到：

- 2026-03-28

配套的阶段性执行文档见：

- `emoticorebot-agent-migration-stages.zh-CN.md`

对话通道与 WebSocket 事件流的补充决策见：

- `emoticorebot-agent-dialogue-websocket.zh-CN.md`

目录命名收口结论：

- `src/reachy_mini/apps/`
- `src/reachy_mini/runtime/`
- `src/reachy_mini/core/`

其中：

- 早期讨论中的 `agent_runtime/` 与 `agent_core/` 仅是过渡命名
- 当前仓库代码已经实际收口到 `runtime/` 与 `core/`

## 2. 结论先行

本次改造不建议重写机器人底层，也不建议把旧 conversation app 完整保留为并行系统。

正确方向是：

1. 保留 `reachy_mini` 的机器人 SDK、daemon、motion、media、io、app 生命周期。
2. 删除或降级旧 agent 主流程，不再以 `OpenAI realtime + 旧 conversation profile 配置` 作为主脑。
3. 将 `emoticorebot` 的 `front + runtime + brain_kernel + memory + affect + companion` 引入当前仓库。
4. 复用旧 conversation app 中已经验证过的机器人动作能力、工具能力和人物设定素材。
5. 将 `front` 升级为真正的外显层，而不是简单文案包装层。
6. 当前主链路已通过 `reply / tool call` 和 runtime 产出的 `surface_state` 驱动 Reachy Mini 原有动作接口；后续再把更多 `front` 外显状态决策直接接入执行层。

一句话总结：

`Reachy Mini 继续做身体，emoticorebot 接管大脑。`

进一步收口后的产品口径是：

- `UI`
  负责 app 创建、配置、运行壳和机器人模拟
- `front`
  负责外显复杂任务，是“外显大脑”或“外显导演层”
- `kernel`
  负责 Codex 风格的命令、工具、run 生命周期和多任务调度，是“任务大脑”
- `robot runtime`
  负责执行协调、真机驱动与模拟驱动，把 `front` / `kernel` 的结果稳定落到身体

需要明确：

- `UI` 不是 `front`
- `front` 不是 `kernel`
- `kernel` 也不是身体执行层

也就是说，这次要对齐的“原版能力”，核心并不是更强的任务求解，而是更强的 `front` 外显编排能力。

## 3. 当前现状分析

### 3.1 当前仓库的核心职责

当前仓库 `D:/work/py/reachy_mini` 主要是 Reachy Mini SDK 和运行底座，核心在：

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

当前仓库中和旧 conversation agent 最相关、也是这次迁移的主要承接点，当前主要有：

- `src/reachy_mini/apps/app.py`
- `src/reachy_mini/runtime/main.py`
- `src/reachy_mini/runtime/project.py`
- `src/reachy_mini/runtime/profile_loader.py`
- `src/reachy_mini/runtime/tool_loader.py`

其中：

- 旧的 `fork_conversation` 入口和模板目录已经从仓库中删除
- `reachy-mini-agent create` 现在直接生成新的 app 项目目录
- `reachy-mini-agent agent` 与 `reachy-mini-agent web` 已作为新的 resident runtime 入口存在
- `apps/app.py` 当前除了 app 生命周期外，也已经承担 resident runtime 与 WebSocket 宿主接线；运行时工具上下文和身体/语音 hook 适配已开始收口到 `apps/runtime_host.py`

也就是说，当前仓库已经不只是“删掉旧入口”，而是已经有了一版新的 Agent 运行主路径。旧脑子主要仍存在于外部 conversation app 仓库中，但当前仓库已具备替换主脑所需的基本骨架。

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

### 4.3 `front` 与 `kernel` 的职责重新定义

这次讨论已经进一步明确：

- `front` 不是简单的文本润色层
- `front` 也不是只能做“轻量回复”
- `kernel` 也不是唯一会做复杂工作的层

真正的职责划分应改为：

- `front`
  负责外显复杂任务
- `kernel`
  负责内置处理复杂任务

其中：

- `front` 的复杂任务包括：
  - 倾听态、回复态、收束态、待机态编排
  - 情绪表达与动作选择
  - 说话时机与动作时机对齐
  - 视线、头部、天线、呼吸感、陪伴感等外显策略
  - 外显类工具的选择与调用
- `kernel` 的复杂任务包括：
  - 任务分解
  - 复杂推理
  - 文件、系统、工作区、外部服务等任务工具调用
  - 记忆写入、读取和长期状态维护

这一点非常重要：

- 我们不是要把系统改成“front 简单、kernel 复杂”
- 而是要改成“front 管外显复杂性，kernel 管任务复杂性”

### 4.4 外显工具归属到 `front`

关于工具归属，新的架构决策已经明确：

- 外显类工具归 `front`
- 任务类工具归 `kernel`

这里的“外显类工具”包括但不限于：

- `move_head`
- `play_emotion`
- `dance`
- `stop_emotion`
- `stop_dance`
- `head_tracking`
- `camera`
- 未来用于 listening / replying / idle / settling 编排的动作工具

这里的“任务类工具”包括但不限于：

- 文件读写
- 工作区搜索
- system / exec / mcp / web 等任务工具
- profile 私有的任务型工具

当前代码现状补充：

- `kernel` 默认任务工具平面目前已经稳定接入的是文件与工作区类工具
- `system / exec / mcp / web` 相关工具文件已经存在，但还没有全部进入默认主链路

需要特别说明的是：

- “工具放在哪一层”不是线程数问题
- 即使当前只有一个会话线程，也依然应该按“外显决策权属于谁”来划分

因此，单线程不构成把外显工具继续留在 `kernel` 的理由。

### 4.5 `profiles/<name>/` 升级为用户创建的 App 项目目录

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

### 4.6 不保留双脑并行

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
  当前第一版已落到 `src/reachy_mini/runtime/moves.py`
- `camera_worker.py`
  当前第一版已落到 `src/reachy_mini/runtime/camera_worker.py`
- `tools/`
  当前第一版 Reachy 机器人工具已落到 `src/reachy_mini/runtime/tools/reachy_tools.py`
- `profiles/`
  已升级为新 Agent 的 app 项目目录
- `prompts/`
  继续保留为 profile 私有提示素材目录

此外，围绕这些能力的配套层当前也已经出现第一版：

- `src/reachy_mini/runtime/audio/`
  放置 speech reactive wobble 相关能力
- `src/reachy_mini/runtime/vision/`
  放置 head tracking / local vision 相关能力
- `src/reachy_mini/runtime/dance_emotion_moves.py`
  放置 emotion / dance 队列动作资产

以下内容不应作为主干继续保留：

- `openai_realtime.py`
- 旧 realtime session 管理逻辑
- 旧基于 OpenAI realtime 的主对话循环

### 5.4 从 emoticorebot 迁入的核心模块

当前已经以“整包迁入”或“按 Reachy 语义适配后迁入”的核心模块，主要有：

- `src/reachy_mini/core/`
  承接 `brain kernel / memory / run store / sleep agent` 相关核心能力
- `src/reachy_mini/front/`
  承接 front prompt / front service，以及后续要增强的外显导演能力
- `src/reachy_mini/affect/`
  承接 affect runtime
- `src/reachy_mini/companion/`
  承接 companion intent 与 surface expression
- `src/reachy_mini/runtime/config.py`
  承接运行时配置装配
- `src/reachy_mini/runtime/model_factory.py`
  承接模型构建与 provider 适配
- `src/reachy_mini/runtime/main.py`
  承接 `agent` / `web` 入口
- `src/reachy_mini/runtime/scheduler.py`
  承接 resident runtime 生命周期与 kernel 桥接

建议按需迁入的模块：

- `emoticorebot/tools/`
  仅保留当前项目需要的基础工具抽象和必要工具
- `emoticorebot/cli/` 中的 `agent` 命令语义
  当前已经以 `reachy-mini-agent agent` 的形式迁入

当前尚未以原目录形态完整保留，但语义已部分吸收到当前仓库的内容：

- `providers/`
  已体现在 `runtime/model_factory.py` 等适配层中，而不是单独保留原目录
- `config/`
  已体现在 `runtime/config.py`
- `app/`
  已体现在 `apps/app.py`、`runtime/project.py`、生成模板和 resident runtime 接线中

建议暂不迁入的模块：

- `emoticorebot/channels/`
- `emoticorebot/desktop/`
- `emoticorebot/cron/`
- `emoticorebot` CLI 中的 `desktop`
- `emoticorebot` CLI 中的 `desktop-dev`
- 各种第三方平台接入层

原因是这些不是机器人大脑迁移的核心路径，会明显增加依赖复杂度。

## 6. 目标架构

### 6.1 产品视角与运行时视角

为了让产品口径更简单，同时不丢失实现边界，建议同时保留两套视角：

- 产品视角：
  - `UI + front + kernel + robot runtime`
- 运行时细化视角：
  - `5` 个运行时层
  - `1` 个横向配置面

其中：

- 产品视角适合对外表达、产品讨论和团队日常沟通
- 运行时细化视角适合做真实的代码分层、事件流设计和执行链路设计

先看产品视角。

建议的 `4` 个产品层如下：

1. `UI`
   - 负责 app 创建、配置、运行壳和会话展示
   - 可以提供机器人模拟视图
   - 不承担核心智能决策

2. `front`
   - 负责外显复杂任务
   - 负责前台表达、陪伴感、情绪、倾听态/回复态/收束态/待机态编排
   - 负责外显类工具的选择与调用

3. `kernel`
   - 负责内置处理复杂任务
   - 负责 Codex 风格的命令、工具、run 生命周期和多任务调度
   - 负责推理、记忆、任务拆解、任务工具调用、事实结果生成

4. `robot runtime`
   - 负责承接 `front` 与 `kernel` 的结果
   - 负责执行协调、真机驱动和模拟驱动
   - 负责把高层决策翻译为稳定的机器人执行动作

这里需要再额外固定一个口径：

- `robot runtime` 仍然是一个产品层，不建议当前再拆成新的对外层级
- 但在工程职责上，`robot runtime` 内部必须明确分成两段：
  - 宿主编排职责
    - 负责承接输入事件、turn 生命周期、`front` / `kernel` 串联、输出分发与收束
    - 当前主要落在 `runtime/scheduler.py`、`apps/app.py` 与 `apps/runtime_host.py`
  - 执行协调职责
    - 负责动作仲裁、身体状态翻译、speech motion、tracking 压制与恢复
    - 当前主要落在 `EmbodimentCoordinator`、`surface_driver.py`、`speech_driver.py`
- 这是一种职责拆分，不等于当前必须物理拆目录
- 先把职责边界写死，再决定未来是否需要物理拆文件或目录

再看运行时细化视角。

建议的 `5` 个运行时层如下：

1. 信息输入层
   - 负责接入用户文本、语音转写、WebSocket 事件、相机/视觉输入、按钮/触摸、系统状态事件
   - 只负责“把信息送进来”
   - 不负责重决策

2. `front` 外显层
   - 负责外显复杂任务
   - 负责前台表达、陪伴感、情绪、倾听态/回复态/收束态/待机态编排
   - 负责外显类工具的选择与调用

3. `kernel` 任务层
   - 负责内置处理复杂任务
   - 负责 Codex 风格的命令、工具、run 生命周期和多任务调度
   - 负责推理、记忆、任务拆解、任务工具调用、事实结果生成

4. 执行协调层
   - 负责统一仲裁 `front`、`kernel`、实时音频、视觉、idle 状态
   - 负责把高层决策翻译为稳定的执行命令
   - 这里应承载 `runtime` 的协调能力，以及后续的 `EmbodimentCoordinator`、`surface_driver.py`、`speech_driver.py`

5. 身体输出层
   - 负责真正驱动 Reachy 身体
   - 包括 `MovementManager`
   - 包括 `HeadWobbler`
   - 包括 `CameraWorker`
   - 包括 Reachy SDK / motion / media / io / daemon 等底层执行能力

横向配置面为：

- App/Profile 配置面
  - 负责定义 persona、front 风格、tool policy、memory、prompts、profile 私有工具
  - 当前主要落在 `profiles/<name>/profiles/`
  - 它不是运行时主链路中的一层，而是贯穿 `front`、`kernel`、协调层的配置输入面

补充说明：

- 这里仍维持 `5` 层的细化模型，不额外升格出第 `6` 层
- `robot runtime` 内部的“宿主编排职责”当前视为 runtime 内部语义，不单独升格为新的顶层运行时层
- 也就是说，当前真正需要固定的是职责边界，而不是继续增加层级数量

### 6.2 `robot runtime` 内部边界禁令清单

为了避免 `robot runtime` 再次长成一个混合大层，当前需要把“宿主编排职责”和“执行协调职责”的边界直接写死。

1. 宿主编排职责允许做的事
   - 承接输入事件和 turn 生命周期
   - 串联 `front`、`kernel`、memory、front output、WebSocket 输出
   - 分发 `surface_state`、reply audio hook、front tool result、turn error
   - 管理线程、队列、订阅、收束与对外事件发布

2. 宿主编排职责禁止继续长的方向
   - 不直接操作 `MovementManager`、`CameraWorker`、`HeadWobbler`、Reachy SDK
   - 不直接决定动作优先级、tracking 压制、speech motion 清理与恢复
   - 不继续堆积新的身体执行规则
   - 不把自己长成第二个 `front` 或半个 `EmbodimentCoordinator`

3. 执行协调职责允许做的事
   - 消费 `surface_state`
   - 仲裁显式动作、speech motion、tracking、listening / replying / settling / idle 的身体阶段
   - 把高层意图翻译为 `MovementManager`、`surface_driver.py`、`speech_driver.py` 可执行的动作
   - 管理动作冲突、抢占、恢复和低层执行状态

4. 执行协调职责禁止继续长的方向
   - 不接触 `user_text`、`kernel_output`、memory、run 生命周期、任务状态推进
   - 不生成前台文本
   - 不负责 profile prompt、persona、tool policy
   - 不把自己长成第二个 `scheduler` 或半个 `kernel`

5. `apps/app.py` 的约束
   - 允许保留宿主接线、runtime 启停、WebSocket 桥接、hook 适配
   - 不继续沉淀新的外显策略和执行仲裁
   - 未来新增 runtime 规则时，优先进入 `scheduler` 或 `coordinator`；新增宿主胶水时，优先进入薄的 host adapter，而不是继续堆进 `app.py`

6. 当前需要特别警惕的漂移点
   - `scheduler` 当前已经承担了一部分 `companion / surface` 的一阶外显策略拼装
   - 这部分短期可以保留，但后续新增“怎么表现”的规则时，不应继续无节制堆进 `scheduler`
   - 新增外显策略优先收口到 `front` / `companion` 语义域；新增执行规则优先收口到协调层

一句话约束：

- `scheduler` 负责把系统串起来
- `coordinator` 负责把身体稳下来
- 谁开始同时做这两件事，谁就在越界

### 6.3 `front` / `kernel` 双脑分工

为了避免再次回到“一个大脑同时管所有事情”的旧混合状态，需要把这两层明确成两种不同的复杂性中心：

1. `front` 是外显大脑
   - 负责“怎么表现”
   - 决定这一轮是贴近、安静、提气、调皮还是专注
   - 决定 listening / replying / settling / idle 的外显过渡
   - 决定是否调用外显类工具，以及调用哪一个
   - 核心目标是让机器人“像活着一样在场”

2. `kernel` 是任务大脑
   - 负责“怎么解决”
   - 决定要不要查文件、跑工具、写记忆、拆任务、做规划
   - 负责 Codex 风格的命令、工具、run 生命周期管理
   - 负责多任务调度、任务状态推进和任务取消/收束
   - 负责真实事实、真实结果和任务推进
   - 核心目标是把问题处理对

这也解释了为什么当前反复提到“原版能力”。

本次对齐原版时，真正要补的是：

- `front` 的工作能力

而不是：

- 再造一个更大的 `kernel`

### 6.4 数据流

目标数据流如下：

1. 用户先通过 `UI`、语音、视觉、按钮或 WebSocket 进入系统
2. 这些输入再进入“信息输入层”，被标准化成当前 runtime 可消费的事件
3. App/Profile 配置面为 `front`、`kernel` 和协调层提供 persona、prompt、tool policy、memory 配置
4. `front` 做外显判断，决定前台回复风格、外显状态和是否触发外显类工具
5. `brain_kernel` 进行内置任务决策、任务工具调用、记忆写入，并负责 Codex 风格的命令/工具/run 生命周期推进
6. `front` 与 `brain_kernel` 分别产出：
   - `front`
     - 外显回复
     - 外显工具调用
     - 外显状态切换
   - `brain_kernel`
     - 事实结果
     - 任务结论
     - 任务工具调用
7. `robot runtime` 内部的执行协调层统一仲裁这些输出，并翻译为稳定的执行对象：
   - `reply`
   - `surface_state`
   - 外显工具动作
   - 任务工具结果
8. 身体输出层最终将其落到 Reachy 身体或模拟体：
   - 头部动作
   - 天线动作
   - body yaw
   - 音频播放/TTS
   - 视觉工具与追踪能力
9. 最终可由 `UI` 把会话状态、任务状态和机器人状态呈现出来

## 7. 推荐目录规划

### 7.1 当前仓库中的目录现状

截至 2026-03-27，下面这些目录已经实际存在并承担迁移主路径职责：

- `src/reachy_mini/core/`
- `src/reachy_mini/front/`
- `src/reachy_mini/affect/`
- `src/reachy_mini/companion/`
- `src/reachy_mini/runtime/`
- `src/reachy_mini/runtime/tools/`
- `profiles/`

其中当前 `runtime/` 下已经实际存在的关键文件包括：

- `main.py`
- `config.py`
- `project.py`
- `profile_loader.py`
- `tool_loader.py`
- `web.py`
- `scheduler.py`
- `moves.py`
- `camera_worker.py`
- `dance_emotion_moves.py`
- `audio/`
- `vision/`

当前已经正式落地、并正在继续收口的 Reachy 输出执行层文件包括：

- `src/reachy_mini/runtime/surface_driver.py`
- `src/reachy_mini/runtime/speech_driver.py`
- `src/reachy_mini/runtime/embodiment/coordinator.py`

### 7.2 旧 conversation app 资产建议归档方式

从语义上说，旧 conversation app 中复用的资产仍然适合与新脑子分层管理。

但截至当前实现：

- `src/reachy_mini/legacy_conversation_assets/` 目录还没有真正建立
- 第一版已迁入或重写的资产，当前直接落在 `src/reachy_mini/runtime/` 与 `src/reachy_mini/runtime/tools/`

也就是说，当前仓库选择的是：

- 先把“真的会被 resident runtime 用到的能力”直接接入主运行时
- 暂时不额外建立一个纯归档目录

如果后续要增强“原始 legacy 资产”和“现运行时资产”的语义隔离，再引入：

- `src/reachy_mini/legacy_conversation_assets/`

会更合适。

注意：

- `profiles/` 不建议继续作为 legacy 资产归档目录
- `profiles/` 已升级为当前系统的正式 app 项目目录，内部 profile 文件包位于 `profiles/<name>/profiles/`

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

运行时加载顺序在目标架构下也应按两套职责分开：

1. `kernel` 加载任务类系统工具
2. `kernel` 加载当前 app 的 profile 私有任务工具
3. `front` 单独持有外显类工具集合
4. 最终由 runtime/coordinator 统一执行到机器人能力层

换句话说，外显工具不应继续作为 `kernel` 的默认工具集混入同一决策平面。

在当前代码现实里，截至 2026-03-28：

- `runtime/tool_loader.py` 已经拆成 `kernel_system_tools`、`front_tools`、`profile_tools`
- `RuntimeScheduler` 当前会把 `front_tools` 交给 `FrontService`，把 `kernel_tools` 交给 `BrainKernel`
- Reachy 的外显工具主链路已经不再默认挂在 `kernel` 决策平面，而是开始由 `front` 持有并通过 runtime/coordinator 执行

这意味着：

- “外显工具归 `front`”已经不只是架构决策，而是当前主链路现实
- 当前剩余差异不再是“工具归属错位”，而主要是：
  - 正常对话时，`front` LLM 还不会像上游 realtime agent 一样自由自动 function-call 外显工具
  - idle 创意动作范围当前仍主要收口为 `move_head / do_nothing`

因此，后续改造目标不是“继续往 `kernel` 里加更多外显工具”，而是继续增强 `front` 的自由决策深度，并只在必要处保留规则化收口。

运行时加载顺序中与 `kernel/front` 相关的部分应按下面模型固定：

1. 先加载系统级工具
2. 再加载当前 app 的 profile 私有工具
3. 单独装配 `front` 持有的外显工具集合
4. 将前两者合并后交给 `BrainKernel`

这里的重点不是继续兼容旧 `tools.txt`，而是让新 runtime 直接读取目录。

截至 2026-03-28，当前实现已经有第一版落地：

- `src/reachy_mini/runtime/tool_loader.py`
  负责合并系统工具与 profile 工具
- `src/reachy_mini/runtime/tools/__init__.py`
  负责构建系统工具实例
- `profiles/<name>/profiles/tools/`
  已作为 profile 私有工具的标准目录

当前默认已接入的系统工具，已经不只是工作区文件工具，还包括第一版 Reachy 机器人工具：

- 工作区文件工具：
  `read_file`、`write_file`、`edit_file`、`list_dir`、`search_files` 等
- Reachy 机器人工具：
  `move_head`
  `do_nothing`
  `head_tracking`
  `camera`
  `play_emotion`
  `dance`
  `stop_emotion`
  `stop_dance`

也就是说，当前 runtime 已经具备：

- 真实文件读写能力
- 第一版机器人动作/视觉能力工具接入能力

但从新决策看，后续需要把这些 Reachy 机器人工具进一步细分：

- 任务型机器人能力
  如果未来存在，应继续归 `kernel`
- 外显型机器人能力
  应迁到 `front`

当前第一版 Reachy 机器人工具中，绝大多数都更接近“外显型机器人能力”。

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

当前状态是：

1. 系统文件工具：已完成第一版
2. 机器人能力工具：已完成第一版接入
3. 后台工具管理层：尚未接入，仍建议后置

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

当前第一版代码已经直接位于：

- `src/reachy_mini/runtime/moves.py`

建议将其定位为：

- 新大脑的动作编排引擎
- `surface_state` 执行后端
- `tool call` 的动作执行后端

### 8.5 camera_worker 的处理方式

`camera_worker.py` 建议保留。

当前第一版代码已经直接位于：

- `src/reachy_mini/runtime/camera_worker.py`

它在新系统中的角色是：

- 摄像头输入服务
- head tracking 的输入层
- 视觉工具的数据源

但不应再与旧 `openai_realtime.py` 紧耦合。

## 9. surface_state 到 Reachy 动作的映射方案

这是本次改造的核心执行层。

需要补充的关键决策是：

- `surface_state` 仍然保留
- 但它不再被视为原版能力的全部来源

原版真正让人感觉“活着”的地方，不只是有一个抽象 `surface_state`，而是：

- `front` 侧在倾听、说话、待机、情绪表达时有持续外显编排能力
- 这些外显决策再通过动作层统一落到机器人身体上

因此，`surface_state` 更适合作为：

- 外显基线状态
- 连续姿态和呼吸感的输入

而不应成为：

- 唯一的外显决策来源

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

当前实现状态：

- `RuntimeScheduler` 已经能构建并持续推送 `surface_state`
- `ReachyMiniApp` 已经支持通过 `surface_state_handler` 和 `/ws/agent` 转发这些状态
- `surface_driver.py` 第一版已经建立，并已将 `surface_state` 映射到 `MovementManager` 的 `set_listening()` / `mark_activity()`
- `surface_driver.py` 当前带有一层极薄的 `thread_id` 状态收束，避免多会话时单个 `idle` 直接覆盖仍在进行中的身体状态
- `ReachyMiniApp` 的 WebSocket 与 `app.chat()` 已统一走同一条身体状态入口
- `ReachyMiniApp` 里原先持续变厚的 runtime tool context 构建/清理、surface/audio 转接，现已收口到 `src/reachy_mini/apps/runtime_host.py`
- `speech_driver.py` 与 `EmbodimentCoordinator` 第一版已经建立，开始把 `HeadWobbler` 从 app 细节提升为执行协调层能力
- `speech_driver.py` 现在已开始正式接管 `HeadWobbler` 生命周期，并在 `replying` 阶段按音频新鲜度自动回收残留 speech motion
- `reply_audio.py` 第一版已经建立，支持通过可选 `speech` 配置启用 OpenAI TTS，把 `final reply` 以 24 kHz PCM16 流式合成并按 chunk 推送到 Reachy media
- `move_head / play_emotion / dance / head_tracking / stop_*` 等外显工具现在已开始优先通过 `EmbodimentCoordinator` 落到身体执行层
- coordinator 已具备第一条仲裁规则：显式动作开始时暂时压住 head tracking，并清理残留 speech motion，动作窗口结束后恢复期望 tracking
- coordinator 已补入第二条仲裁规则：显式动作优先级暂定为 `move_head > play_emotion > dance`，高优先级可清队列抢占低优先级，低优先级在强动作窗口内会被延后
- resident runtime 已补入第一版 `reply -> 语音播放` 闭环：`RuntimeScheduler` 会在 `front_final_done` 之后、`settling_entered` 之前触发 reply audio，因此整段语音播放仍处于 `replying` 相位，并同步驱动 speech motion
- resident runtime 现已开始真实发出 `assistant_audio_started / assistant_audio_delta / assistant_audio_finished`，并在空闲阶段周期性发出 `idle_tick`，`front` 也会基于 `idle_tick` 触发轻量 look-around
- resident runtime / app websocket 也已正式支持 `user_speech_started / user_speech_stopped`，并补入 `listening_wait` 中间相位
- `user_speech_started` 现在也会主动打断当前 reply audio，并阻止被打断的旧 turn 再回写 `settling / idle`
- 浏览器模板和示例 app 现已补入第一版麦克风输入链路：`SpeechRecognition -> user_speech_started / user_speech_stopped -> 最终 user_text`
- 当前真正还没完成的，是“raw PCM / input audio buffer / server VAD -> runtime”的输入链路、reply-audio 更低延迟/错误外显策略，以及更深的 coordinator 动作编排

补充建议：

- `surface_driver.py`
  负责连续外显状态
- `front` 外显工具
  负责显式动作触发
- `runtime/coordinator`
  负责把两者统一仲裁后送到 `MovementManager`

这比单纯依赖 `surface_state -> 动作映射` 更接近原版效果。

## 10. reply 到音频输出的映射方案

### 10.1 保留原有音频能力

你已经明确希望保留原项目的头部、天线、音频动作控制，因此音频输出能力也建议沿用旧项目的成熟路径。

### 10.2 新流程建议

新的 `reply` 流程：

1. `emoticorebot.front` 生成对用户可见的自然回复
2. 回复文本传给音频输出层
3. 音频输出层使用旧项目的音频/语音机制播放
4. 播放期间可触发头部和天线的 speech reactive wobble

当前实现状态：

- 运行时已经有 `HeadWobbler`、`speech_tapper` 等音频动作辅助能力
- `ReachyMiniApp` 也已经暴露了音频 delta 喂给 wobble 的 helper
- `RuntimeScheduler -> runtime_host -> reply_audio.py` 这条“最终 reply 文本 -> TTS/音频播放”正式输出链路已经接通
- 播放期间会同步发出 `assistant_audio_started / delta / finished` 语义，并继续驱动 speech reactive wobble
- `/ws/agent` 也已能接收 `user_speech_started / user_speech_stopped` 并下发到 runtime，对应 surface phase 会进入 `listening / listening_wait`
- `user_speech_started` 到来时，当前 reply audio 也会立刻中断，旧 turn 不会再把身体状态拉回 `settling / idle`
- 浏览器侧也已补入第一版真实麦克风输入：支持的浏览器会通过 `SpeechRecognition` 发出 `user_speech_started / user_speech_stopped` 并自动提交最终 transcript
- 所以当前缺口已经不是“没有 speech lifecycle 语义”或“不会插话打断”，而是“没有 raw PCM / input audio buffer / server VAD 级输入链路”，以及更接近原版 realtime 的低延迟持续会话能力
- 因此当前音频相关实现已经进入“正式语音输出层 + 动作联动层”阶段，但离原版 realtime 那种更低延迟的持续会话壳还有距离

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

当前状态与建议：

- 当前项目仍维持 `>=3.10`
- 后续如果要继续扩大 `emoticorebot` 依赖整合范围，建议再统一提升到 Python 3.11

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
- `web` 命令行入口已可用于无硬件浏览器验证

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
- `reachy-mini-agent web <app_name|app_path>`

### 阶段 2：再接入 kernel

目标：

- 在 front 文本层稳定后接入 `kernel`
- 跑通更完整的文本主链路

成功标准：

- `kernel` 已接入主文本链路
- 旧 realtime 主流程不再承担主脑职责

截至 2026-03-27 的阶段性结果：

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

当前状态补充：

- `surface_state` 已经能产出
- Reachy 机器人工具已经接入第一版
- `MovementManager`、`CameraWorker`、`HeadWobbler` 等运行时上下文已经可注入 resident runtime
- `surface_driver.py` 第一版已经建立，并已接通 `surface_state -> 身体基线状态`
- `speech_driver.py` 第一版已经建立，并已把音频驱动入口正式收口
- `EmbodimentCoordinator` 第一版已经建立，并已开始统一承接身体执行入口
- `front` 已正式持有外显类工具
- 外显类工具与任务类工具已完成第一版职责拆分
- `front` 当前已经能产出 `lifecycle_state / surface_patch / tool_calls`，但身体执行主链里的 phase `surface_state` 仍主要由 runtime/scheduler 推送，`front surface_patch` 还不是身体层唯一入口
- `kernel` 默认任务工具平面当前仍以文件/工作区类工具为主，更宽的 `system / exec / mcp / web` 工具还未全部进入默认主链路
- 外显类工具已开始优先通过 coordinator 落到身体执行层
- coordinator 已开始处理动作 / tracking / speech 的冲突关系
- coordinator 已补入显式动作优先级与抢占规则，当前优先级暂定为 `move_head > play_emotion > dance`
- `reply_audio.py` 第一版已经建立，resident runtime 已具备 `final reply -> OpenAI TTS 流式合成 -> Reachy media 分块播放 -> speech motion 同步` 的闭环
- 但统一 coordinator 的完整仲裁策略和动作编排链仍未完成
- 因此这一阶段仍然只算“已完成部分底座和接线”，还没有完全收口

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
  - 当前已完成 resident runtime / WebSocket / tool context 接线
  - 后续重点应放在接入正式输出执行层，而不是旧 conversation 分支清理
- `docs/source/index.mdx`
  - 仍需继续重写首页口径，降低 legacy conversation / Hugging Face 发布流在主路径中的权重
- `docs/source/SDK/integration.md`
  - 当前已完成第一版 resident runtime 文档重写，后续以增量补充为主
- `docs/source/troubleshooting.md`
  - 仍保留部分 legacy conversation / OpenAI realtime 说明，后续需要继续收口

### 14.2 当前仓库中已新增与仍待新增的文件

当前已经存在：

- `src/reachy_mini/runtime/main.py`
- `src/reachy_mini/runtime/profile_loader.py`
- `src/reachy_mini/runtime/tool_loader.py`
- `src/reachy_mini/runtime/web.py`

在本轮迁移中已经新增：

- `src/reachy_mini/runtime/surface_driver.py`
- `src/reachy_mini/runtime/speech_driver.py`
- `src/reachy_mini/runtime/reply_audio.py`
- `src/reachy_mini/runtime/embodiment/coordinator.py`

### 14.3 当前仓库中已新增与仍待新增的目录

当前已经存在：

- `src/reachy_mini/core/`
- `profiles/`
- `src/reachy_mini/runtime/tools/`

当前仍待评估是否新增：

- `src/reachy_mini/legacy_conversation_assets/`

### 14.4 旧资产迁入后建议位置

- 当前第一版 `moves.py` -> `src/reachy_mini/runtime/moves.py`
- 当前第一版 `camera_worker.py` -> `src/reachy_mini/runtime/camera_worker.py`
- 当前系统级共享工具与 Reachy 机器人工具 -> `src/reachy_mini/runtime/tools/`
- 如果后续需要保存“未改写的原始 legacy 资产”，再考虑新增：
  - `src/reachy_mini/legacy_conversation_assets/moves.py`
  - `src/reachy_mini/legacy_conversation_assets/camera_worker.py`
  - `src/reachy_mini/legacy_conversation_assets/tools/`
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

- 在继续扩大依赖整合范围前统一到 Python 3.11；当前仍按 `>=3.10` 运行
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

截至当前仓库，已经存在的相关测试主要包括：

- `tests/unit_tests/test_profile_loader.py`
- `tests/unit_tests/test_agent_profile_config.py`
- `tests/unit_tests/test_runtime_tool_loader.py`
- `tests/unit_tests/test_runtime_surface_driver.py`
- `tests/unit_tests/test_runtime_embodiment.py`
- `tests/unit_tests/test_runtime_reachy_tools.py`
- `tests/unit_tests/test_runtime_reply_audio.py`
- `tests/unit_tests/test_app_runtime_tool_context.py`
- `tests/unit_tests/test_front_agent_runner.py`
- `tests/unit_tests/test_front_service_runtime.py`
- `tests/unit_tests/test_kernel_agent_runner.py`
- `tests/unit_tests/test_kernel_tool_execution.py`
- `tests/unit_tests/test_resident_runtime_host.py`
- `tests/unit_tests/test_runtime_head_wobbler.py`

当前仍然明显缺口较大的测试区域是：

- `surface_driver.py` 对状态节流、去抖和映射逻辑的测试
- `speech_driver.py` / `reply_audio.py` 在更细粒度时序、异常回退、真实设备采样率差异上的测试
- 真正面向动作执行层的端到端行为测试

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
13. `front` 被正式定义为外显大脑，不再视为简单文本包装层。
14. `kernel` 被正式定义为任务大脑，不再承载默认的外显工具决策。
15. 外显类工具应逐步从当前 `kernel` 侧加载模型迁移到 `front` 侧。
16. “原版能力对齐”的主目标是补齐 `front` 的外显工作能力，而不是继续扩张 `kernel`。

这就是本项目后续改造的统一基线。



