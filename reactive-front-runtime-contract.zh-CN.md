# Reactive Front Runtime Contract 草案

这份文档回答一个更具体的问题：

- `Reactive Front Runtime` 到底接什么输入？
- 它自己能立刻做什么？
- 什么必须交给 kernel？
- 视频在里面属于哪一层？

这一版先定义 contract，不定义具体类名和目录实现。

## 一句话定义

`Reactive Front Runtime` 是系统的实时多模态反应层。

它直接消费：

- 文本事件
- 语音事件
- 视频事件
- kernel 进度事件

它直接产出：

- 自然语言短反馈
- embodiment/surface 变化
- 中断与抢占
- kernel handoff

关键前提只有一个：

- `runtime 决策 + 自然语言输出`

而不是：

- `LLM 先产结构化结果，再由前台解析执行`

## 为什么视频必须单独定义

一旦 front 包含视频，它就不再只是“语音前台”。

在机器人场景里，视频至少承担两种完全不同的职责：

1. 实时注意力和在场感
2. 语义理解和视觉问答

这两者的时延预算、失败模式、实现方式都不一样。

如果把它们混成一条链路，就会出现两个问题：

1. 实时反馈被慢视觉理解拖住
2. 语义视觉被错误地放进高频控制热路径

因此视频必须明确拆成两条 lane。

## 两条视频链路

### 1. Reactive Vision Lane

这是 front 的视频热路径。

它负责的是“立刻反应”，不是“深入理解”。

典型职责：

- 是否有人出现
- 人是否还在视野里
- 人大致在画面什么位置
- 机器人是否应该转头或维持关注
- 是否需要从跟随状态回到中位
- 是否触发 presence / engagement 变化

这一层的输出应该是 runtime-native 的轻信号，例如：

- `person_detected`
- `person_lost`
- `face_center_changed`
- `attention_acquired`
- `attention_released`
- `tracking_enabled`
- `tracking_disabled`

它不依赖 LLM 生成结构化结果。
它也不追求语言解释能力。
它只负责把视觉输入压缩成可立即消费的反应信号。

### 2. Semantic Vision Lane

这是视觉慢路径。

它负责的是“看懂并回答”，不是“先反应”。

典型职责：

- 用户问摄像头里有什么
- 对当前画面做问答
- 识别场景、物体、关系
- 支持多步 multimodal reasoning
- 为 kernel 提供视觉上下文

这一层可以使用本地视觉模型，也可以以后接更强的多模态模型。

它的输出不是高频控制信号，而是：

- 视觉描述
- 语义观察结果
- 可被 kernel 消费的视觉事实

## 当前项目里已经存在的对应实现

当前代码其实已经有这个分层，只是还没有正式命名。

### Reactive Vision 已经存在

YOLO head tracker 在这里：

- [src/reachy_mini/runtime/vision/yolo_head_tracker.py](/Users/apple/work-py/reachy_mini/src/reachy_mini/runtime/vision/yolo_head_tracker.py)

它负责：

- 检测脸
- 选择当前最合适的目标脸
- 返回归一化位置

Camera worker 在这里：

- [src/reachy_mini/runtime/camera_worker.py](/Users/apple/work-py/reachy_mini/src/reachy_mini/runtime/camera_worker.py)

它负责：

- 持续轮询摄像头帧
- 在热路径里调用 tracker
- 生成头部跟踪 offset
- 丢脸后平滑回中

Movement manager 在这里消费这些 offset：

- [src/reachy_mini/runtime/moves.py](/Users/apple/work-py/reachy_mini/src/reachy_mini/runtime/moves.py)

这说明当前项目已经有一条“视频输入 -> 实时姿态反应”的热路径。
这条路径本质上就属于 `Reactive Front Runtime`。

### Semantic Vision 也已经存在

本地视觉处理器在这里：

- [src/reachy_mini/runtime/vision/processors.py](/Users/apple/work-py/reachy_mini/src/reachy_mini/runtime/vision/processors.py)

摄像头工具在这里：

- [src/reachy_mini/runtime/tools/reachy_tools.py](/Users/apple/work-py/reachy_mini/src/reachy_mini/runtime/tools/reachy_tools.py)

这条路径的特点是：

- 先拿一帧图像
- 再进入视觉模型推理
- 最后返回文字描述或问答结果

这明显不是前台热反应回路，而是语义视觉慢路径。

## 输入 Contract

`Reactive Front Runtime` 至少应接四类输入。

### 1. Text Input

