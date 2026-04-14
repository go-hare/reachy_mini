# ccmini 作为 Reachy Mini 唯一大脑的落地设计

## 1. 背景

Reachy Mini resident runtime 在迁移前的官方文档定义是双层脑结构，见 [AI Integrations](integration.md)：

`app project -> front -> BrainKernel -> front`

这代表旧运行时里同时存在两套认知层：

- `front`
- `core / BrainKernel`

其中：

- `front` 负责前台表达、快速前台响应、前台动作决策、用户可见话术
- `core / BrainKernel` 负责任务推理、工具调用、记忆、路由、后台 run

与此同时，`ccmini` 的文档已经把它定义成统一大脑和可嵌入 brain SDK，见：

- `src/ccmini/README_ZH.md`
- `src/ccmini/CCMINI_EMBEDDABLE_BRAIN_SDK_ZH.md`
- `src/ccmini/CCMINI_ROBOT_TOOL_INTEGRATION_ZH.md`
- `src/ccmini/CCMINI_PROFILES_ZH.md`

这两条线曾经并存，但现在仓库中的 resident runtime 已经收口成单一主链。本文保留背景、边界和落地结果，用来解释为什么当前实现是这样组织的。

## 2. 目标

本文的目标是说明当前已经落地的单脑方向：

- 只保留一个大脑
- 这个大脑是 `ccmini.Agent`
- `front` 和 `core` 不再作为独立脑层存在
- Reachy Mini 保留宿主、执行、UI、表情、语音、安全、实时控制职责
- resident runtime 已切到单脑主链
- 浏览器协议、宿主接口和前端实现已同步切到单脑语义

这里的“只保留一个大脑”需要精确定义：

- 它指的是 **只保留一套认知实现**，也就是未来只有 `ccmini` 负责推理
- 它 **不** 指“全局只允许一个前台活跃 turn”
- 它也 **不** 强制要求“所有 `thread_id` 必须串行共用一个全局前台 lane”

当前实现继续保持与原先一致的前台语义：

- 前台仍按 `thread_id` 分 lane
- 每个 `thread_id` 都维护自己的 `current_turn_id / surface_state / stale` 裁决
- 某个线程的新输入只影响该线程自己的前台可见输出
- 后台任务、worker、Kairos、memory 等持续运行能力不因某个前台线程切换而停止

实现层的状态机草图见：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

### 2.1 建议阅读顺序

为了减少在三份文档之间反复跳读，建议按下面顺序阅读：

1. 先读本文，理解单脑方向、分层、边界和阶段目标
2. 再读 [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)，理解 turn / run / stale / 打断规则
3. 最后读 [ccmini 单脑架构实施清单](ccmini-single-brain-checklist.md)，把它转成实际改造任务和验证项

### 2.2 术语表

下面这些术语建议在整套文档里固定含义，不再混用。

- `thread_id`
  外部 UI / 浏览器线程标识。它是宿主和浏览器共同理解的前台 lane 概念。第一阶段需要继续保持与当前 `front` 一样的 per-thread 前台行为。
- `conversation_id`
  `ccmini` 内部会话标识。第一阶段建议与 `thread_id` 维持稳定一对一映射，但语义上它属于 brain 会话，不属于浏览器协议。这里的一对一映射是 **per-thread lane** 语义，不是“全局单前台 turn”语义。
- `session_id`
  app/runtime 或连接层的上下文标识。它不等于 `conversation_id`，也不应该默认等于一个用户 turn。
- `turn_id`
  一次用户输入对应的一轮主链标识，由 `agent.submit_user_input(...)` 返回，并贯穿该轮对用户可见的输出。
- `run_id`
  一次 query 执行片段或 tool 恢复续跑链路的标识。目标态下，一个 `turn_id` 可以关联一个或多个 `run_id`；若当前实现仍是单槽位 continuation，应在宿主适配层按现状接入，不要误判为已具备多 run 并发恢复能力。
- `tool_use_id`
  单个工具调用的唯一标识。它用于把 `PendingToolCallEvent.calls[*]` 和后续 `HostToolResult` 对上。
