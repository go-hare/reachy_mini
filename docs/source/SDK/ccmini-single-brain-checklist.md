# ccmini 单脑架构落地核对清单

本文是 [ccmini 单脑架构](ccmini-single-brain-design.md) 的落地核对版。

`RuntimeScheduler` 的具体 turn / run / 相位流转草图，见：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

术语定义以设计稿为准：

- [ccmini 单脑架构设计](ccmini-single-brain-design.md)

完整 happy path 和浏览器 payload 示例在状态机草图中：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

这份文档保留为落地核对清单。当前 resident runtime 已经切到 `ccmini` 单脑，下面这些条目主要用于确认哪些目标已经达成，以及后续还有哪些增强项可继续做。

## 0. 总体原则

- [x] 以单脑主链为中心直接切换，不以兼容旧协议为前提
- [x] app project 结构尽量保持稳定，但允许同步调整宿主接口和前端页面
- [x] 浏览器 WebSocket 协议允许破坏式调整
- [x] 直接停用并删除 `front/` 和旧 `core` 脑层热路径
- [x] 同步替换内部推理主链和浏览器事件模型
- [x] `ccmini.Agent` 成为唯一认知层
- [x] `coordinator` 作为 `ccmini.Agent` 的内部模式保留，不视为第二颗脑
- [x] “单脑”明确定义为单一认知实现，而不是全局单前台 turn
- [x] 前台继续按 `thread_id` 分 lane
- [x] 后台任务 / worker / Kairos / memory 持续常驻，不因某个前台线程切换而停止
- [x] Reachy Mini 宿主继续负责执行、实时控制、表情、语音、安全

## 1. 架构冻结结果

这部分对应迁移期冻结项，当前都已收口完成。

- [x] 明确停止给 `FrontService` 增加新推理职责
- [x] 明确停止给 `BrainKernel` 增加新推理职责
- [x] 明确不再扩展 `front_model` 的新行为
- [x] 明确不再扩展 `BrainEvent / BrainOutput` 新协议面
- [x] 明确统一以 `ccmini` 宿主接口为准
- [x] 明确只保留一个 brain

## 2. 直接切换目标结果

这部分目标已经体现在当前实现里。

- [x] `RuntimeScheduler` 对浏览器直接输出单轨事件协议
- [x] 浏览器主回复流改为 `text_delta`
- [x] 单轮收口事件改为 `turn_done`
- [x] 错误事件统一为 `turn_error`
- [x] 宿主生命周期状态继续通过 `surface_state` 输出
- [x] 宿主 ASR 预览继续通过 `speech_preview` 输出
- [x] `ThinkingEvent` / `ToolProgressEvent` 可直接暴露为 `thinking` / `tool_progress`
- [x] `front_hint_*` / `front_final_*` 退出协议面
- [x] `ChatResponse` 改成单脑语义，不再保留 `front_decision` 这类双脑遗留字段
- [x] app profile 目录继续使用 `AGENTS.md / USER.md / SOUL.md / TOOLS.md / FRONT.md / config.jsonl`

## 3. 核心改造结果

这一部分记录已经完成的主链改造。

### 3.1 在 RuntimeScheduler 中引入 ccmini

- [x] 在 `RuntimeScheduler` 内新增 resident `ccmini` 单脑运行时接入逻辑
- [x] 使用 `create_robot_agent(...)` 或 `create_agent(..., profile="robot_brain")`
- [x] 显式关闭默认 coding-style 工具装配
- [x] 宿主自行传入机器人工具集
- [x] 宿主在 runtime 启动时调用 `await agent.start()`
- [x] 宿主在 runtime 停止时调用 `await agent.stop()`
- [ ] 宿主支持按 profile / runtime 配置切换 `agent.set_mode("normal" | "coordinator")`
- [ ] 若启用 `coordinator`，worker delegation / background task 仍视为主脑内部能力，不引入第二脑链路
- [x] 第一阶段不引入“全局唯一活跃前台 turn”语义
- [x] 第一阶段保持与当前 `front` 一样的 per-thread 前台 lane 语义

