# Python 远程后端最小规范

这份文档定义了一个最小可用的 Python 后端，用来把 Codex 当作纯 UI 外壳复用。

整体架构是：

`Codex UI -> websocket -> Python agent 服务`

在这种模式下：

- Codex 只负责交互界面
- Python 负责真实 agent、任务编排和机器人逻辑
- Python 服务必须通过 websocket 说 Codex `app-server` 协议

这不是 SDK 嵌入方案。

## 目标

目标是让 Codex UI 在不依赖内置 Rust agent 的情况下，直接驱动你的 Python 后端。

第一阶段只要做到下面这条链路跑通就够了：

1. 用 remote 模式启动 Codex
2. 连接到 Python websocket 服务
3. 创建 thread
4. 发起 turn
5. 流式返回一条 assistant 回复
6. 正常结束 turn

只要这一条链路通了，就说明 Codex UI 已经和内置 agent 成功解耦。

## 如何启动 UI

Codex 已经支持把交互式 TUI 连接到远程 websocket 后端。

用法：

```bash
codex --remote ws://127.0.0.1:4500
```

如果需要鉴权 token，也可以这样：

```bash
codex --remote ws://127.0.0.1:4500 --remote-auth-token-env CODEX_REMOTE_AUTH_TOKEN
```

相关参考：

- `codex-rs/cli/src/main.rs`
- `codex-rs/tui_app_server/src/lib.rs`
- `codex-rs/app-server/README.md`

## 传输规则

Python 后端应该实现 `app-server` 的 websocket 传输语义。

最小要求：

- websocket 地址形如 `ws://127.0.0.1:4500`
- 每个 websocket text frame 只承载一条 JSON-RPC 消息
- method 名必须和 Codex `app-server` 协议完全一致
- wire format 字段名必须使用 camelCase

`app-server` 文档里提到，线上协议默认省略 `"jsonrpc": "2.0"`。为了兼容性，Python 后端第一版也建议遵循这个习惯。

## 最小后端范围

第一版不需要实现完整 app-server。

最小可用后端建议只实现：

- `initialize`
- `initialized` 通知处理
- `thread/start`
- `turn/start`
- `turn/interrupt`

强烈建议尽快补上：

- `thread/read`
- `thread/resume`

可以后面再做的：

- 文件 API
- 命令执行 API
- approvals
- dynamic tools
- realtime audio
- review mode
- plugins
- MCP

## 最小请求/响应面

### 1. `initialize`

UI 建连后，第一个请求一定是 `initialize`。  
在完成这个握手之前，不应该接受其他业务请求。

请求示例：

```json
{
  "method": "initialize",
  "id": 1,
  "params": {
    "clientInfo": {
      "name": "codex_cli",
      "title": "Codex CLI",
      "version": "dev"
    }
  }
}
```

你需要返回一个合法的 initialize 结果，然后接受后续的 `initialized` 通知。

实用规则：

- 接受 `initialize`
- 把连接状态记为 initialized
- 接受 `initialized` 通知
- 在 initialize 之前收到别的请求时返回错误

### 2. `thread/start`

这个请求用于创建 UI 可见的 thread。

请求示例：

```json
{
  "method": "thread/start",
  "id": 10,
  "params": {
    "model": "robot-main",
    "cwd": "D:/work/py/robot_project"
  }
}
```

返回：

```json
{
  "id": 10,
  "result": {
    "thread": {
      "id": "thr_001",
      "preview": "",
      "ephemeral": false,
      "modelProvider": "python",
      "createdAt": 1774579200,
      "updatedAt": 1774579200,
      "status": "idle",
      "path": null,
      "cwd": "D:/work/py/robot_project"
    },
    "model": "robot-main",
    "modelProvider": "python",
    "serviceTier": null,
    "cwd": "D:/work/py/robot_project",
    "approvalPolicy": "never",
    "approvalsReviewer": "user",
    "sandbox": "dangerFullAccess",
    "reasoningEffort": null
  }
}
```

然后再发一条通知：

```json
{
  "method": "thread/started",
  "params": {
    "thread": {
      "id": "thr_001",
      "preview": "",
      "ephemeral": false,
      "modelProvider": "python",
      "createdAt": 1774579200,
      "updatedAt": 1774579200,
      "status": "idle",
      "path": null,
      "cwd": "D:/work/py/robot_project"
    }
  }
}
```

说明：

- `approvalPolicy: "never"` 很适合第一版，因为可以避开 approvals 流程
- `sandbox: "dangerFullAccess"` 可以先当占位值，只要你的 Python 后端暂时不暴露 Codex shell 工具
- `modelProvider: "python"` 不是 Codex 内置 provider，但作为自定义后端标识非常实用

### 3. `turn/start`

用户在 UI 里发消息时，核心请求就是 `turn/start`。

请求示例：

```json
{
  "method": "turn/start",
  "id": 20,
  "params": {
    "threadId": "thr_001",
    "input": [
      {
        "type": "text",
        "text": "Hello robot"
      }
    ]
  }
}
```

先立即返回：

