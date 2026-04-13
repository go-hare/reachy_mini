# ccmini 单脑架构实施清单

本文是 [ccmini 单脑架构](ccmini-single-brain-design.md) 的实施清单版。

`RuntimeScheduler` 的具体 turn / run / 相位流转草图，见：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

术语定义以设计稿为准：

- [ccmini 单脑架构设计](ccmini-single-brain-design.md)

完整 happy path 和浏览器 payload 示例在状态机草图中：

- [RuntimeScheduler 单脑状态机草图](ccmini-single-brain-runtime-state-machine.md)

目标不是一次性重写全部运行时，而是按阶段把当前双脑 runtime 平滑切到 `ccmini` 单脑，同时保持外部接口不变。

## 0. 总体原则

- [ ] 外部接口不变
- [ ] 浏览器 WebSocket 协议不变
- [ ] app project 结构不变
- [ ] 第一阶段不改前端页面
- [ ] 第一阶段不物理删除 `front/` 和 `core/`
- [ ] 只替换内部推理主链
- [ ] `ccmini.Agent` 成为唯一认知层
- [ ] Reachy Mini 宿主继续负责执行、实时控制、表情、语音、安全

## 1. 架构冻结清单

先把方向固定，避免一边迁移一边继续加深旧架构。

- [ ] 明确停止给 `FrontService` 增加新推理职责
- [ ] 明确停止给 `BrainKernel` 增加新推理职责
- [ ] 明确不再扩展 `front_model` 的新行为
- [ ] 明确不再扩展 `BrainEvent / BrainOutput` 新协议面
- [ ] 明确未来统一以 `ccmini` 宿主接口为准
- [ ] 明确未来只保留一个 brain

## 2. 外部兼容目标清单

第一阶段必须保持这些对外行为不变。

- [ ] `ReachyMiniApp` 外部行为不变
- [ ] `GET /` 保持不变
- [ ] `WS /ws/agent` 保持不变
- [ ] 浏览器继续发送 `user_text`
- [ ] 浏览器继续支持 `user_speech_started / user_speech_stopped`
- [ ] runtime 继续输出 `front_hint_*`
- [ ] runtime 继续输出 `surface_state`
- [ ] runtime 继续输出 `front_final_*`
- [ ] runtime 继续输出 `turn_error`
- [ ] app profile 目录继续使用 `AGENTS.md / USER.md / SOUL.md / TOOLS.md / FRONT.md / config.jsonl`

## 3. 第一阶段核心改造清单

这一阶段的目标是：只换内部大脑，不动外部壳。

### 3.1 在 RuntimeScheduler 中引入 ccmini

- [ ] 在 `RuntimeScheduler` 内新增 resident `ccmini.Agent` 持有逻辑
- [ ] 使用 `create_robot_agent(...)` 或 `create_agent(..., profile="robot_brain")`
- [ ] 显式关闭默认 coding-style 工具装配
- [ ] 宿主自行传入机器人工具集
- [ ] 宿主在 runtime 启动时调用 `await agent.start()`
- [ ] 宿主在 runtime 停止时调用 `await agent.stop()`

### 3.2 替换旧输入主链

旧路径：

- `FrontService.handle_user_turn(...)`
- `BrainKernel.publish_user_input(...)`

新路径：

- `agent.submit_user_input(...)`

清单：

- [ ] 用户文本输入改走 `submit_user_input(...)`
- [ ] 语音转写后的最终文本改走 `submit_user_input(...)`
- [ ] 保留 `thread_id / session_id / user_id`
- [ ] 将旧 `turn_id` 相关逻辑改为使用 `ccmini` 返回的 `turn_id`
- [ ] 把当前 metadata 迁移到 `submit_user_input(..., metadata=...)`

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

- [ ] 在宿主中注册 `agent.on_event(...)`
- [ ] 处理 `TextEvent`
- [ ] 处理 `CompletionEvent`
- [ ] 处理 `ErrorEvent`
- [ ] 处理 `ThinkingEvent`
- [ ] 处理 `ToolProgressEvent`
- [ ] 处理 `ToolUseSummaryEvent`
- [ ] 处理 `PendingToolCallEvent`

### 3.4 宿主最小状态模型