### 3.2 替换旧输入主链

旧路径：

- `FrontService.handle_user_turn(...)`
- `BrainKernel.publish_user_input(...)`

新路径：

- `agent.submit_user_input(...)`

清单：

- [x] 用户文本输入改走 `submit_user_input(...)`
- [x] 语音转写后的最终文本改走 `submit_user_input(...)`
- [x] 保留 `thread_id / session_id / user_id`
- [x] 将旧 `turn_id` 相关逻辑改为使用 `ccmini` 返回的 `turn_id`
- [x] 把当前 metadata 迁移到 `submit_user_input(..., metadata=...)`

### 3.3 替换旧输出主链

旧路径：

- `BrainKernel.recv_output()`
- `FrontService.present(...)`

新路径：

- `agent.on_event(...)`
- `wait_event()` / `drain_events()`

补充约定：

- `RuntimeScheduler` 这种常驻 runtime 主路径优先使用 `submit_user_input(...)` + 事件队列
- `query()` 更适合单次脚本、bridge/demo host 或测试，不作为常驻主循环的默认组织方式

清单：

- [x] 在宿主中注册 `agent.on_event(...)`
- [x] 处理 `TextEvent`
- [x] 处理 `CompletionEvent`
- [x] 处理 `ErrorEvent`
- [x] 处理 `ThinkingEvent`
- [x] 处理 `ToolProgressEvent`
- [x] 处理 `ToolUseSummaryEvent`
- [x] 处理 `PendingToolCallEvent`

### 3.4 宿主最小状态模型

- [ ] 建立 `thread_id -> conversation_id` 的稳定映射
- [ ] 每个 `thread_id` 维护当前激活的 `turn_id`
- [ ] 维护 `run_id -> {thread_id, conversation_id, turn_id}` 映射，用于 tool 恢复
- [ ] 维护每个 `thread_id` 的 `surface_state`
- [ ] 维护每个 `thread_id` 的 `audio_state` 与播放/收音互斥状态
- [ ] 维护动作、跟踪、TTS 等可中断执行句柄
- [ ] 同一线程收到新的用户输入后，将旧 `turn_id` 的前台输出标记为 stale
- [ ] 某个线程的新输入只会让该线程自己的旧 `turn_id` stale，其他线程前台 lane 不受影响
- [ ] stale 事件只允许用于本地清理，不再继续推送浏览器
- [ ] `submit_tool_results(run_id, ...)` 必须走保存下来的映射恢复，不能猜当前线程
- [ ] 不重新引入任务板、双轨 memory 或 sleep 子系统
- [ ] 若当前 `ccmini` continuation 仍是单 pending-client-run 槽位，宿主第一阶段按现状适配，不假设已支持多 run 并发恢复
- [ ] 后台任务、worker、Kairos、memory 等常驻能力不因某个前台线程 stale 或切换而停止

## 4. 事件翻译层清单

第一阶段直接改浏览器协议，因此重点是定义单脑事件契约，而不是翻译旧协议。

### 4.1 ccmini -> 浏览器单轨协议

- [x] 宿主继续输出 `speech_preview`，服务实时转写预览
- [x] `TextEvent` 直接输出为 `text_delta`
- [x] `CompletionEvent` 直接输出为 `turn_done`，并携带整轮最终全文
- [x] `ThinkingEvent` 按需直接输出为 `thinking`
- [x] `ToolProgressEvent` 按需直接输出为 `tool_progress`
- [x] `PendingToolCallEvent` 不直接透传浏览器
- [x] `ErrorEvent` 输出为 `turn_error`
- [x] 宿主状态变更输出为 `surface_state`
- [x] 如果已经发过 `text_delta`，则 `turn_done` 以整轮最终全文收口，前端以 done 为准

### 4.2 协议约束

