# Reactive Vision Lane Policy 草案

这份文档专门定义 `Reactive Front Runtime` 里的视频热路径策略。

它回答五个问题：

- reactive vision 到底负责什么，不负责什么
- 它和 semantic vision 的边界在哪里
- 视觉事件应该怎么定义
- 当视觉、说话、显式动作同时发生时，谁优先
- 当前项目已经实现到哪一步，还缺什么

## 一句话定义

`Reactive Vision Lane` 是 front 的视频热路径。

它负责：

- 发现人
- 保持注意
- 调整朝向
- 维持在场感
- 在目标消失后平滑退场

它不负责：

- 图像问答
- 场景理解
- 复杂多步视觉推理

一句话分工：

- reactive vision 负责“先有反应”
- semantic vision 负责“真正看懂”

## 当前项目的真实现状

当前 repo 里，reactive vision 已经有一条真实存在的动作热路径：

1. `HeadTracker` 用 YOLO 找脸
2. `CameraWorker` 持续拉帧并计算 tracking offset
3. `MovementManager` 在控制环里混入 face-tracking offset

这条链路已经能工作。

但它目前更像：

- 视觉直接驱动动作

而还不是：

- 视觉事件先进入 front，再由 front 决策驱动动作

也就是说，当前项目已经有 reactive vision hot path，
但 front-level vision event bus 还没有完全补齐。

## 当前项目的另一条视觉链路

同时，当前项目也已经有 semantic vision：

1. `camera` tool 先抓一帧
2. `VisionProcessor` 进入本地视觉模型推理
3. 返回图像描述或问答文本

这条链路天然属于慢路径。

所以当前代码已经证明了两件事：

1. 视频确实应该拆成两层
2. 这两层现在已经部分分开实现了

## 当前 hot path 的已知行为

当前 reactive vision 热路径有几个非常明确的行为参数。

### 1. 高频轮询

`CameraWorker` 当前以约 `30Hz` 轮询摄像头帧。

这说明它从设计上就是热路径，而不是异步问答工具。

### 2. 目标选择

YOLO tracker 当前会在候选脸中选择“综合分数最好”的目标。

分数由两部分构成：

- 置信度
- 框面积

这意味着当前默认偏向：

- 更可信
- 更接近镜头

而不是做多目标长期跟踪。

### 3. 丢目标后的退场

当前实现不是一丢脸就瞬间回中。

而是：

- 先等待 `2.0s`
- 再用 `1.0s` 做平滑插值回中

这很符合 reactive vision 的基本原则：

- 不要抖
- 不要突兀
- 宁可短暂停留，也不要频繁抽动

## Reactive Vision 的职责边界

推荐把 reactive vision 的职责收口在下面这些动作。

### 应负责

- `person_present`
- `person_lost`
- `target_locked`
- `target_released`
- `face_direction`
- `tracking_enabled`
- `tracking_disabled`
- `engagement_changed`

### 不应负责

- “这个人是谁”
- “桌上有什么”
- “画面里发生了什么”
- “根据这张图给出建议”
- “结合历史记忆推断用户意图”

如果一个需求必须经过视觉语义理解，它就不该留在 reactive lane。

## 推荐事件模型

建议 future reactive vision 至少发出下面这组轻事件。

| Event | 含义 | 典型 metadata |
| --- | --- | --- |
| `person_detected` | 视野里首次出现可跟踪目标 | `confidence`, `target_id` |
| `person_lost` | 原目标持续丢失 | `lost_for_ms` |
| `attention_acquired` | 系统决定把注意力交给当前目标 | `direction`, `target_id` |
| `attention_shifted` | 注意力从旧目标切到新目标 | `from_target`, `to_target`, `direction` |
| `attention_released` | 不再保持视觉关注 | `reason` |
| `tracking_state_changed` | 跟踪开关变化 | `tracking_enabled` |
| `engagement_changed` | 在场感/参与度变化 | `engagement` |

这些 event 都应该是：

- 小
- 快
- 可恢复
- 不依赖 LLM

## 推荐优先级栈

如果把 reactive vision 正式接入 front，总体优先级建议如下。

### P0. Safety / Explicit Stop

任何显式停止、紧急取消、人工覆盖都高于视觉跟随。

### P1. 用户开口与插话

当用户开始说话时，系统首先要“把地板让给用户”。

这时 reactive vision 可以保留轻量 attention，
但不应触发明显抢眼的外显动作。

换句话说：

- 可以轻微保持看向用户
- 不可以让视觉跟随压过 listening posture