- `stale`
  旧 turn 已失去前台可见资格，但内部清理、停止动作、释放资源仍可继续。
- `surface_state`
  宿主发给浏览器的生命周期 / 表情 / UI 相位状态，不等于 brain 的内部推理事件。
- `text_delta`
  浏览器看到的唯一 assistant 流式文本事件。默认由 `TextEvent` 直接映射而来。
- `turn_done`
  浏览器看到的单轮最终收口事件。默认由 `CompletionEvent` 直接映射而来。

## 3. 非目标

本文明确不追求下面这些事情：

- 不为了兼容旧前端而保留双脑主链
- 不要求第一阶段连带重写 app project 目录结构
- 不要求第一阶段同时打通 `ccmini` 的全部扩展面，如 Kairos、Buddy 驱动 UI
- 不把机器人高频控制环塞进 `ccmini`
- 不把宿主重新做成一个巨大的兼容层

## 4. 核心结论

Reachy Mini 后续应采用如下结构：

```mermaid
flowchart LR
    A["App / Browser / Speech Input"] --> B["RuntimeScheduler (Host)"]
    B --> C["ccmini.Agent (Only Brain)"]
    C --> D["PendingToolCallEvent"]
    D --> B
    B --> E["Reachy Embodiment / Speech / Camera / Motion / Safety"]
    E --> F["HostToolResult"]
    F --> B
    B --> C
    C --> G["TextEvent / CompletionEvent / ErrorEvent / ThinkingEvent / ToolProgressEvent"]
    G --> B
    B --> H["surface_state / speech_preview / text_delta / turn_done / turn_error / thinking / tool_progress"]
```

一句话总结：

- `ccmini` 负责思考
- Reachy Mini 宿主负责执行和表现
- 前台继续按 `thread_id` 独立裁决
- 后台任务继续常驻运行
- 浏览器协议直接收口成单轨事件模型
- 内部不再保留双脑

## 5. 当前架构的主要问题

`front -> BrainKernel -> front` 这条旧双脑链路虽然能工作，但会带来这些问题：

- 职责重叠：`front` 和 `core` 都在做认知相关判断
- 状态分裂：前台知道一部分上下文，核心知道另一部分上下文
- 记忆分裂：`front` 和 `kernel` 的行为、历史、提示词边界不统一
- 输出路径复杂：前台先说一遍，核心再想一遍，最后前台再包装一遍
- 配置双份：`front_model` 和 `kernel_model` 同时存在，复杂度高
- 迁移成本上升：想增强大脑能力时，要同时考虑两层模型链路

对于只保留一个大脑的方向，这套结构现已退出 resident runtime 主链。

## 6. 为什么选 ccmini 做唯一大脑

`ccmini` 已经具备统一大脑所需的关键能力，不只是一个模型调用封装。

它已经提供：

- 常驻生命周期：`start()` / `stop()`
- 正式宿主入口：`submit_user_input(...)`
- 正式事件输出：`on_event(...)`、`wait_event()`、`poll_event()`、`drain_events()`
- 工具暂停恢复：`PendingToolCallEvent` + `submit_tool_results(...)`
- 宿主事件注入：`publish_host_event(HostEvent(...))`
- `Tool` / `ClientTool` 边界
- Hook 体系：`PreQueryHook`、`PreToolUseHook`、`IdleHook`、`StopHook` 等
- 统一 memory/session
- 流式事件：`TextEvent`、`CompletionEvent`、`ThinkingEvent`、`ToolProgressEvent`、`ErrorEvent`
- 快思考相关能力：fast mode、prompt suggestion、speculation、tool-use summary
- 慢思考与协调能力：coordinator、background tasks、多 agent、team / peer
- 睡眠与记忆能力：统一 memory/session、sleep / consolidation 相关能力
- companion / 宠物态能力：Buddy、companion、关系连续性相关能力
- 时间性与自主性能力：Kairos、proactive / autonomy 相关能力

这说明 `ccmini` 不是再加一层，而是已经具备取代当前双脑的基础。

### 6.1 这些能力为什么都和机器人有关

这里还需要再明确一层：