- [x] 浏览器直接理解单脑事件名
- [x] 所有 `ccmini` 内部事件先在宿主侧消化
- [x] `text_delta` 是唯一真实主回复流
- [x] 不再保留 `front_hint_*` / `front_final_*` 双轨语义
- [x] `thinking` / `tool_progress` 不能承载真实 assistant 文本流

## 5. 工具接入清单

`ccmini` 的机器人接法以 `Tool`/`ClientTool` 为主边界。

### 5.1 现有工具复用

- [ ] 复用现有 Reachy 执行层
- [ ] 不重写 movement manager
- [ ] 不重写 speech driver
- [ ] 不重写 embodiment coordinator
- [ ] 不重写 camera/vision runtime

### 5.2 工具包装

建议优先暴露的工具：

- [ ] `speak`
- [ ] `move_head`
- [ ] `look_at`
- [ ] `play_emotion`
- [ ] `dance`
- [ ] `head_tracking`
- [ ] `camera`
- [ ] `stop_motion`
- [ ] `wake_up`
- [ ] `goto_sleep`
- [ ] `set_interaction_mode`（可选）

### 5.3 工具契约

- [ ] 区分一次性工具、模式切换工具、长时可中断工具
- [ ] `dance`、`head_tracking` 明确为长时工具，不把控制环阻塞在模型推理里
- [ ] 长时工具优先返回 `queued` / `started`，后续再体现 `completed` / `stopped`
- [ ] `stop_motion` 作为显式中断工具，负责停止当前动作、跟踪或排队中的运动
- [ ] `stop_motion` 设计为幂等操作
- [ ] 工具互斥、排队、取消、安全裁决都由宿主负责

### 5.4 明确不暴露

- [ ] 不暴露原始关节写入
- [ ] 不暴露高频 `set_target()` 连续流
- [ ] 不暴露原始 PID / 扭矩参数写入
- [ ] 不暴露原始传感器流

### 5.5 工具恢复链

- [ ] 宿主监听 `PendingToolCallEvent`
- [ ] 根据 `tool_name / tool_input` 调现有 Reachy 执行器
- [ ] 执行结果包装成 `HostToolResult`
- [ ] 调用 `agent.submit_tool_results(...)`
- [ ] 继续消费后续事件直到本轮完成
- [ ] 长时工具的恢复结果能正确回到原 `thread_id / turn_id / run_id`

## 6. 宿主本地规则清单

这些能力应明确保留在宿主，不送进 `ccmini` 做推理。

### 6.1 UI / 生命周期状态

- [ ] `listening`
- [ ] `listening_wait`
- [ ] `replying`
- [ ] `settling`
- [ ] `idle`

### 6.2 语音与打断

- [ ] 用户开口立刻打断 reply audio
- [ ] 播放中收音互斥
- [ ] 播放结束后的回落节奏
- [ ] 语音播放冷却期控制

### 6.3 表情 / Surface 即时切换

- [ ] 用户说话时清理 flashy expression
- [ ] 立即切 listening surface
- [ ] 结束说话切 listening_wait
- [ ] 回复结束切 settling / idle

### 6.4 高频与安全

- [ ] 头部跟踪实时环仍在宿主
- [ ] 音频流处理仍在宿主
- [ ] 相机实时采集仍在宿主
- [ ] 最终执行安全裁决仍在宿主

## 7. 状态注入清单

旧 runtime 有大量内部状态。迁移时不应丢掉，但要换成 `ccmini` 可理解的注入方式。

### 7.1 HostEvent 注入

适合注入：

- [ ] `sensor_summary`
- [ ] `speech_started`
- [ ] `speech_stopped`
- [ ] `mode_changed`
- [ ] `vision_attention_summary`
- [ ] `surface_summary`
- [ ] `safety_state`

### 7.2 Hook 注入

- [ ] `PreQueryHook` 注入摘要化机器人状态
- [ ] `PreToolUseHook` 做运动类安全闸
- [ ] `IdleHook` 做空闲维护和低频状态刷新
- [ ] `SessionStartHook` 做资源申请
- [ ] `SessionEndHook` 做资源释放
- [ ] `StopHook` 做急停/低电/保护

