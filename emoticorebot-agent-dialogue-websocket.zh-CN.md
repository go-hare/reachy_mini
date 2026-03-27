# Reachy Mini 接入 emoticorebot 对话通道补充决策

## 1. 文档定位

本文档是 `D:/work/py/reachy_mini` 的补充决策文档。

它只解决一个问题：

- 当前系统里的“用户 app 如何和 agent runtime 对话”

它不重复展开整体迁移背景。整体迁移方向仍以以下两份文档为准：

- `emoticorebot-agent-migration.zh-CN.md`
- `emoticorebot-agent-migration-stages.zh-CN.md`

## 2. 本文档冻结的结论

截至 2026-03-27，以下结论已经冻结：

1. 当前项目不是 app。
2. 当前项目的职责是：
   - Reachy 侧宿主流程
   - runtime 底座
   - 主入口
   - SDK / daemon / app 生命周期
3. 用户创建的 `profiles/<name>/` 才是 app。
4. `profiles/<name>/profiles/` 是这个 app 内部的 profile 文件包。
5. 最终目录命名收口为 `runtime` 与 `core`，不再保留 `agent_runtime` 与 `agent_core` 作为正式命名。
6. 当前项目不再区分“普通 app”和“agent app”两类。
7. 所有用户 app 都默认带 agent runtime。
8. 用户 app 与 runtime 的对话通道，优先采用 WebSocket。
9. 不新增 `web_bridge.py` 这类中间桥接层。
10. `runtime` 与 `core` 是内部实现目录，不是用户可见产品概念。
11. runtime 的实例生命周期与 WebSocket 开放能力，统一收敛到 `ReachyMiniApp`。
12. 用户 app 自己不再重复实现整套 runtime 宿主逻辑，只提供自己的 profile 根路径和最小入口。

## 3. 语义边界

后续讨论和实现统一使用下面这套口径：

### 3.1 当前项目

当前项目 `reachy_mini` 不是一个用户 app。

它只承担：

- 运行时底座
- 机器人宿主流程
- daemon / SDK / media / motion / io
- app 生命周期管理
- emoticorebot runtime 的内部承载

补充约束：

- 当前项目内部虽然继续保留 `apps/` 这套宿主机制
- 但在语义上，不再单独维护“非 agent app”路线
- 当前项目中的 app 宿主基类默认就是 agent runtime 的统一承载层

### 3.2 用户 app

用户创建的 app 目录是：

- `profiles/<name>/`

它包含：

- `profiles/<name>/<name>/main.py`
- `profiles/<name>/<name>/static/`
- `profiles/<name>/profiles/`

其中：

- `profiles/<name>/<name>/main.py`
  是用户 app 的 Python 入口
- `profiles/<name>/<name>/static/`
  是用户 app 的前端页面
- `profiles/<name>/profiles/`
  是这个 app 的 profile 文件包

### 3.3 内部 runtime

`runtime` 是内部能力目录，不是用户可见 app。

它负责：

- profile 加载
- front / kernel / memory / affect 运行编排
- resident runtime 调度

但它不单独承担：

- 用户可见 app 语义
- 独立 bridge 服务
- 额外的宿主进程角色

### 3.4 目录命名冻结

最终目录命名冻结为：

- `src/reachy_mini/apps/`
- `src/reachy_mini/runtime/`
- `src/reachy_mini/core/`

其中：

- `runtime/`
  对应当前过渡实现中的 `agent_runtime/`
- `core/`
  对应当前过渡实现中的 `agent_core/`

后续实现与文档应逐步收口到上述命名。

## 4. 为什么采用 WebSocket

本系统的浏览器对话链路不是单次同步请求，而是多阶段异步事件链：

1. 用户发来一条消息
2. `front` 先给出首轮可见回复
3. 同时把用户输入投递给 `kernel`
4. `kernel` 处理完后，把结果再交给 `front`
5. `front` 再把最终对用户可见的结果发出去
6. 过程中还会持续产生 `surface_state`

因此，这不是一个“只需要请求一次、返回一次”的页面模型。

采用 WebSocket 的原因是：

1. 前端和后端需要双向长连接
2. 一条连接内既要收用户消息，也要持续推送 runtime 事件
3. `front_hint`、`front_final`、`surface_state`、`turn_error` 都应属于同一条会话事件流
4. 后续还可能接入取消、中断、音频状态、工具状态等事件

因此，当前冻结结论为：

- 用户 app 对话通道采用 WebSocket
- 不优先采用 `POST /chat + SSE`

## 5. 对话链路定义

### 5.1 总体链路

用户 app 的浏览器前端通过 WebSocket 与当前宿主实例通信。

主链路如下：

