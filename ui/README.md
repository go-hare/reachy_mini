# 内置 UI 目录

这个目录用于把 Codex TUI 作为当前项目的内置 UI 集成进来。

当前规划：

- `codex-rs/`：从外部仓库 vendoring 进来的 Codex Rust workspace
- `robot-ui/`：当前项目自己的 UI 包装器

## 当前状态

目前已经创建了 `robot-ui/` 骨架，但还没有把 `codex-rs/` 正式带进来。

也就是说：

- 目录结构已经就位
- `robot-ui` 代码已经准备好
- 下一步只需要把 `codex-rs` 复制到 `ui/codex-rs/`，就可以开始尝试编译 UI

## 推荐做法

建议先把整份 `codex-rs` 带进来，而不是一开始就尝试只复制 UI 相关子目录。

原因：

- `tui_app_server` 当前依赖链比较长
- 第一轮先追求“跑起来”，不是追求最小化代码体积
- 跑通后再做裁剪，成本更低

## 目录说明

### `robot-ui/`

这里是项目自己的 UI 启动器 crate。

职责非常窄：

- 读取 remote websocket 地址
- 调用 `codex_tui_app_server::run_main(...)`
- 作为项目自己的 UI 启动入口

### `codex-rs/`

这里将放置 vendoring 进来的 Codex Rust workspace。

`robot-ui` 默认会通过相对路径依赖这里面的 crate。

## 下一步

建议下一步按这个顺序继续：

1. 把 `codex-rs` 带入 `ui/codex-rs/`
2. 进入 `ui/robot-ui/`
3. 运行 `cargo build`
4. 再开始做 Python websocket 后端