### P2. 显式动作声明权

显式动作包括：

- `move_head`
- `play_emotion`
- `dance`

当前 `EmbodimentCoordinator` 已经实现了这层语义：

- 显式动作声明身体控制权
- head tracking 在显式动作期间被延后或关闭

这条规则应该保留，并且成为正式政策。

### P3. Replying / Speech Motion

系统在说话时，reactive vision 可以继续维持“软关注”，
但不应触发会打断播报姿态的高幅度抢占动作。

也就是说：

- 回复时允许弱跟随
- 不允许视觉热路径夺走 reply 的主表现权

### P4. Reactive Vision Attention

当没有更高优先级占用时，reactive vision 可以：

- 跟随目标
- 微调 gaze
- 保持 presence

### P5. Idle Look-around

idle look-around 的优先级应低于真实人的出现。

如果视野里有人：

- idle scan 应立刻让位给真正的 attention

## 推荐行为规则

### 规则 1：Reactive Vision 只做可逆动作

它只能做：

- gaze 调整
- head tracking 开关
- attention phase 切换
- presence 更新

不能直接做复杂长动作决策。

### 规则 2：只维护一个前台主目标

在 front 热路径里，推荐始终只维护一个 primary target。

多人场景下，不在 hot path 里做复杂社交分配。

默认规则可以很简单：

1. 置信度不合格直接忽略
2. 在候选里优先当前已锁定目标
3. 否则选综合分数最高者

这和当前 YOLO tracker 的单目标选择思路一致。

### 规则 3：不要让边缘抖动触发频繁切头

只有当方向变化超过阈值、且持续一定时间后，
才应该从“内部 offset 更新”升级为“front attention event”。

否则就会出现：

- 事件风暴
- 头部抖动
- 前台状态来回闪

### 规则 4：目标丢失应分两段处理

推荐保留当前两段式逻辑：

1. 短 hold
2. 平滑回中

这样视觉退出时会显得更像“失去注意力”，而不是“检测失败”。

### 规则 5：semantic vision 永远不进入 hot lane

只要请求变成下面这种形式，就必须 handoff：

- “你看到了什么？”
- “这张图里有什么物体？”
- “帮我判断图像内容”

这类请求走：

- frame capture
- semantic model
- kernel presentation

而不是 reactive vision。

## front 与 reactive vision 的正确接口

未来最健康的接法不是让 `CameraWorker` 直接替 front 做完所有事情。

而是拆成两段：

### A. Sensor / Tracking Driver

负责：

- 取帧
- 跑 detector
- 算出目标位置
- 维护 tracking 基础状态

### B. Front Vision Reactor

负责：

- 把 tracking 状态压缩成前台事件
- 决定是否切换 `attending`
- 决定是否发出 `move_head`
- 决定是否开关 `head_tracking`
- 决定是否通知 UI / shell

这符合 `Reactive Front Runtime` 的定义：

- 传感层不直接替 front 决策
- front 自己拥有交互语义

## 当前代码和目标架构之间的缺口

当前已经有：

- YOLO tracking
- camera polling
- head tracking enable/disable
- explicit motion 抢占 tracking
- face lost 平滑退场

当前还缺：

1. 稳定的 `vision_attention_updated` 生产事件
2. front 视角下的 reactive vision event schema
3. `attending` phase 的系统级落地
4. 从 direct offsets 到 front-visible attention state 的统一桥接

## 最推荐的迁移顺序

这块最怕一步到位过度抽象。

按 KISS 原则，建议只走三步。

### 第一步

保留现有 `CameraWorker -> MovementManager` 热路径不动。

原因：

- 它已经能工作
- 它延迟足够低
- 现在不该为了“架构纯洁”破坏实时性

### 第二步

在这条热路径旁边，补一个轻量的 front vision event emitter。

先只发：

- `person_detected`
- `person_lost`
- `attention_acquired`
- `attention_released`

不要一开始就设计复杂多目标协议。

### 第三步

等 event 流稳定后，再决定：

- `attending` 是否进入 stable phase set
- UI 是否显示 visual attention
- front 是否要用语言对 attention 做轻量外显

## 当前结论

当前项目已经证明：

- 视频不是附属能力
- 它已经是 front 的一部分

但当前 front 和视频的关系还处在中间态：

- 动作热路径已经有了
- front 事件总线还没补全

所以最合理的下一步不是推翻现状，而是：

- 保留今天的 direct reactive loop
- 在它上面长出 front-owned vision events
- 再逐步把 `attending` 和 visual presence 提升为正式协议
