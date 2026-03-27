# robot-ui Phase 1 Plan

## 需求理解

当前目标不是把 Python 嵌进 Codex，也不是继续使用 Codex 自带 agent。

这一阶段只做一件事：

- 把 Codex TUI 作为当前项目的内置 UI 壳接进来
- UI 通过 websocket 连接后续要实现的 Python agent 服务
- 先验证 `robot-ui -> codex_tui_app_server -> remote websocket` 这条链路在当前项目里能否编译成立

## 已确认决策

- `Codex` 只承担 UI 职责
- agent 内核替换为项目自己的 Python 服务
- Python 服务通过 websocket 对外提供 Codex app-server 协议
- `codex-rs` vendoring 到当前项目的 `ui/codex-rs/`
- 当前项目自己的 UI 启动入口为 `ui/robot-ui/`
- 第一阶段先追求“编译通过 + 启动入口成立”，暂不做裁剪

## 技术方案

1. 确认 `ui/codex-rs/` 已同步到当前项目
2. 在 `ui/robot-ui/` 使用 path dependency 引用 vendored crates
3. 运行 `cargo build`
4. 修复首次编译暴露出来的路径、API、feature、workspace 依赖问题
5. 成功后更新目录文档与阶段状态

## 本阶段不做

- 不实现完整 Python websocket agent
- 不修改 `codex-rs/core` 的业务逻辑
- 不做大规模裁剪或删除 vendored crates
- 不处理机器人专属面板和业务 UI 扩展

## 关键问题与答案

### 问题 1

是否要把 Python 直接嵌入 Codex 进程？

答案：否。Python 后端独立运行，UI 通过 websocket 连接。

### 问题 2

是否继续使用 Codex 原有 agent？

答案：否。保留 UI，替换 agent/backend。

### 问题 3

是否接受第一阶段先完整 vendoring `codex-rs`，后续再裁剪？

答案：是。这是当前最稳妥的推进方式。