- 快思考
- 慢思考
- 睡眠与记忆
- companion / 宠物态
- 团队协作
- Kairos / 自主性

这些不是“机器人之外的高级插件”，而是持续存在型机器人主脑本来就该有的能力层。

因为 Reachy Mini 不是一次性问答器，而是：

- 持续在线
- 持续与人互动
- 有身体
- 有时间流逝
- 有长期关系
- 有空闲期与活跃期

所以这些能力应被理解为：

- 都属于单脑内部能力
- 都可以服务机器人行为
- 只是第一阶段不要求全部外显接入到 runtime 表现层

### 6.2 coordinator 在单脑里的定位

这里需要明确一件很容易被误解的事：

- `coordinator` 不是另一颗脑
- `coordinator` 不是外挂 orchestration 服务
- `coordinator` 是 `ccmini.Agent` 自身的一种工作模式

也就是说，单脑架构下可以是这样的关系：

- 唯一大脑仍然是 `ccmini.Agent`
- 这颗大脑可以运行在 `normal` 模式
- 也可以运行在 `coordinator` 模式
- 当它运行在 `coordinator` 模式时，worker delegation / background task / result synthesis 仍然属于这颗主脑内部能力

因此本文说“只保留一个 brain”，并不意味着要弱化或关闭 coordinator；恰恰相反，coordinator 可以作为单脑的核心协调能力存在。

## 7. 新分层定义

未来建议固定为四层。

### 7.1 认知层：ccmini

职责：

- 理解用户输入
- 决定回复内容
- 决定是否调用工具
- 决定调用哪个工具
- 组织多步推理
- 维护会话与记忆
- 发出流式事件

边界：

- 不直接持有高频电机控制
- 不直接做原始传感器实时处理
- 不直接绑定浏览器协议

### 7.2 宿主编排层：RuntimeScheduler

职责：

- 接收用户文字与语音事件
- 继续按 `thread_id` 维护前台 lane
- 调用 `ccmini` 的正式宿主接口
- 监听 `ccmini` 流式事件
- 执行工具调用
- 提交工具结果
- 生成和推送外部协议消息
- 控制语音播放、表情中断、surface 状态
- 协调后台任务、Kairos、worker 等常驻能力与前台 lane 的关系

边界：

- 不再自己做推理
- 不再持有第二颗脑
- 不再依赖 `FrontService` / `BrainKernel` 作为主链
- 不引入“全局唯一活跃前台 turn”语义

### 7.3 执行层：Embodiment

职责：

- 电机执行
- 动作管理
- 表情管理
- TTS/音频播放
- 摄像头采集
- 视觉处理
- 头部跟踪
- 安全限制和执行前保护

边界：

- 不负责决策
- 不负责语言生成
- 不负责会话记忆

### 7.4 外部接口层：App / Browser

职责：

- WebSocket 消息收发
- 浏览器 UI
- 麦克风输入
- 外部调用入口

边界：

- 不直接知道 `ccmini` 内部协议
- 不与内部 brain 深耦合

## 8. front 和 core 未来如何处理

这里最重要的是，不要把停用脑职责和物理删目录混为一谈。

### 8.1 front 的处理

现在已经不再把 `front` 当成模型层使用。

`front` 当前承担了两类职责：

- 认知职责：快速前台回复、前台动作决策、前台话术生成
- 表现职责：listening、replying、idle 等 UI/表情节奏

当前处理方式：

- 认知职责并入 `ccmini`
- 表现职责下沉到宿主本地规则
- `FRONT.md` 保留，作为统一 prompt 的风格输入
- `FrontService` 不再走主推理链

### 8.2 core 的处理

现在已经不再把 `BrainKernel` 当成主脑使用。

`core` 当前承担了：

- resident loop
- task routing
- tool loop
- run state
- memory
- sleep consolidation

当前处理方式：