### 7.3 注入约束

- [ ] 不注入高频原始传感器流
- [ ] 不注入每帧图像
- [ ] 不注入大体积系统日志
- [ ] 所有注入内容都先摘要化

## 8. Prompt 资产迁移清单

只保留一个大脑后，prompt 资产要统一进入 `ccmini`。

- [x] `AGENTS.md` 继续作为硬规则输入
- [x] `USER.md` 继续作为长期用户上下文
- [x] `SOUL.md` 继续作为人格基线
- [x] `TOOLS.md` 继续作为工具边界说明
- [x] `FRONT.md` 继续作为用户可见风格约束
- [x] 宿主额外运行时规则通过 `append system prompt` 或 context 注入
- [ ] 快思考 / 慢思考 / 睡眠记忆 / companion / Kairos / autonomy 等能力层不因迁移而被架构性丢失

明确：

- [x] `FRONT.md` 保留
- [x] 但 `FRONT.md` 不再对应一个独立 front-model

## 9. 配置收口清单

当前 `config.jsonl` 里存在：

- `front_model`
- `kernel_model`

迁移目标：

- [x] 第一阶段直接改成只真正使用一套 `ccmini` provider/model 配置
- [x] 新增统一 `brain_model` 或等价配置
- [x] 删除 `front_model`
- [x] 删除 `kernel_model`

## 10. front 停用清单

这里不是第一阶段就删，而是先停用主链职责。

- [x] `FrontService.handle_user_turn(...)` 不再走主链
- [x] `FrontService.present(...)` 不再走主链
- [x] `FrontService.reply(...)` 不再走主链
- [x] `FrontDecision / FrontUserTurnResult` 不再作为内部主协议
- [x] `front_model` 不再决定热路径输出

保留：

- [x] `FRONT.md`
- [x] 必要的宿主表现逻辑
- [ ] 若有可复用的非模型辅助函数，可后续下沉或保留

## 11. core 停用清单

同样先停用热路径，再决定是否物理删除。

- [x] `BrainKernel` 不再作为 resident brain
- [x] `publish_user_input(...)` 不再作为主入口
- [x] `publish_tool_results(...)` 不再作为主恢复入口
- [x] `recv_output()` 不再作为主输出入口
- [x] `BrainEvent / BrainOutput` 不再作为主协议
- [ ] `task_type` 路由不再作为必须保留的脑内概念

明确不保留：

- [ ] `src/reachy_mini/core/memory.py` 不迁移、不复用
- [x] `src/reachy_mini/core/sleep_agent.py` 不迁移旧实现
- [x] `src/reachy_mini/core/run_store.py` 不迁移、不保留为宿主任务板
- [ ] 如果未来确实需要空闲维护能力，基于 `ccmini` Hook / background task 从需求重新设计，不继承 `sleep_agent.py`
- [x] 宿主只保留为运行链路服务的最小线程态、surface 态和执行态，不重新长出 `run_store` 式任务板

## 12. 文件级实施清单

下面是建议优先修改的文件。

### 12.1 第一批核心文件

- [x] `src/reachy_mini/runtime/scheduler.py`
  变成 `ccmini` 宿主编排主入口
- [x] `src/reachy_mini/apps/app.py`
  保持外部接口不变，内部接新的 runtime 行为
- [x] `src/reachy_mini/apps/runtime_host.py`
  继续提供 Reachy runtime context 给新工具层
- [x] `src/reachy_mini/runtime/tool_loader.py`
  改为给 `ccmini` 组装工具
- [x] `src/reachy_mini/runtime/tools/`
  保留实现，补 `ccmini` 工具包装

### 12.2 第二批过渡文件

- [x] `src/reachy_mini/front/service.py`
  从主链移除
- [x] `src/reachy_mini/front/prompt.py`
  风格资产并入统一 prompt 组合
