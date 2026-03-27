# 阶段 1 文件级执行清单

这份文档把“阶段 1：先把 Codex UI 内置进项目”细化到目录和文件层面。

这一阶段的目标只有一个：

`把 Codex TUI 作为项目自带 UI 带进来，并准备好连接 Python 后端`

先不要求后端跑通，也先不要求机器人逻辑接入。

## 阶段目标

完成这一阶段后，项目应具备下面这些能力：

- 项目仓库里已经包含 UI 代码
- 项目可以编译自己的 UI 可执行文件
- 这个 UI 可执行文件未来可以连 Python websocket 后端
- 不再依赖用户外部安装 Codex

## 第一原则

这一阶段不要做下面这些事：

- 不要一开始就裁剪 `codex-rs` 到最小
- 不要修改 `codex-rs/core` 逻辑
- 不要开始实现 Python 协议后端
- 不要把桌面端产品源码当成当前目标

这一阶段只做：

- vendoring UI 代码
- 建自己的 UI 包装器
- 让项目具备“内置 UI”结构

## 推荐目录落位

建议把 UI 相关内容收敛到当前项目下的 `ui/` 目录：

```text
reachy_mini/
├── backend/
├── ui/
│   ├── codex-rs/
│   └── robot-ui/
├── launcher.py
└── docs/
```

如果当前项目里还没有 `backend/` 和 `ui/`，这一阶段就顺手建出来。

## 需要带入项目的目录

### 第一步：整份带入 `codex-rs`

先带入：

- `codex-rs/`

放到：

- `ui/codex-rs/`

原因：

- `tui_app_server` 当前依赖链比较长
- 手工只拷一小部分目录，第一轮很容易踩编译依赖坑
- 先整份带进来，后面跑通后再裁剪，成本更低

### 暂时不要带的目录

这一阶段先不要带入：

- `codex-cli/`
- `sdk/`
- `.devcontainer/`
- `.vscode/`
- 根目录文档和发布辅助目录

原因：

- 这一阶段目标是内置 UI，不是复制整仓库
- 这些内容不是 UI 启动最短路径的一部分

## 需要新增的目录和文件

### 1. 新建 UI 包装器 crate

新增目录：

- `ui/robot-ui/`

建议新增文件：

- `ui/robot-ui/Cargo.toml`
- `ui/robot-ui/src/main.rs`

它的职责不是重写 UI，而是包装调用 `codex_tui_app_server::run_main(...)`。

### 2. 新建 UI README

建议新增：

- `ui/README.md`

用来记录：

- UI 来源
- vendoring 方式
- 本地编译方法
- 与 Python 后端的连接方式

### 3. 新建 UI 构建说明

建议新增：

- `ui/robot-ui/README.md`

说明：

- 如何编译 `robot-ui`
- 如何指定 remote websocket 地址
- 暂时支持哪些参数

## `robot-ui` 的职责

`robot-ui` 不是 agent，也不是协议服务端。

它的职责非常窄：

1. 读取 UI 启动参数
2. 构造 remote websocket 地址
3. 调用 `codex_tui_app_server::run_main(...)`
4. 把 remote 地址传进去

也就是说，它只是你的“产品内置 UI 启动器”。

## `robot-ui` 第一版建议依赖

`ui/robot-ui/Cargo.toml` 第一版建议只依赖必要项。

目标是尽量轻，不要再走 `codex` 顶层 CLI 那层包装。

建议依赖：

- `anyhow`
- `clap`
- `codex-tui-app-server`
- `codex-arg0`
- `codex-core`
- `codex-protocol`
- `codex-utils-cli`

路径依赖应指向：

- `../codex-rs/...`

## 功能开关建议

第一版建议把非必要 feature 关掉，尤其是音频相关 feature。

建议策略：

- `codex-tui-app-server = { path = "../codex-rs/tui_app_server", default-features = false }`

原因：

- 可以先避开 `voice-input`
- 能减少音频设备和平台依赖带来的复杂度

## `main.rs` 第一版职责清单

`ui/robot-ui/src/main.rs` 第一版应该完成的事情：

1. 解析命令行参数
2. 支持传入 websocket 地址
3. 支持默认地址，比如 `ws://127.0.0.1:4500`
4. 调用 `codex_tui_app_server::run_main(...)`
5. 把 remote 参数传进去
6. 不接入内置 agent

这一版先不要做：

- 自定义复杂主题
- 复杂机器人面板
- 特殊桌面窗口控制
- 多后端切换

## 文件级操作顺序

建议严格按下面顺序操作。

### 步骤 1：创建 UI 目录

新增目录：

- `ui/`
- `ui/robot-ui/`
- `ui/robot-ui/src/`

### 步骤 2：带入 `codex-rs`

复制：

- `C:\Users\Administrator\Downloads\codex-main (1)\codex-main\codex-rs`

到：

- `D:\work\py\reachy_mini\ui\codex-rs`

### 步骤 3：新增 `ui/robot-ui/Cargo.toml`

这个文件定义你的 UI 包装器 crate。

它应该：

- 声明为独立 package
- 使用 path dependency 指向 `ui/codex-rs` 中的 crate
- 默认关闭 `codex-tui-app-server` 的多余 feature

### 步骤 4：新增 `ui/robot-ui/src/main.rs`

这个文件实现 UI 启动器。

最重要的是：

- 直接调用 `codex_tui_app_server::run_main(...)`
- 传入 remote websocket 地址

### 步骤 5：新增 `ui/README.md`

记录 UI 集成方式，避免后续忘记为什么要整份带入 `codex-rs`。

### 步骤 6：新增 `ui/robot-ui/README.md`

记录本地构建和运行方式。

## 这一阶段暂不做的清理

虽然我们前面已经写了裁剪计划，但在阶段 1 暂时先不要执行这些删除：

- 不删 `cloud-tasks`
- 不删 `v8-poc`
- 不删 `core`
- 不删 `app-server`

原因：

- 阶段 1 的目标是先把 UI 带进来
- 裁剪应该放在“UI 能跑起来之后”

## 建议的阶段 1 验收标准

阶段 1 完成后，至少满足下面这些条件：

1. 当前项目里已经有 `ui/codex-rs/`
2. 当前项目里已经有 `ui/robot-ui/`
3. `robot-ui` 能成功编译
4. `robot-ui --help` 可以运行
5. `robot-ui` 的代码路径中已经预留 remote websocket 参数

注意：

这一阶段不要求真的连上 Python 后端。

## 阶段 1 完成后的下一步

阶段 1 完成后，就进入：

- `阶段 2：做 Python 最小远程后端`

也就是：

- `initialize`
- `thread/start`
- `turn/start`
- `turn/interrupt`
- `item/agentMessage/delta`

到那时，UI 和后端两边才能真正开始对接。

## 当前最合理的执行动作

如果按这份清单推进，当前最合理的第一批实际动作就是：

1. 在当前项目下创建 `ui/`
2. 把 `codex-rs` 复制到 `ui/codex-rs/`
3. 创建 `ui/robot-ui/`
4. 写 `Cargo.toml`
5. 写 `src/main.rs`

也就是说，阶段 1 的本质是：

`先把 UI 代码和 UI 启动器落到项目里`

后面的 Python 后端接入，是阶段 2 的事。