- resident brain 改由 `ccmini` 单脑运行时承担
- task/tool/memory 主链改由 `ccmini`
- run/event/tool-result 协议统一改为 `ccmini` 宿主接口
- `BrainKernel` 不再是热路径
- `core.memory` 不迁移、不复用，统一 memory/session 直接收口到 `ccmini`
- `core.sleep_agent` 不迁移；如果未来需要空闲维护能力，应基于 `Hook` 或 background task 重新设计，而不是继承旧实现
- `core.run_store` 不迁移、不保留为宿主任务板；宿主只保留最小必要的线程态和执行态
- 不应把 `BrainKernel` 逻辑一比一搬进 `ccmini`

## 9. 为什么仍然需要宿主本地规则

只保留一个大脑不等于一切都交给大脑。

宿主本地规则不是第二颗脑，而是反射层、执行层和协议层。

应该保留在宿主本地的东西包括：

- `listening / listening_wait / replying / settling / idle` 这类 UI 生命周期
- 用户一开口立即打断播放
- 正在收音时切换 surface 状态
- 语音播放结束后的回落节奏
- 高实时性和高频控制
- 运动执行前最后一层安全检查
- WebSocket 外部协议消息格式

这些事情的特点是：

- 低延迟
- 强确定性
- 强硬件耦合
- 强安全约束
- 不需要思考
- 不应该依赖模型输出才能完成

所以宿主本地规则必须保留，但它们不构成第二颗脑。

## 10. ccmini 在快响应方面可以替代 front 什么能力

旧 `front` 存在的一大理由是快速响应用户，但 `ccmini` 已经有足够强的快响应能力。

可利用的能力包括：

- 非阻塞提交：`submit(...)` / `submit_user_input(...)`
- 流式输出：`TextEvent`
- 思考状态：`ThinkingEvent`
- 工具进度：`ToolProgressEvent`
- 工具摘要：`ToolUseSummaryEvent`
- fast mode
- prompt suggestion
- speculation

这意味着未来快速响应不再需要一个单独 front-model 层。

未来替代方式：

- 默认把 `TextEvent` 作为实际回复流，直接输出 `text_delta`
- `CompletionEvent` 作为整轮最终全文，直接输出 `turn_done`
- 不再保留 `front_hint_* / front_final_*` 双轨协议
- `ThinkingEvent` 和 `ToolProgressEvent` 驱动中间状态、surface 过渡态或可选的短暂提示
- speculation 和 fast mode 作为后续增强能力

## 11. 新的主边界：Tool + Hook + HostEvent + HostToolResult

这是最关键的统一边界。

### 11.1 Tool

作用：

- 告诉大脑你有哪些能力可以调用

推荐暴露给 `ccmini` 的最小工具集合：

- `speak`
- `move_head`
- `look_at`
- `play_emotion`
- `dance`
- `head_tracking`
- `camera`
- `stop_motion`
- `wake_up`
- `goto_sleep`

可选：

- `set_interaction_mode`
- `notify_user`
- `get_robot_capabilities`
- `record_memory_marker`

不要暴露：

- 原始关节写入
- 高频 `set_target()` 连续接口
- 原始 PID / 扭矩参数写入
- 原始传感器流透传

工具契约建议：

- 一次性工具：`speak`、`move_head`、`look_at`、`play_emotion`、`camera`
- 模式切换工具：`wake_up`、`goto_sleep`、`set_interaction_mode`
- 长时且可中断工具：`dance`、`head_tracking`
- 中断工具：`stop_motion`，用于停止长时动作、跟踪或当前排队中的运动

这些工具的边界应明确为：

- `ccmini` 只决定“要不要调工具”和“用什么参数调”
- 宿主负责工具互斥、排队、取消、安全裁决和硬件资源占用
- 长时工具不应把高频控制环阻塞在模型推理里
- 长时工具应先返回 `queued` / `started`，必要时再通过后续结果或宿主状态变化体现 `completed` / `stopped`
- `stop_motion` 应设计为幂等操作，避免重复调用导致额外副作用

### 11.2 Hook

作用：

- 给大脑提供安全和上下文环境，不是主业务入口

优先使用的 Hook：

- `PreQueryHook`
- `PreToolUseHook`
- `IdleHook`
- `SessionStartHook`
- `SessionEndHook`
- `StopHook`
- `NotificationHook`

### 11.3 HostEvent

作用：

