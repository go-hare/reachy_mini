# Reactive Front Runtime 设计草案

这份文档定义一个新的术语：

- `Reactive Front Runtime`

它用于描述当前 Reachy Mini agent runtime 中，面向用户和机器人的“实时交互层”。

这份文档先只定义角色、边界和设计原则，不进入实现细节。

## 为什么需要这个术语

在当前讨论里，`front` 这个词已经不够精确了。

原因是：

- 它不是单纯的 UI 前端
- 它不是一个“小模型”
- 它也不是 kernel 的弱化版本

真正需要表达的是：

- 这是一个常驻运行时层
- 它面向实时交互
- 它优先做反应而不是长推理
- 它负责把系统延迟转化为交互节奏

因此更准确的名字是：

- `Reactive Front Runtime`

## 一句话定义

`Reactive Front Runtime` 是系统的实时交互壳。

它负责：

- 立即接住用户输入
- 立即驱动机器人可见/可听反馈
- 处理低延迟交互和中断
- 在 kernel 慢速处理期间维持自然交互连续性
- 把后台结果重新组织成面向用户的自然语言呈现

## 它不是什么

它不是：

- 一个必须先调用 LLM 再决定动作的前台模型
- 一个纯 UI 组件
- 一个完整任务规划器
- 一个长期记忆管理器
- 一个多步复杂执行引擎

换句话说：

- 它是 runtime，不是 prompt
- 它是 shell，不是 kernel

## 核心设计判断

`Reactive Front Runtime` 的关键原则是：

- `runtime 决策 + 自然语言输出`

而不是：

- `LLM 结构化决策 + 系统解析执行`

原因很直接：

1. 用户和机器人都需要及时反应
2. 语音中断、表情切换、头部注意力转移不能等 LLM 先思考
3. 如果把前台热路径建立在“先生成结构化结果”上，延迟会显著上升

因此：

- 热路径决策应由 runtime 和规则层完成
- 用户可见输出应保持自然语言
- 语言层可以参与润色，但不应成为前台热路径的唯一控制中心

## 面向用户的输出原则

用户可见输出应该始终是自然语言。

例如：

- “我在，先看一下。”
- “我正在听。”
- “我先帮你停下来。”
- “这个我继续处理，你稍等我一下。”

系统不应该向用户暴露 JSON、状态枚举或内部协议。

## 热路径原则

`Reactive Front Runtime` 的热路径尽量不依赖 LLM。

热路径应优先覆盖这些动作：

- 切换 `listening / replying / idle / interrupted`
- 停止当前播报
- 停止当前动作
- 切换注意姿态
- 输出默认短确认
- 决定是否立即移交 kernel

如果需要更自然的表达：

- 可以在热路径后补一个轻量语言渲染步骤
- 但这个渲染步骤不应阻塞最关键的状态变化和具身反馈

## 建议的内部组成

`Reactive Front Runtime` 建议拆成下面几个子部件：

### 1. Event Intake

负责接收这些事件：

- 用户文本输入
- 用户开始说话
- 用户中途插话
- 用户停止说话
- kernel 进度事件
- tool 完成事件
- idle tick
- 视觉注意力变化

### 2. Reactor

负责基于事件快速决定：

- 当前 phase
- 是否打断回复
- 是否切换姿态/表情
- 是否立即回一句短确认
- 是否把任务交给 kernel

这一层应尽量是低延迟、规则驱动、可测试的逻辑。

### 3. Embodiment Bridge

负责把前台反应同步到机器人侧：

- surface state
- speech motion
- head attention pose
- emotion
- dance / stop

### 4. Utterance Renderer

负责把当前反应状态转成人能接受的自然语言。

它可以有多种实现：

- 固定模板
- 小模型润色
- 大模型风格化输出

但它的职责只应是“怎么说”，而不是“系统现在要做什么”。

### 5. Handoff Manager

负责决定：

- 这一轮是否由 front 直接完成
- 是否转交 kernel
- kernel 返回时如何重新包装成最终呈现

## 推荐职责

`Reactive Front Runtime` 应该负责：

- 即时确认用户输入
- 维持交互节奏
- 低延迟动作和表达
- 快速中断处理
- 前台级简单工具调用
- kernel 结果的最终呈现包装

## 不推荐职责

`Reactive Front Runtime` 不应负责：

- 长链路任务规划
- 多步复杂工具编排
- 后台 run 生命周期管理
- 长期记忆策略
- 系统权限裁决
- 多 agent 调度

这些应由 kernel 或后续独立子系统负责。

## 与 Kernel 的边界

推荐边界如下：

### Front Runtime 负责

- 快速反应
- 交互连续性
- 具身反馈
- 轻量任务直接完成
- 最终自然语言呈现

### Kernel 负责

- 长推理
- 复杂工具链
- run 调度
- 任务切换
- 后台任务
- 记忆整理
- 系统内核状态

一句话概括：

- `Front Runtime` 负责“马上有反应”
- `Kernel` 负责“真正把事情做完”

## 延迟目标

如果把它当成正式子系统，建议给它明确的时延目标。

例如：

- 100ms 内完成 phase 切换
- 300ms 内给出 first ack
- 1s 内若 kernel 尚未完成，给出 progress-style 前台反馈
- 用户插话时优先抢占当前回复

这些目标是 `Reactive Front Runtime` 成立的基础。

## 与当前代码的对应关系

当前代码里，最接近这个概念的是下面三块的组合：

- `RuntimeScheduler`
- `FrontService`
- `EmbodimentCoordinator`

它们现在还没有被明确收口成一个正式子系统，但职责已经相当接近。

## 为什么这个概念重要

如果没有 `Reactive Front Runtime`，系统会退化成：

- 用户说完以后等待一个慢 kernel
- 机器人在等待期间没有存在感
- 一旦大模型慢了，整个体验像“卡死”

而一旦把它单独立起来，系统就能形成更健康的分工：

- `Reactive Front Runtime` 保证“活”
- `Kernel` 保证“成事”

## 当前结论

`front` 在这个项目里不应再被理解成：

- 一个前台 prompt
- 一个前台小模型
- 一个普通 UI 层

更准确的定义应当是：

- `Reactive Front Runtime`

这会成为后续讨论这些问题的基础术语：

- front contract
- kernel 边界
- 即时工具权限
- phase 状态机
- 自然语言渲染层

## 暂不展开的后续问题

这份文档先不细讲：

- 它的具体 API
- 它的事件协议
- phase 枚举
- instant tools / deferred tools 的边界
- kernel progress 如何映射成前台表达

这些建议在术语确认后单独展开。