- [x] `src/reachy_mini/front/events.py`
  停止作为内部主协议
- [x] `src/reachy_mini/core/agent.py`
  停止作为主 brain
- [x] `src/reachy_mini/core/models.py`
  停止作为主 runtime 协议
- [x] `src/reachy_mini/core/resident.py`
  停止作为 resident 主循环

### 12.3 文档与配置文件

- [x] `docs/source/SDK/integration.md`
  更新为单脑架构文档
- [x] `profiles/*/profiles/config.jsonl`
  后续收口配置项
- [x] app README 中的运行链路说明
  从 `front -> BrainKernel -> front` 更新为新结构

## 13. 验证清单

### 13.1 当前实现已验证

- [x] 浏览器可正常连接新的 `WS /ws/agent`
- [x] 用户输入仍能拿到回复
- [x] `speech_preview` 仍正常输出
- [x] `text_delta` 仍正常输出
- [x] `turn_done` 仍正常输出
- [x] `surface_state` 仍正常输出
- [ ] `turn_error` 仍正常输出
- [x] `thinking` / `tool_progress` 若启用则输出正常
- [ ] 语音输入流程不退化
- [ ] reply audio 和中断逻辑不退化
- [ ] 头部动作、情绪、相机工具仍正常
- [x] 前端已切到单脑事件模型后可正常运行
- [x] `ChatResponse` 已切到单脑语义后可正常返回
- [x] 同一线程连续打断时，旧 turn 残留事件不会污染新 turn
- [x] A 线程的新输入不会让 B 线程的前台 lane 进入 stale
- [ ] `PendingToolCallEvent` 恢复后的输出不会串到错误线程
- [ ] `coordinator` 模式下主脑仍可正常输出流式回复，不因 worker 派工阻塞前台
- [ ] `coordinator` 模式下后台 worker/task notification 不会绕过当前 `turn_id` 裁决
- [ ] 后台任务、Kairos、worker 在前台线程切换后仍持续运行
- [ ] 快思考 / 慢思考 / 睡眠记忆 / companion / Kairos / autonomy 等能力层的入口没有被迁移意外切断

### 13.2 已完成的收口验证

- [x] `BrainKernel` 已不再被 runtime 引用
- [x] `FrontService` 已不再被 runtime 引用
- [x] `ccmini` 成为唯一实际推理来源
- [x] memory/session 不再双轨分裂

### 13.3 回归风险验证

- [ ] 同步文字输入
- [ ] 浏览器语音输入
- [ ] 本地麦克风桥
- [ ] 视觉/头跟踪共存
- [ ] 动作执行中的中断
- [ ] 空闲状态恢复

## 14. 明确禁止事项

- [x] 不要为了过渡偷偷保留 front-model 做最终润色
- [x] 不要让旧 `BrainKernel` 和 `ccmini` 同时做主推理
- [x] 不要把高频控制环交给 `ccmini`
- [x] 不要在第一阶段同时重写 UI 和 brain
- [x] 不要把旧 `core` 的概念整包复制进 `ccmini`
- [x] 不要以“临时过渡”为名继续依赖 `core.memory`、`sleep_agent`、`run_store`
- [x] 不要重新造 `front_hint_*` / `front_final_*` 这类双轨协议
- [x] 不要把 `coordinator` 描述成外挂服务或第二颗脑

## 15. 当前完成定义

当前 resident runtime 可以视为单脑改造已完成，当且仅当：

- [x] `RuntimeScheduler` 已通过 `ccmini.Agent` 驱动用户主链
- [x] 现有 Reachy 执行器继续工作
- [x] 浏览器、宿主接口和 `ChatResponse` 已全部切到单脑语义
- [x] 旧 `front/core` 已不再参与主推理热路径，旧脑层代码目录也已删除或收缩为 utility
- [ ] 机器人行为和体验未明显退化

达到这个状态后，后续工作就从“架构迁移”变成“清理旧实现”和“逐步增强 ccmini 能力”。