- 宿主将状态和系统事件注入会话

适合注入：

- `sensor_summary`
- `speech_started`
- `speech_stopped`
- `mode_changed`
- `surface_summary`
- `vision_attention_summary`
- `safety_state`

不适合注入：

- 高频原始流
- 每帧图像
- 低层日志

### 11.4 HostToolResult

作用：

- 宿主在执行完 client-side tool 后，把结果结构化回给 `ccmini`

适合返回：

- 已排队
- 已开始
- 已完成
- 已拒绝
- 简短视觉结果
- 动作执行摘要
- 错误信息

## 12. 直接切换，允许破坏式调整

这次落地坚持的原则是：

- 认知主链一次切换
- 外部接口按单脑语义同步收口

继续保留的核心资产：

- app project 结构
- `FRONT.md / AGENTS.md / USER.md / SOUL.md / TOOLS.md`
- `thread_id / conversation_id / turn_id / run_id` 这一套运行时语义
- 宿主执行层、音频层、相机层和安全裁决边界

已经直接调整的内容：

- `GET /`、`WS /ws/agent` 以及相关 payload
- 浏览器消息结构和事件命名
- `ChatResponse` 字段组织方式
- 前端状态机和页面联动逻辑
- `FrontService` 推理主链
- `BrainKernel` 推理主链
- `BrainEvent / BrainOutput` 主协议
- `front_model` 和 `kernel_model` 双脑行为

## 13. 接口映射关系

旧接口到现接口的映射如下：

- `BrainKernel.publish_user_input(...)`
  -> `ccmini.Agent.submit_user_input(...)`
- `BrainKernel.publish_observation(...)`
  -> `ccmini.Agent.publish_host_event(...)`
- `BrainKernel.publish_front_event(...)`
  -> `ccmini.Agent.publish_host_event(...)`
- `BrainKernel.publish_tool_results(...)`
  -> `ccmini.Agent.submit_tool_results(...)`
- `BrainKernel.recv_output()`
  -> `ccmini.Agent.on_event(...)` / `wait_event()` / `drain_events()`

旧数据结构到新数据结构：

- `PendingToolCall.tool_call_id`
  -> `ToolCallEvent.tool_use_id`
- `PendingToolCall.args`
  -> `ToolCallEvent.tool_input`
- `ToolResult.success`
  -> `HostToolResult.is_error = not success`
- `FrontEvent`
  -> `HostEvent`
- `BrainOutput.response.reply`
  -> `CompletionEvent.text`

### 13.1 常驻 runtime 的推荐调用方式

对 Reachy Mini 这种常驻 runtime，推荐主路径固定为：

- 入口用 `submit_user_input(...)`
- 输出消费用 `on_event(...)` 或 `wait_event()` / `drain_events()`
- client-side tool 恢复用 `submit_tool_results(...)`

`query()` 仍然有价值，但更适合：

- 单次脚本
- bridge / demo host
- 调试或测试

不建议让 `RuntimeScheduler` 的常驻主循环直接围绕 `query()` 组织。

同时要明确：

- 这里的“常驻 runtime”指统一的单脑宿主
- 不应把它理解成“所有 `thread_id` 串行共用一个全局前台 turn”
- 第一阶段必须继续保持与当前 `front` 一样的 per-thread lane 语义

### 13.2 宿主最小状态模型

既然不再保留 `core.memory`、`run_store`、`sleep_agent`，宿主侧应只维护最小必要状态。

更完整的 turn / run / stale / 打断规则，见：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

建议固定保留：

- `thread_id -> conversation_id`
  外部线程和 `ccmini` 会话的一对一稳定映射
- `thread_id -> current_turn_id`
  当前对浏览器仍然有效的活跃回合
- `run_id -> {thread_id, conversation_id, turn_id}`
  用于 `PendingToolCallEvent` 恢复时精确路由 `submit_tool_results(...)`
- `thread_id -> surface_state`
  当前 surface / phase 的宿主态
- `thread_id -> audio_state`
  例如收音中、播放中、冷却中
- `thread_id -> execution_handles`
  例如当前动作、跟踪、TTS 播放等可中断句柄