1. 浏览器通过 WebSocket 发送 `user_text`，或者发送 `user_speech_started / user_speech_stopped`
2. 如果使用浏览器语音识别，最终 transcript 仍会回落成一条 `user_text`
3. 宿主实例将文本 turn 和 speech lifecycle 事件都交给 resident runtime
4. runtime 先执行 `front.reply(...)`
5. runtime 将该阶段结果推送给浏览器
6. runtime 将用户输入投递给 `kernel`
7. `kernel` 处理完后，结果进入 `front.present(...)`
8. runtime 将最终结果推送给浏览器
9. `surface_state` 在整个过程中持续推送给浏览器

### 5.2 关键职责

这里要明确三层：

1. 浏览器前端
   - 即用户 app 的页面 UI
2. `front`
   - 即内部的文本表达层
3. `kernel`
   - 即内部的主决策层

注意：

- `front` 不等于浏览器页面
- `kernel` 也不直接通知浏览器
- 浏览器只和宿主实例开放的 WebSocket 通信
- runtime 负责把内部 `front/kernel/surface_state` 事件统一整理后发给浏览器

## 6. 事件模型

为了让浏览器能区分“首轮前台回复”和“最终前台回复”，事件需要显式分层。

建议的 WebSocket 事件类型如下。

### 6.1 浏览器 -> 用户 app

文本输入：

```json
{
  "type": "user_text",
  "thread_id": "main",
  "text": "你好"
}
```

语音开始：

```json
{
  "type": "user_speech_started",
  "thread_id": "main",
  "text": ""
}
```

语音结束：

```json
{
  "type": "user_speech_stopped",
  "thread_id": "main",
  "text": "你好"
}
```

当前第一版麦克风链路采用浏览器内建 `SpeechRecognition`。

- 不上传 raw PCM 到当前 runtime
- 最终仍统一回落成 `user_text` 进入主文本链路

### 6.2 用户 app -> 浏览器

首轮前台回复：

```json
{ "type": "front_hint_chunk", "thread_id": "main", "text": "..." }
{ "type": "front_hint_done", "thread_id": "main", "text": "..." }
```

最终前台回复：

```json
{ "type": "front_final_chunk", "thread_id": "main", "text": "..." }
{ "type": "front_final_done", "thread_id": "main", "text": "..." }
```

表情/动作表层状态：

```json
{ "type": "surface_state", "thread_id": "main", "state": { "phase": "replying" } }
```

错误事件：

```json
{ "type": "turn_error", "thread_id": "main", "error": "..." }
```

## 7. 宿主层与用户 app 的实现边界

### 7.1 `ReachyMiniApp`

`ReachyMiniApp` 统一负责：

1. 持有 runtime 实例
2. 管理 runtime 生命周期
3. 持有 runtime loop / readiness 状态
4. 在 `settings_app` 上挂 WebSocket
5. 把浏览器发来的 `user_text / user_speech_*` 交给 runtime
6. 订阅 runtime 输出队列
7. 把 `surface_state` 和文本事件转发给浏览器

也就是说：

- runtime 代码仍来自 `runtime/`
- 但 runtime 的宿主化能力统一放在 `ReachyMiniApp`
- 当前项目不再额外包一层 `web_bridge.py`

### 7.2 用户 app

用户 app 自己只负责：

1. 提供自己的 profile 根路径
2. 提供自己的最小入口
3. 提供自己的静态页面

用户 app 不再重复实现：

- runtime loop 管理
- WebSocket 宿主逻辑
- runtime 事件转发主逻辑

## 8. 与上游 `reachy_mini_conversation_app` 的对齐点

本次决策和上游对齐的关键点不是“复制它的旧会话实现”，而是对齐它的结构方式：

1. 路由直接挂在宿主层拿到的 `settings_app` 上
2. 宿主层直接持有运行对象
3. 不额外再加一层独立桥接文件

本次差异只在于：

- 上游原系统主链路是旧 conversation / realtime
- 当前项目的新主链路是 `front -> kernel -> front`

## 9. 当前不做的事

本文档冻结的范围只限于浏览器事件对话通道，不包含：

1. 原始 PCM 输入采集 / server VAD
2. 音频/TTS 输出
3. 视觉联动
4. 机器人动作映射
5. desktop / desktop-dev 接入
6. 工具调用执行完成后的机器人动作编排细节

## 10. 后续实现约束

后续如果继续修改实现，需要满足以下约束：

1. 不再把当前项目表述成 app
2. 不再新增 `web_bridge.py` 这类中间桥接层
3. 不再把浏览器前端和内部 `front` 混为一个概念
4. 用户创建的 `profiles/<name>/` 始终是唯一需要被称为 app 的对象
5. 文本与浏览器 speech lifecycle 通道默认以 WebSocket 作为主通道
6. 在当前前提下，runtime 实例生命周期统一挂在 `ReachyMiniApp`