- [ ] 建立 `thread_id -> conversation_id` 的稳定映射
- [ ] 每个 `thread_id` 维护当前激活的 `turn_id`
- [ ] 维护 `run_id -> {thread_id, conversation_id, turn_id}` 映射，用于 tool 恢复
- [ ] 维护每个 `thread_id` 的 `surface_state`
- [ ] 维护每个 `thread_id` 的 `audio_state` 与播放/收音互斥状态
- [ ] 维护动作、跟踪、TTS 等可中断执行句柄
- [ ] 同一线程收到新的用户输入后，将旧 `turn_id` 的前台输出标记为 stale
- [ ] stale 事件只允许用于本地清理，不再继续推送浏览器
- [ ] `submit_tool_results(run_id, ...)` 必须走保存下来的映射恢复，不能猜当前线程
- [ ] 不重新引入任务板、双轨 memory 或 sleep 子系统

## 4. 事件翻译层清单

第一阶段不改浏览器协议，因此需要一层翻译。

### 4.1 ccmini -> 旧浏览器协议

- [ ] `TextEvent` 默认翻译为 `front_final_chunk`，作为实际 assistant 流式输出
- [ ] `CompletionEvent` 翻译为 `front_final_done`，并携带整轮最终全文
- [ ] `front_hint_chunk / front_hint_done` 只用于宿主本地即时确认、fast mode 或后续 speculation；第一阶段允许完全不发
- [ ] `ThinkingEvent` 默认不直接暴露给浏览器，只驱动 `surface_state` 或宿主本地中间态
- [ ] `ToolProgressEvent` 默认不新增公开协议；如确有需要，只翻成短暂 hint 或 system text
- [ ] `PendingToolCallEvent` 不直接透传浏览器
- [ ] `ErrorEvent` 翻译为 `turn_error`
- [ ] 宿主状态变更继续翻译为 `surface_state`
- [ ] 如果已经发过 `front_final_chunk`，则 `front_final_done` 以整轮最终全文收口，前端以 done 为准

### 4.2 兼容策略

- [ ] 第一阶段不要求浏览器知道 `ccmini` 事件名
- [ ] 所有 `ccmini` 内部事件先在宿主侧消化
- [ ] 浏览器仍只看到当前已有协议
- [ ] 浏览器不能把 `front_hint_*` 当成单脑主链的必需事件
- [ ] 不把真实回复流伪装成 `front_hint_*`，避免重新引入双轨语义歧义

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

- [ ] `AGENTS.md` 继续作为硬规则输入
- [ ] `USER.md` 继续作为长期用户上下文
- [ ] `SOUL.md` 继续作为人格基线
- [ ] `TOOLS.md` 继续作为工具边界说明
- [ ] `FRONT.md` 继续作为用户可见风格约束
- [ ] 宿主额外运行时规则通过 `append system prompt` 或 context 注入

明确：

- [ ] `FRONT.md` 保留
- [ ] 但 `FRONT.md` 不再对应一个独立 front-model

## 9. 配置收口清单

当前 `config.jsonl` 里存在：

- `front_model`
- `kernel_model`

迁移目标：

- [ ] 第一阶段先兼容读取旧配置
- [ ] 内部逐步改成只真正使用一套 `ccmini` provider/model 配置
- [ ] 后续新增统一 `brain_model` 或等价配置
- [ ] 最终废弃 `front_model`
- [ ] 最终废弃 `kernel_model`

## 10. front 停用清单

这里不是第一阶段就删，而是先停用主链职责。

- [ ] `FrontService.handle_user_turn(...)` 不再走主链
- [ ] `FrontService.present(...)` 不再走主链
- [ ] `FrontService.reply(...)` 不再走主链
- [ ] `FrontDecision / FrontUserTurnResult` 不再作为内部主协议
- [ ] `front_model` 不再决定热路径输出

保留：

- [ ] `FRONT.md`
- [ ] 必要的宿主表现逻辑
- [ ] 若有可复用的非模型辅助函数，可后续下沉或保留

## 11. core 停用清单

同样先停用热路径，再决定是否物理删除。

- [ ] `BrainKernel` 不再作为 resident brain
- [ ] `publish_user_input(...)` 不再作为主入口
- [ ] `publish_tool_results(...)` 不再作为主恢复入口
- [ ] `recv_output()` 不再作为主输出入口
- [ ] `BrainEvent / BrainOutput` 不再作为主协议
- [ ] `task_type` 路由不再作为必须保留的脑内概念

明确不保留：