补充说明：

- 这里描述的是宿主目标态状态模型
- 如果当前 `ccmini` continuation 仍是单 pending-client-run 槽位，宿主第一阶段应按现状实现适配
- 不要因为目标态文档里使用 `run_id` 映射，就误以为当前已经支持一个 turn 上多个并发 client-side continuation

建议固定遵守的规则：

- `thread_id` 是外部 UI / 浏览器概念，`conversation_id` 是 `ccmini` 会话概念；第一阶段保持稳定一对一
- 同一 `thread_id` 上出现新的用户输入后，旧 `turn_id` 的前台输出应视为过期，不再继续推送给浏览器
- 某个 `thread_id` 上的新输入只使该线程自己的旧 `turn_id` 过期；其他线程的前台 lane 不受影响
- 过期事件可以继续用于本地清理，但不能污染新的前台 turn
- `submit_tool_results(run_id, ...)` 必须按保存下来的 `run_id` 映射恢复，不能猜测“当前线程就是目标线程”
- 宿主不重新长出任务板、双轨 memory 或 sleep 子系统
- 后台任务、worker、Kairos、memory 等常驻能力不因为某个前台线程 stale 或切换而停止

### 13.3 浏览器事件建议

既然不再要求兼容旧 WebSocket 协议，建议浏览器直接收口到单轨事件模型。

| ccmini 事件 / 宿主信号 | 浏览器事件 | 建议 |
| --- | --- | --- |
| 宿主 ASR / 语音局部预览 | `speech_preview` | 继续保留，服务浏览器或麦克风桥的实时转写 UX |
| `TextEvent` | `text_delta` | 作为唯一 assistant 流式文本事件 |
| `CompletionEvent` | `turn_done` | 发送整轮最终全文，作为单轮收口结果 |
| `ThinkingEvent` | `thinking` | 可直接公开给前端，或由前端决定是否展示 |
| `ToolProgressEvent` | `tool_progress` | 直接表达工具执行进度，不再伪装成 hint |
| `PendingToolCallEvent` | 不直接透传 | 宿主执行工具后走 `submit_tool_results(...)` 恢复 |
| `ErrorEvent` | `turn_error` | 绑定当前 `turn_id`，直接暴露给浏览器 |
| 宿主生命周期状态变化 | `surface_state` | 作为 UI / 机器人表现层的唯一宿主状态信号 |

额外约束：

- `text_delta` 是唯一主回复流，不再存在 `front_hint_* / front_final_*` 双轨
- `turn_done` 应携带该轮最终全文，前端以 done 为准收口
- `thinking` 和 `tool_progress` 是独立辅助手段，不能承载真实 assistant 文本流

## 14. Prompt 资产怎么利用

统一大脑后，prompt 资产不能浪费，反而应该更集中利用。

建议统一 system prompt 组成：

- `AGENTS.md`
- `USER.md`
- `SOUL.md`
- `TOOLS.md`
- `FRONT.md`
- 宿主追加上下文

这里最关键的一点是：

- `FRONT.md` 继续保留
- 但它不再意味着必须有一个 front model
- 它只是统一 brain 的一部分风格输入

## 15. 该怎么利用 ccmini 的强大能力

这里不是简单替换，而是要真正用到 `ccmini` 的优势。

优先值得利用的能力：

- 常驻单 Agent
- coordinator mode
- 流式输出
- client-tool 暂停恢复
- Hook 体系
- 统一 memory/session
- fast mode（快思考）
- tool-use summary
- prompt suggestion
- speculation
- 睡眠与记忆相关能力
- companion / Buddy 相关能力
- Kairos / 时间性能力

后续继续扩展的能力面：

- background tasks
- team / peer
- 更强的 autonomy / proactive 行为
- 更完整的 companion / 宠物态外显行为
- 更完整的团队协作与长期任务推进

建议顺序：

- 当前 resident runtime 已打通单脑主链，并允许主脑后续按 profile / runtime 需要运行在 `normal` 或 `coordinator` 模式
- 当前实现不要求把快思考、慢思考、睡眠记忆、companion、Kairos、自主性全部外显接到机器人表现层
- 后续增强可继续扩展 background task、team / peer、Kairos / autonomy 等更完整的行为面
- 再后续可把 companion / 宠物态、长期关系和更强自主性更完整地接入机器人体验

