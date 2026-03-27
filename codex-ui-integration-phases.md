# Codex UI 内置集成阶段方案

这份文档定义了一个阶段性方案，用于把 Codex TUI 内置到当前 Python 项目里，并让它作为项目自带 UI 连接到 Python 后端。

目标不是继续依赖外部安装的 Codex，而是把 Codex 的开源 TUI 能力带进项目里，作为产品自己的桌面界面。

整体方向是：

`内置 Codex TUI + Python 远程后端`

## 总体目标

要达成的最终形态是：

- 用户启动你的项目
- Python 后端自动启动
- 内置的 Codex 风格 UI 自动启动
- UI 自动连接本地 websocket 后端
- 用户不需要单独安装 Codex

也就是说，从产品体验上看，它已经是你项目自己的桌面 UI。

## 推荐目录结构

建议项目逐步整理成下面这种结构：

```text
your_project/
├── backend/
│   ├── agent_server.py
│   ├── kernel.py
│   ├── worker_manager.py
│   └── robot_bridge.py
├── ui/
│   ├── codex-rs/
│   └── robot-ui/
├── launcher.py
└── docs/
```

说明：

- `backend/`：你的 Python 后端，负责 agent、任务、机器人逻辑
- `ui/codex-rs/`：先整份 vendoring 进来的 Codex Rust workspace
- `ui/robot-ui/`：你自己的 UI 包装器，用来启动 `codex_tui_app_server`
- `launcher.py`：最终的一体化启动入口

## 阶段 0：先定产品骨架

### 目标

先统一项目形态，不再摇摆于 SDK、嵌入、外置启动等多种路线。

### 要做的事

- 明确采用“内置 Codex TUI + Python 后端”路线
- 固定项目的目录结构
- 固定 websocket 端口规划
- 固定 UI 和后端的启动顺序
- 固定日志、配置、运行时数据目录

### 完成标准

- 项目结构和职责划分已经清楚
- 不再讨论是否走 `sdk/python`
- 不再把外部安装的 Codex 当成最终产品方案

## 阶段 1：先把 UI 内置进项目

### 目标

把 Codex TUI 变成项目自带的 UI，而不是依赖用户外部安装。

### 要做的事

- 先把 `codex-rs` 整份 vendoring 到 `ui/codex-rs/`
- 不要一开始就做激进裁剪
- 新增一个自己的包装 crate：`ui/robot-ui/`
- 在 `robot-ui` 里直接调用 `codex_tui_app_server::run_main(...)`
- 把 remote websocket 地址作为参数传进去
- 默认关闭不必要的 feature，比如语音输入

### 原则

- 第一阶段先追求“跑起来”
- 不要一开始就试图最小化拷贝 UI 子集
- 不要一开始就动 `core`

### 完成标准

- 你的项目可以自己编译出一个 `robot-ui` 可执行文件
- 这个 UI 可执行文件不依赖外部安装的 Codex

## 阶段 2：做 Python 最小远程后端

### 目标

让内置 UI 能够连接到 Python 服务，并完成一轮最小交互。

### 要做的事

- 实现 websocket 服务
- 实现 `initialize`
- 实现 `thread/start`
- 实现 `turn/start`
- 实现 `turn/interrupt`
- 支持 `item/agentMessage/delta` 流式回复

### 原则

- 先做最小协议，不要追求完整 app-server
- 先让 UI 能收发一轮消息
- 不要一开始就接机器人复杂逻辑

### 完成标准

- 打开 `robot-ui` 后能连到 Python 后端
- 用户发送一条消息后，UI 可以显示流式回复
- turn 能正常结束或被中断

## 阶段 3：做一体化启动

### 目标

从“两个开发进程”变成“一个产品入口”。

### 要做的事

- 写 `launcher.py`
- 先启动 Python 后端
- 等待 websocket ready
- 再启动 `robot-ui`
- 做退出联动
- 做端口占用检测
- 做日志落盘

### 用户体验目标

用户只需要启动一次，就能看到完整界面。

### 完成标准

- 项目已有统一入口
- 不需要手工分开启动 UI 和后端

## 阶段 4：把假后端换成真实机器人后端

### 目标

把前面为了联调写的最小 Python 服务，升级成真实机器人平台后端。

### 要做的事

- 接入 `front agent`
- 接入 `kernel`
- 接入 `worker pool`
- 接入 `robot bridge`
- 接入 `robot state`
- 接入动作执行与安全控制

### 原则

- 先保证协议稳定
- 再逐步替换内部实现
- 前台 UI 尽量少改，优先保证后端能力落地

### 完成标准

- UI 背后的服务已经不是 demo
- 而是真实机器人 agent 平台后端

## 阶段 5：再做仓库裁剪

### 目标

在 UI 和后端都跑通后，再清理没用的 Codex 支线。

### 要做的事

- 删除 `codex-cli`
- 删除 `cloud-tasks`
- 删除 `cloud-tasks-client`
- 删除 `v8-poc`
- 删除其他确认无用的外围支线
- 清理 workspace manifest 和无用依赖

### 原则

- 裁剪放在“跑通之后”
- 不要在最前面删到编译地狱
- 先保住运行链路，再做减法

### 完成标准

- UI 仍能正常启动
- 仓库体积和认知负担明显下降

## 阶段 6：再决定是否继续产品化 UI

### 目标

在功能跑通后，决定 UI 的长期形态。

### 后续可以考虑的方向

- 保持 Codex TUI 风格，长期维护 Rust UI
- 在现有 TUI 基础上进一步改造成机器人控制台
- 后期再换成更图形化的前端

### 当前建议

这一阶段先不要提前展开，等前五个阶段跑通后再决定。

## 推荐执行顺序

建议严格按下面顺序推进：

1. 定骨架
2. 内置 UI
3. 跑通 Python 最小后端
4. 做一体化启动
5. 替换成真实机器人后端
6. 再裁剪 Codex 仓库

## 关键里程碑

建议把阶段结果明确成这些里程碑：

- `M1`：项目内可以编译并启动 `robot-ui`
- `M2`：`robot-ui` 能连接 Python websocket
- `M3`：UI 能完成一轮流式对话
- `M4`：项目变成单入口启动
- `M5`：Python 后端升级为真实机器人内核
- `M6`：Codex 仓库外围支线完成裁剪

## 当前阶段建议

按照现在的状态，最合理的下一步不是继续讨论概念，而是进入：

`阶段 1：先把 UI 内置进项目`

也就是：

- 把 `codex-rs` 带进项目
- 建 `robot-ui`
- 让它具备连接 remote websocket 的能力

只要这一步完成，后面的 Python 后端接入就会顺很多。
