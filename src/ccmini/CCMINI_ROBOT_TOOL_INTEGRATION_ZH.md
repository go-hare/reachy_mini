# ccmini 机器人 Tool 接入约定

这份文档面向这样的宿主：

- 机器人侧已经有统一的工具输入格式
- 旧 core 也是通过 `tool` 边界和宿主交互
- 现在要把 `ccmini` 接成新的常驻大脑

核心结论只有一句：

**`ccmini` 对机器人宿主的主边界应当继续是 `Tool`，而不是重新发明一套机器人专用 RPC。**

`Hook` 负责状态注入、安全和生命周期策略，`Tool` 负责动作与能力暴露，生命周期由宿主显式管理。

## 设计目标

- 保持与旧 core 的接法一致，减少迁移成本
- 让 `ccmini` 只负责认知、决策、记忆、协作
- 让机器人宿主继续拥有运动控制、硬件线程、实时环路
- 让能力边界清晰，可审计，可做安全拦截

## 推荐分层

### `ccmini` 负责

- 对话理解
- 任务决策
- 长短期记忆
- 何时调用哪个 tool
- 输出文本和工具调用意图

### 机器人宿主负责

- 电机控制
- 传感器采集
- 音视频输入输出
- 运动队列与控制环
- 硬件安全
- 工具真实执行

### 主边界

- `Tool`: 大脑能做什么
- `Hook`: 推理前后如何注入状态与安全策略
- `start()/stop()`: 常驻生命周期

## 不推荐的做法

- 让 `ccmini` 直接持有电机或高频控制环
- 让模型直接输出原始 `set_target()` 或底层关节命令
- 为本地机器人运行时再定义一套独立 bridge 协议
- 把原始高频传感器流直接塞进模型上下文

## 生命周期约定

机器人宿主应把 `ccmini` 当作常驻 brain kernel，而不是单次函数调用。

标准流程：

1. 宿主创建 `create_robot_agent(...)`
2. 宿主传入固定的 `tools=[...]`
3. 宿主传入机器人相关 `hooks=[...]`
4. 启动时调用 `await agent.start()`
5. 收到用户语音、外部事件、系统事件时调用 `submit(...)` 或 `query(...)`
6. 宿主消费流式事件，执行对应 tool
7. 退出时调用 `await agent.stop()`

建议：

- 机器人场景优先用 `submit(...)`，避免阻塞控制主线
- 同一 `Agent` 实例长期持有
- 会话期间不要频繁重建 `Agent`

## Tool 边界约定

如果机器人宿主已经有统一的工具输入格式，`ccmini` 应该去适配那套格式，而不是让宿主再适配一次 `ccmini` 私有协议。

### Tool 设计原则

- 工具表达的是高层意图，不是底层控制量
- 参数 schema 尽量稳定，便于不同宿主复用
- 返回值尽量简短，供模型继续推理
- 长动作返回“已排队/已开始/已拒绝”即可
- 危险动作必须允许被 hook 拦截

### 推荐暴露给 `ccmini` 的最小 Tool 集

- `speak`
  - 让机器人说话或播报
- `move_head` / `look_at`
  - 头部朝向或看向目标
- `play_emotion`
  - 播放情绪动作
- `gesture` / `dance`
  - 触发高层动作片段
- `head_tracking`
  - 开关跟踪
- `camera`
  - 拍照或请求视觉摘要
- `stop_motion`
  - 停止当前动作
- `wake_up` / `goto_sleep`
  - 切换机器人整体状态

### 可选 Tool

- `set_interaction_mode`
  - 如 `idle`、`conversation`、`demo`
- `notify_user`
  - 蜂鸣、灯效、屏幕提示
- `record_memory_marker`
  - 在宿主侧打事件标记
- `get_robot_capabilities`
  - 给调试或多机适配用

### 不建议直接暴露的 Tool