```json
{
  "id": 20,
  "result": {
    "turn": {
      "id": "turn_001",
      "items": [],
      "status": "inProgress",
      "error": null
    }
  }
}
```

然后发通知：

```json
{
  "method": "turn/started",
  "params": {
    "threadId": "thr_001",
    "turn": {
      "id": "turn_001",
      "items": [],
      "status": "inProgress",
      "error": null
    }
  }
}
```

## 一条 assistant 回复的最小事件流

第一版只需要支持一个 assistant message item。

推荐事件顺序：

1. `turn/started`
2. `item/started`
3. 0 个或多个 `item/agentMessage/delta`
4. `item/completed`
5. `turn/completed`

### `item/started`

先发一个空文本的 `agentMessage`：

```json
{
  "method": "item/started",
  "params": {
    "threadId": "thr_001",
    "turnId": "turn_001",
    "item": {
      "type": "agentMessage",
      "id": "msg_001",
      "text": "",
      "phase": "finalAnswer",
      "memoryCitation": null
    }
  }
}
```

### `item/agentMessage/delta`

然后流式发送文本分片：

```json
{
  "method": "item/agentMessage/delta",
  "params": {
    "threadId": "thr_001",
    "turnId": "turn_001",
    "itemId": "msg_001",
    "delta": "Hello"
  }
}
```

```json
{
  "method": "item/agentMessage/delta",
  "params": {
    "threadId": "thr_001",
    "turnId": "turn_001",
    "itemId": "msg_001",
    "delta": ", I am online."
  }
}
```

### `item/completed`

流式结束后，发送最终完整 item：

```json
{
  "method": "item/completed",
  "params": {
    "threadId": "thr_001",
    "turnId": "turn_001",
    "item": {
      "type": "agentMessage",
      "id": "msg_001",
      "text": "Hello, I am online.",
      "phase": "finalAnswer",
      "memoryCitation": null
    }
  }
}
```

### `turn/completed`

最后把 turn 标记为完成：

```json
{
  "method": "turn/completed",
  "params": {
    "threadId": "thr_001",
    "turn": {
      "id": "turn_001",
      "items": [],
      "status": "completed",
      "error": null
    }
  }
}
```

## `turn/interrupt`

UI 可能会发出中断请求。

最小行为：

1. 接收中断请求
2. 停止 Python 后端当前生成
3. 发 `turn/completed`，并把状态设成 `interrupted`

请求示例：

```json
{
  "method": "turn/interrupt",
  "id": 21,
  "params": {
    "threadId": "thr_001",
    "turnId": "turn_001"
  }
}
```

返回：

```json
{
  "id": 21,
  "result": {}
}
```

然后发：

```json
{
  "method": "turn/completed",
  "params": {
    "threadId": "thr_001",
    "turn": {
      "id": "turn_001",
      "items": [],
      "status": "interrupted",
      "error": null
    }
  }
}
```

## 最小对象形状

下面这些字段是第一版最重要的。

### Thread

最小实用字段：

- `id`
- `preview`
- `ephemeral`
- `modelProvider`
- `createdAt`
- `updatedAt`
- `status`
- `path`
- `cwd`

### Turn

最小实用字段：

- `id`
- `items`
- `status`
- `error`

状态建议只先支持这些：

- `inProgress`
- `completed`
- `interrupted`
- `failed`

### Agent Message Item

最小实用字段：

- `type: "agentMessage"`
- `id`
- `text`
- `phase`
- `memoryCitation`

第一版里，`phase` 最简单的值就是：

- `"finalAnswer"`

## 推荐的实现顺序

不要一开始就试图实现完整 app-server。

建议 Python 后端按这个顺序做：

1. websocket server
2. `initialize`
3. `thread/start`
4. `turn/start`
5. 写死的 `agentMessage` 流式输出
6. `turn/interrupt`
7. thread 持久化和 `thread/read`
8. `thread/resume`

只有这条链路稳定后，再把机器人相关内部状态逐步暴露出来，比如：

- front agent 状态
- planner 状态
- worker pool 状态
- robot action queue
- robot safety 状态

## 第一版不做什么

除非 UI 很快证明必须依赖，否则第一版先不要做：

- shell command items
- file change items
- approval requests
- request-user-input server calls
- dynamic tools
- plugin APIs
- MCP APIs
- realtime audio APIs

## 和现有 Python SDK 的关系

这个后端不要用 `sdk/python` 来做。

原因：

- `sdk/python` 是 Python 去调用 `codex app-server` 的客户端
- 你现在需要的是让 Python 自己充当服务器，接在 Codex UI 后面

正确的理解应该是：

- `sdk/python`：Python -> Codex
- 你现在这个后端：Codex UI -> Python

## 成功标准

只要下面 5 件事成立，这个 Python 远程后端就算最小成功：

1. `codex --remote ws://127.0.0.1:4500` 能正常连上
2. UI 里能发送一条 prompt
3. Python 服务能收到 `turn/start`
4. `item/agentMessage/delta` 的流式文本能在 UI 里显示
5. `turn/completed` 后 UI 能恢复到 idle

这一步一旦跑通，就说明 Codex 已经可以作为一个可复用的 UI 外壳来使用。