- 用户文本消息
- UI 点击产生的短指令
- 来自 shell 的快捷命令

### 2. Speech Input

- 用户开始说话
- 用户持续说话
- 用户停止说话
- 用户插话
- ASR partial/final

### 3. Reactive Video Input

- person present / absent
- face center
- attention gained / lost
- engagement changed
- visual interruption hint

### 4. Kernel / Runtime Input

- kernel started
- tool running
- tool finished
- reply ready
- background still working
- handoff failed

## 输出 Contract

`Reactive Front Runtime` 不应该直接输出面向外部的 JSON 协议给用户。

用户可见层永远是自然语言和具身反应。

但在系统内部，它至少应该能发出四类结果：

### 1. Natural-Language Utterance

例如：

- “我在。”
- “我先看一下。”
- “你等我两秒，我继续。”
- “我先停一下当前动作。”

### 2. Embodiment Patch

例如：

- 切换 surface phase
- 改变 gaze / head attention
- 停止播报
- 停止当前动作
- 开关 head tracking

### 3. Control Decision

例如：

- 抢占当前 reply
- 丢弃旧的 short ack
- 合并连续事件
- 延后某个动作直到 motion settle

### 4. Kernel Handoff

例如：

- 把复杂问题转交 kernel
- 发起 semantic vision request
- 发起多步工具链
- 请求后台持续处理

## Front 可直接做的事情

这些动作应该属于 front 的即时权限：

- 切 phase
- 立刻停止当前播报
- 立刻停止当前表层动作
- 输出一个短确认
- 改变关注姿态
- 开启/关闭 head tracking
- 根据视觉 presence 更新机器人“在场感”

这类动作的特点是：

- 低风险
- 低延迟
- 无需长推理
- 即使做错，代价也可恢复

## 必须交给 Kernel 的事情

下面这些不应该走 front 热路径：

- 摄像头问答
- 复杂图像理解
- 多步视觉推理
- 跨轮任务规划
- 需要长期记忆参与的判断
- 多工具链联合执行

也就是说：

- front 可以“先接住”
- kernel 才负责“真正做完”

## Phase State Machine 建议

建议 front 至少有下面几个相位：

- `idle`
- `attending`
- `listening`
- `acknowledging`
- `thinking_visible`
- `speaking`
- `interrupted`
- `recovering`

其中视频最直接影响的是：

- `idle -> attending`
- `attending -> listening`
- `speaking -> interrupted`
- `attending -> idle`

也就是说，视频首先改变的不是“语义答案”，而是“交互相位”。

## 与自然语言输出的关系

用户前面提的那个点是对的：

- front 的输出应该是自然语言
- 不应该为了前台响应去等 LLM 产 JSON

更准确地说：

- front 的决策核心是 runtime state machine
- front 的对外呈现是自然语言

如果后面要上语言润色，也应该是：

- 决策先发生
- 语言后渲染

而不是反过来。

## 对当前代码的架构归属建议

如果沿着 `Reactive Front Runtime` 收敛，视频建议这样归属：

### 属于 Front 热路径

- `camera_worker`
- `yolo_head_tracker`
- head tracking enable/disable
- presence / attention / engagement 事件
- 基于视频的即时姿态调整

### 属于 Kernel 或 Deferred Vision

- `VisionProcessor`
- `camera` tool 的语义问答
- 图像描述
- 场景理解
- 多步视觉 reasoning

## 一个更准确的心智模型

前台视频不是“视觉版的大脑”。

它更像：

- 视觉反射
- 注意力系统
- 在场感驱动器

而语义视觉才更接近：

- 看图理解
- 视觉推理
- 图像问答

所以更准确的分工是：

- `Reactive Front Runtime` 管“看见了就先有反应”
- `Kernel` 管“看懂以后把事情做完”

## 这份 Contract 对后续设计的意义

如果接受这个 contract，后面 front 的设计就会稳定很多：

1. 不会再把视频全塞进 LLM
2. 不会再把前台热路径和语义视觉混在一起
3. front 可以明确走多模态 event-driven runtime
4. kernel 继续保留复杂推理和工具编排职责

## 当前结论

当前项目已经具备 `Reactive Front Runtime` 的视频雏形。

而且这个雏形已经证明一件事：

- front 不是纯语音层
- 它天然应该是 text + speech + reactive video 的实时运行时

下一步如果继续写文档，最值得补的是两份：

- front phase/event 详细协议
- reactive vision lane 的事件模型与优先级规则