补充原则：

- 这些能力层都属于单脑内部能力，不应在迁移过程中被架构性丢失
- 当前实现可以不把它们全部外显接到 Reachy Mini runtime 行为里
- 但已经保留它们的接入点、模式切换点和后续落位空间

## 16. 落地结果与历史阶段

下面这些阶段是本次改造的实际落地顺序。它们现在主要用于解释历史演进，不再表示“未来计划”。

### 阶段一：切到单脑主链

目标：

- `RuntimeScheduler` 内部接入 `ccmini` 单脑运行时
- 现有 Reachy 执行器继续工作
- 浏览器协议、前端状态机和宿主接口同步切到单脑语义
- 旧 `front/core` 立即退出主推理热路径
- 主脑可按需要运行在 `normal` 或 `coordinator` 模式，而不引入第二颗脑
- 前台仍按 `thread_id` 分 lane
- 后台任务、Kairos、worker 等持续运行能力保持常驻，不因前台线程切换而停止

动作：

- 新增 `ccmini` host adapter
- 用户输入改走 `submit_user_input(...)`
- 工具恢复改走 `submit_tool_results(...)`
- 状态注入改走 `HostEvent`
- 浏览器事件改成单轨 `text_delta / turn_done / thinking / tool_progress`
- 若 profile 需要主脑协调 worker，则宿主支持 `agent.set_mode("coordinator")`

### 阶段二：移除旧 core / front 热路径

目标：

- 旧 `core`、旧 `front` 不再被 runtime 继续引用
- 删除对旧协议、旧状态模型和双脑概念的剩余依赖

动作：

- 停止使用 `publish_user_input / recv_output / publish_tool_results`
- 停止使用 `BrainEvent / BrainOutput` 作为运行时主协议
- 停止使用 `FrontService.handle_user_turn(...)`
- 停止使用 `FrontService.present(...)`
- 停止依赖 `front_model`

### 阶段三：配置收口

目标：

- 从双模型收口成单脑模型配置

动作：

- 直接引入统一 `brain_model` 或等价配置
- 移除 `front_model`
- 移除 `kernel_model`

补充说明：

- 第一阶段完成后，旧 `front/core` 就应退出主推理热路径
- 第二阶段主要是去遗留、去双轨概念，而不是再次切一遍主链
- 收敛成统一 brain provider/model 配置

### 阶段四：清理和删除

目标：

- 物理删除不再需要的旧脑层模块

动作：

- 删除 `front/` 脑层代码
- 删除 `core/` 脑层代码

## 17. 第一阶段最稳的实现目标

这组目标现在都已经成为当前实现的验收结果：

- 浏览器已经理解新的单轨事件协议
- 用户仍然通过同一个 app project 使用系统
- 现有工具执行器不重写
- 现有 UI 已切到 `text_delta / turn_done / thinking / tool_progress`
- 机器人行为不退化
- 内部主链已经换成 `ccmini`

因此，当前仓库里的单脑结构已经不是“迁移中方案”，而是 resident runtime 的实际结构。

## 18. 明确不该做的事

- 不要为了保留前台润色而偷偷保留一个 front model
- 不要一边说只要一个大脑，一边继续维护两套 memory/runtime 协议
- 不要把高频运动控制放进 `ccmini`
- 不要让 `ccmini` 直接知道浏览器消息格式
- 不要第一阶段同时重写前端和大脑
- 不要把旧 `BrainKernel` 的概念硬搬一遍进 `ccmini`

## 19. 一句话总结

Reachy Mini 若要真正实现一个大脑，最正确的方向不是继续修补 `front + core` 双脑，而是：

- 让 `ccmini` 成为唯一认知层
- 让 Reachy Mini 保持宿主、执行、表现、安全职责
- 通过 `Tool + Hook + HostEvent + HostToolResult` 完成接入
- 第一阶段已经完成内部主链替换，并同步收口外部协议
