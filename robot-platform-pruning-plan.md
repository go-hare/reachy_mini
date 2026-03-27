# 机器人平台裁剪计划

这份文档定义了把 Codex 改造成机器人平台时，第一阶段的“减法”方案。

这一阶段的目标不是马上加机器人功能，而是先把明显和未来机器人架构无关的产品残留、历史包袱、实验支线删掉，同时保留真正的运行时骨架。

## 阶段目标

第一阶段应保留这些核心层：

- `codex-rs/core`
- `codex-rs/exec`
- `codex-rs/app-server`
- `codex-rs/app-server-protocol`
- `codex-rs/tui_app_server`
- `codex-rs/cli`
- `codex-rs/protocol`
- `codex-rs/state`

先删掉那些明显不是平台内核的内容。

这一阶段尽量避免直接动主运行时循环，除非依赖关系逼着你这么做。

## 裁剪原则

第一批删除时，按下面的规则判断：

1. 如果某个模块已经是历史实现，而 Rust 主线已经替代它，优先删除。
2. 如果某个模块只是挂了一个产品化子命令，不属于执行骨架，可以优先删除。
3. 如果某个模块是实验性、演示性、POC 性质，优先删除。
4. 如果某个模块已经深度耦合进 `core`、`app-server`、`exec` 或 `tui_app_server`，第一阶段先不要碰。
5. 如果某个模块未来可能还对本地模型、机器人桥接、运行时扩展有价值，先保留，等机器人架构更清楚后再定。

## 第一批安全删除项

下面这些是第一批最适合删掉的内容，置信度最高。

### 1. 旧的 TypeScript CLI

删除：

- `codex-cli/`

原因：

- 仓库文档里已经明确说明它是 legacy 实现
- 当前维护主线是 Rust CLI
- 两套 CLI 同时保留只会增加理解成本，拖慢机器人平台改造

影响：

- 不会影响 Rust 运行时骨架
- 只是把历史 Node 包装层和旧发布路径移出项目主线

### 2. Cloud Tasks 这一整条功能线

删除：

- `codex-rs/cloud-tasks/`
- `codex-rs/cloud-tasks-client/`

并同步修改：

- `codex-rs/Cargo.toml`
- `codex-rs/cli/Cargo.toml`
- `codex-rs/cli/src/main.rs`

原因：

- 这是明显的产品功能支线，不是机器人平台基础骨架
- 当前引用面比较窄，适合整条支线一起删除
- 未来机器人平台更需要本地任务编排，不需要 Codex Cloud 任务浏览

影响：

- `cloud` 子命令会消失
- interactive、exec、app-server 主流程仍然保留

### 3. V8 Proof of Concept

删除：

- `codex-rs/v8-poc/`

并同步修改：

- `codex-rs/Cargo.toml`

原因：

- 它本来就是 proof-of-concept
- 不代表未来机器人平台内核方向
- 非常适合作为第一批减法，不会伤到运行时主链

### 4. 编辑器和容器脚手架

删除：

- `.devcontainer/`
- `.vscode/`

原因：

- 这些只是开发环境辅助配置，不是平台逻辑
- 会增加噪音，但对运行时没有价值

影响：

- 对运行时没有影响
- 后面如果真需要，可以再按团队实际情况重建

## 暂时保留

下面这些第一阶段不要删。

### 运行时骨架

保留：

- `codex-rs/core/`
- `codex-rs/exec/`
- `codex-rs/app-server/`
- `codex-rs/app-server-protocol/`
- `codex-rs/tui_app_server/`
- `codex-rs/cli/`
- `codex-rs/protocol/`
- `codex-rs/state/`

原因：

- 它们构成了未来机器人平台真正要复用的执行、编排、协议、持久化、UI 骨架

### 未来可能还有价值的模块

先保留：

- `codex-rs/code-mode/`
- `codex-rs/lmstudio/`
- `codex-rs/ollama/`
- `codex-rs/utils/oss/`
- `codex-rs/connectors/`
- `codex-rs/plugin/`
- `codex-rs/mcp-server/`
- `sdk/`

原因：

- `code-mode` 目前仍然被 `core` 引用
- `lmstudio`、`ollama`、`utils/oss` 未来可能对本地机器人模型有用
- `connectors`、`plugin`、`mcp-server` 已经嵌入主架构，第一阶段不适合乱砍
- `sdk/` 后面可能对机器人 bridge 或客户端集成有价值

## 第一阶段不要删的东西

除非后续已经有替代实现，否则第一阶段不要删：

- `codex-rs/core/`
- `codex-rs/app-server/`
- `codex-rs/tui_app_server/`
- `codex-rs/exec/`

原因：

- 这些正是未来要被改造成机器人平台的核心层
- 现在删掉等于把真正的骨架一起拆掉

## 删除后必须同步做的事

删目录本身不够，第一批减法一定要把引用也清理干净。

### Workspace Manifest 清理

需要修改：

- `codex-rs/Cargo.toml`

任务：

- 删除已经删掉的 workspace members
- 删除对应的 workspace dependency alias
- 如果有 stale 的 metadata ignored 项，也一起清理

### CLI 命令清理

需要修改：

- `codex-rs/cli/Cargo.toml`
- `codex-rs/cli/src/main.rs`

任务：

- 删除 `codex-cloud-tasks` 依赖
- 删除 `Cloud` 子命令及其接线
- 删除因此产生的死 import

## 推荐执行顺序

推荐按下面顺序做：

1. 删除 `codex-cli/`
2. 删除 `codex-rs/v8-poc/`
3. 删除 `codex-rs/cloud-tasks/` 和 `codex-rs/cloud-tasks-client/`
4. 清理 `codex-rs/Cargo.toml`
5. 清理 `codex-rs/cli/Cargo.toml`
6. 清理 `codex-rs/cli/src/main.rs`
7. 删除 `.devcontainer/` 和 `.vscode/`

## 成功标准

第一轮减法完成时，应满足下面这些条件：

- legacy TypeScript CLI 已删除
- cloud tasks 代码已删除
- V8 proof-of-concept 已删除
- 编辑器/容器脚手架已删除
- workspace manifest 不再引用这些已删 crate
- CLI 不再暴露 cloud 相关命令
- 仓库剩余结构明显围绕 `core + exec + app-server + tui_app_server`

## 下一阶段

这一轮减法完成后，下一阶段才进入“在现有 Rust 骨架内部重新定义机器人平台”的工作，包括：

- front agent
- kernel
- worker orchestration
- robot runtime bridge
- robot protocol 和 state model

也就是说，先做减法，再做真正的机器人化重构。
