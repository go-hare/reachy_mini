# robot-ui

`robot-ui` 是当前项目自己的 UI 启动器。

它的职责不是实现 agent，而是把 Codex TUI 作为项目自带界面启动起来，并连接到 Python 后端。

## 设计目标

- 复用 Codex TUI
- 不依赖外部安装的 Codex
- 默认连接本地 Python websocket 后端

## 依赖前提

在编译之前，需要先把 `codex-rs` 放到：

```text
ui/codex-rs/
```

可以手工复制，也可以运行：

```powershell
pwsh -File ..\sync_codex_rs.ps1
```

## 编译

```powershell
cd ui\robot-ui
cargo build
```

## 运行

默认连接本地 websocket：

```powershell
cargo run -- --remote ws://127.0.0.1:4500
```

如果不传 `--remote`，默认也会使用：

```text
ws://127.0.0.1:4500
```

## 当前状态

这是第一版骨架。

当前只解决：

- UI 包装器入口
- remote 地址传递
- Codex TUI 的内置化方向

当前还没有解决：

- Python 后端协议实现
- 一体化启动
- 机器人状态面板扩展