- [ ] `src/reachy_mini/core/memory.py` 不迁移、不复用
- [ ] `src/reachy_mini/core/sleep_agent.py` 不迁移旧实现
- [ ] `src/reachy_mini/core/run_store.py` 不迁移、不保留为宿主任务板
- [ ] 如果未来确实需要空闲维护能力，基于 `ccmini` Hook / background task 从需求重新设计，不继承 `sleep_agent.py`
- [ ] 宿主只保留为运行链路服务的最小线程态、surface 态和执行态，不重新长出 `run_store` 式任务板

## 12. 文件级实施清单

下面是建议优先修改的文件。

### 12.1 第一批核心文件

- [ ] `src/reachy_mini/runtime/scheduler.py`
  变成 `ccmini` 宿主编排主入口
- [ ] `src/reachy_mini/apps/app.py`
  保持外部接口不变，内部接新的 runtime 行为
- [ ] `src/reachy_mini/apps/runtime_host.py`
  继续提供 Reachy runtime context 给新工具层
- [ ] `src/reachy_mini/runtime/tool_loader.py`
  改为给 `ccmini` 组装工具
- [ ] `src/reachy_mini/runtime/tools/`
  保留实现，补 `ccmini` 工具包装

### 12.2 第二批过渡文件

- [ ] `src/reachy_mini/front/service.py`
  从主链移除
- [ ] `src/reachy_mini/front/prompt.py`
  风格资产并入统一 prompt 组合
- [ ] `src/reachy_mini/front/events.py`
  停止作为内部主协议
- [ ] `src/reachy_mini/core/agent.py`
  停止作为主 brain
- [ ] `src/reachy_mini/core/models.py`
  停止作为主 runtime 协议
- [ ] `src/reachy_mini/core/resident.py`
  停止作为 resident 主循环

### 12.3 文档与配置文件

- [ ] `docs/source/SDK/integration.md`
  更新为单脑架构文档
- [ ] `profiles/*/profiles/config.jsonl`
  后续收口配置项
- [ ] app README 中的运行链路说明
  从 `front -> BrainKernel -> front` 更新为新结构

## 13. 验证清单

### 13.1 第一阶段必须验证

- [ ] 浏览器可正常连接 `WS /ws/agent`
- [ ] 用户输入仍能拿到回复
- [ ] `front_hint_*` 若启用则仍正常输出；若未启用，浏览器也能正常工作
- [ ] `front_final_chunk` 仍正常输出
- [ ] `front_final_*` 仍正常输出
- [ ] `surface_state` 仍正常输出
- [ ] `turn_error` 仍正常输出
- [ ] 语音输入流程不退化
- [ ] reply audio 和中断逻辑不退化
- [ ] 头部动作、情绪、相机工具仍正常
- [ ] 现有 app project 不需要修改即可运行
- [ ] 同一线程连续打断时，旧 turn 残留事件不会污染新 turn
- [ ] `PendingToolCallEvent` 恢复后的输出不会串到错误线程

### 13.2 第二阶段验证

- [ ] `BrainKernel` 已不再被 runtime 或兼容层引用
- [ ] `FrontService` 已不再被 runtime 或兼容层引用
- [ ] `ccmini` 成为唯一实际推理来源
- [ ] memory/session 不再双轨分裂

### 13.3 回归风险验证

- [ ] 同步文字输入
- [ ] 浏览器语音输入
- [ ] 本地麦克风桥
- [ ] 视觉/头跟踪共存
- [ ] 动作执行中的中断
- [ ] 空闲状态恢复

## 14. 明确禁止事项

- [ ] 不要为了过渡偷偷保留 front-model 做最终润色
- [ ] 不要让旧 `BrainKernel` 和 `ccmini` 同时做主推理
- [ ] 不要把高频控制环交给 `ccmini`
- [ ] 不要让浏览器直接依赖 `ccmini` 事件格式
- [ ] 不要在第一阶段同时重写 UI 和 brain
- [ ] 不要把旧 `core` 的概念整包复制进 `ccmini`
- [ ] 不要以“兼容迁移”为名继续依赖 `core.memory`、`sleep_agent`、`run_store`

## 15. 第一阶段完成定义

第一阶段可以宣告完成，当且仅当：

- [ ] `RuntimeScheduler` 已通过 `ccmini.Agent` 驱动用户主链
- [ ] 现有 Reachy 执行器继续工作
- [ ] 浏览器和外部接口无感知变化
- [ ] 旧 `front/core` 已不再参与主推理热路径（代码目录可暂时保留）
- [ ] 机器人行为和体验未明显退化

达到这个状态后，后续工作就从“架构迁移”变成“清理旧实现”和“逐步增强 ccmini 能力”。