- 原始关节写入
- 原始 PID/扭矩参数写入
- 高频 `set_target()` 流式接口
- 原始传感器大包透传

这些应留在机器人控制层内部，由宿主把高层 tool 意图转换成实时执行。

## Hook 边界约定

`Hook` 不是主业务接口，它的职责是给 `Tool` 提供更安全、更稳定的运行环境。

### 推荐使用的 Hook

- `PreQueryHook`
  - 在每轮推理前注入机器人状态快照
- `PreToolUseHook`
  - 对运动类 tool 做安全闸、参数裁剪、模式检查
- `IdleHook`
  - 在空闲时周期拉取电量、模式、环境摘要
- `SessionStartHook`
  - 申请摄像头、音频、总线等资源
- `SessionEndHook`
  - 清理资源、落盘状态
- `StopHook`
  - 急停、低电、保护触发时打断当前轮次
- `NotificationHook`
  - 把内核通知统一转成机器人侧提醒

## 机器人状态注入约定

机器人状态不建议做成用户每次手动调用的 tool。

更稳的方式是：

- 宿主维护一个结构化 `RobotSnapshot`
- `PreQueryHook` 每轮把摘要注入上下文
- 高频原始数据先在宿主侧降采样、融合、离散化

示例：

```python
from dataclasses import dataclass


@dataclass
class RobotSnapshot:
    battery_percent: float
    is_charging: bool
    motion_state: str
    interaction_mode: str
    head_tracking_enabled: bool
    user_present: bool
    torque_enabled: bool
    estop_triggered: bool
    last_error: str | None = None
```

注入给模型的内容应偏摘要，例如：

```text
Current robot state:
- battery: 78%, charging: no
- motion_state: idle
- interaction_mode: conversation
- user_present: yes
- head_tracking_enabled: yes
- estop_triggered: no
```

## Tool 适配建议

如果旧 core 已经有统一 tool 输入格式，推荐做一层薄适配：

1. 保留旧格式的字段命名和 schema
2. 在宿主里把旧 tool 定义包装成 `ccmini.tool.Tool`
3. 不把宿主内部执行细节泄漏给模型

适配后的 tool 只需要满足三件事：

- 有稳定的 `name`
- 有清晰的 `description`
- 有 JSON Schema `parameters`

宿主执行时再把 tool 请求转换成自己的内部动作总线、任务队列或控制命令。

## 建议的运行流

```text
user / sensor / app event
    -> host submit(...)
    -> ccmini reasoning
    -> tool call
    -> host tool executor
    -> motion/audio/vision subsystem
    -> compact tool result
    -> ccmini continues
```

关键点：

- `ccmini` 看到的是“能力”
- 宿主掌握的是“执行”
- 控制环始终在机器人侧

## 最小接入模板

```python
from ccmini import create_robot_agent


agent = create_robot_agent(
    provider=provider,
    system_prompt="You are the brain of an embodied robot.",
    tools=[
        SpeakTool(host),
        MoveHeadTool(host),
        PlayEmotionTool(host),
        CameraTool(host),
        StopMotionTool(host),
    ],
    hooks=[
        RobotStatePreQueryHook(host),
        RobotSafetyPreToolUseHook(host),
        RobotIdleHook(host),
    ],
)

await agent.start()

# 用户输入、语音转写、系统事件
agent.submit("用户刚刚叫了你的名字")

# 结束
await agent.stop()
```

## 迁移建议

从旧 core 迁移到 `ccmini` 时，优先保持下面三点不变：

- 统一 tool 输入格式不变
- 宿主工具执行器不变
- 机器人控制层不变

优先替换的部分：

- 推理主链
- 记忆系统
- hook 生命周期

这样迁移成本最低，也最不容易把实时控制搞乱。

## 一句话总结

对机器人宿主来说，`ccmini` 最合适的接法是：

**`Tool` 作为主接口，`Hook` 负责状态注入和安全策略，生命周期保持常驻。**
