# Reachy Mini 接入 emoticorebot 分阶段改造执行文档

## 1. 文档定位

本文档是 `/Users/apple/work-py/reachy_mini` 的阶段性改造执行文档。

它不重复展开完整架构设计，而是回答下面几个问题：

- 先做什么
- 后做什么
- 每个阶段的边界是什么
- 每个阶段的交付物是什么
- 到什么程度算这个阶段完成

对应的架构基线、模块取舍和设计背景，见：

- `emoticorebot-agent-migration.zh-CN.md`

## 2. 当前已确认基线

在进入分阶段执行前，以下事项已经确认：

1. `reachy_mini` 继续承担机器人身体层职责：
   - SDK
   - daemon
   - motion
   - media
   - io
   - app 生命周期
2. 旧 conversation / realtime 主脑不再作为未来主干保留。
3. 新系统只保留一个脑子：`emoticorebot`。
4. `profile` 不再是旧 conversation 的提示词目录，而是新的 profile workspace。
5. `profile` 的标准目录形态为：
   - `profiles/<name>/AGENTS.md`
   - `profiles/<name>/USER.md`
   - `profiles/<name>/SOUL.md`
   - `profiles/<name>/TOOLS.md`
   - `profiles/<name>/FRONT.md`
   - `profiles/<name>/config.jsonl`
   - `profiles/<name>/memory/`
   - `profiles/<name>/skills/`
   - `profiles/<name>/session/`
   - `profiles/<name>/tools/`
   - `profiles/<name>/prompts/`
6. `profile` 结构中不包含 `HEARTBEAT.md`。
7. `reachy-mini-app-assistant` 的 `check` / `publish` 命令将退役。
8. `emoticorebot` 的 `agent` 命令行入口需要迁过来，作为文本级主入口之一。
9. 阶段 2 先跑通 `profile -> front -> 文本回复`。
10. 在阶段 2 稳定后，再单独接入 `kernel`。
11. `emoticorebot` 现有的 `desktop / desktop-dev` 入口暂不纳入首批迁移主路径。

## 3. 总体阶段图

本次改造建议分为六个阶段：

| 阶段 | 名称 | 核心目标 |
|------|------|----------|
| 0 | 基线冻结 | 固定方向、目录模型、CLI 取舍、文档口径 |
| 1 | Profile Workspace 与入口整理 | 先把 profile workspace 和 CLI 边界理顺 |
| 2 | 新脑子接入 | 跑通 `profile -> front -> 文本回复` |
| 3 | Kernel 接入 | 在 front 文本层稳定后，接入 kernel |
| 4 | Reachy 输出执行层接回 | 跑通 `surface_state` 和 `reply` 到机器人动作/音频 |
| 5 | 旧资产迁移与主干收口 | 迁移旧资产，清理旧入口，完成语义收口 |

## 4. 阶段 0：基线冻结

### 4.1 目标

- 固定“Reachy 做身体，emoticorebot 做大脑”的主方向
- 固定 `profile workspace` 目录模型
- 固定 CLI 取舍
- 固定首批不做的内容

### 4.2 本阶段交付物

- 主设计文档：
  - `emoticorebot-agent-migration.zh-CN.md`
- 分阶段执行文档：
  - `emoticorebot-agent-migration-stages.zh-CN.md`

### 4.3 本阶段验收标准

- 团队对最终目标没有方向性歧义
- `profile` 的目录模型已经明确
- `check / publish` 已被认定为旧流
- `agent` 命令行需要迁入
- 阶段 2 与阶段 3 的边界已经明确
- `desktop / desktop-dev` 已被认定为非首批路径

## 5. 阶段 1：Profile Workspace 与入口整理

### 5.1 目标

这一阶段先不碰复杂机器人联动，先把“用户如何创建和组织自己的 agent”这件事整理清楚。

核心目标：

- 将 `profiles/<name>/` 明确为新的正式 workspace 单元
- 让 `profile loader` 面向新的 workspace 结构工作
- 退役旧 app 发布流相关 CLI
- 清理旧文档里关于 `check / publish / Hugging Face app store` 的主路径表述

### 5.2 本阶段做什么

- 新建或整理 `profiles/` 目录约定
- 约束 `profile loader` 的输入与输出边界
- 删除 `reachy-mini-app-assistant` 中的：
  - `check`
  - `publish`
  - `--publish`
  - 与发布私有/公开 space 相关的参数
- 清理 `fork_conversation` 完成后的提示文案中对 `check` 的引用
- 清理文档中把 app 发布当作主路径的表述

### 5.3 本阶段不做什么

- 不接机器人动作映射
- 不接音频输出
- 不接视觉联动
- 不接 `emoticorebot` 的 `desktop / desktop-dev`
- 不做大规模旧资产迁移

### 5.4 建议落点

- `profiles/`
- `src/reachy_mini/agent_runtime/profile_loader.py`
- `src/reachy_mini/apps/app.py`
- `src/reachy_mini/apps/fork_conversation.py`
- 文档与技能文件中所有 `check / publish` 相关说明

### 5.5 本阶段交付物

- 新 profile workspace 目录约定落地
- `profile loader` 的新职责说明
- CLI 清理完成
- 文档口径统一

### 5.6 本阶段验收标准

- `profiles/<name>/` 结构已经成为主文档的一部分
- 仓库不再把 `check / publish` 当作主路径
- 当前阶段讨论和实现都围绕 profile workspace，而不是旧 app store 逻辑

## 6. 阶段 2：新脑子接入

### 6.1 目标

先跑通最小闭环：

`profile workspace -> front -> 文本回复`

这一阶段的目标不是让机器人已经表现自然，而是先让 profile 真正驱动 front，并稳定产出文本回复。

### 6.2 本阶段做什么

- 建立 Reachy 侧新的主入口
- 迁入并接通 `agent` 命令行入口
- 将选中的 profile workspace 装配到新运行时
- 接通 `front` 层
- 跑通用户输入到文本回复
- 明确 front 层与后续 kernel 接入的边界

### 6.3 本阶段不做什么

- 不做复杂动作表达
- 不做音频/TTS 输出
- 不做视觉联动
- 不大规模迁移旧 profile 私有工具

### 6.4 建议落点

- `src/reachy_mini/agent_runtime/main.py`
- `src/reachy_mini/agent_runtime/profile_loader.py`
- `src/reachy_mini/agent_core/` 下的新主入口或适配层

### 6.5 本阶段交付物

- 选 profile
- 加载 profile workspace
- 启动新脑子
- `front` 已参与文本回复链路
- 得到文本级回复
- `agent` 命令行可以直接用于文本级验证

### 6.6 本阶段验收标准

- 文本输入已由 `emoticorebot` 接管
- 文本回复已经经过 `front`
- profile workspace 能真正影响当前 agent 的行为与配置
- `agent` 命令行已成为可用入口

## 7. 阶段 3：Kernel 接入

### 7.1 目标

在 `profile -> front -> 文本回复` 跑稳之后，再把 kernel 接入主流程。

这一阶段的重点是把真正的决策、工具调用和记忆链路接进来，而不是继续扩大 front 的职责。

### 7.2 本阶段做什么

- 接入 `kernel`
- 让 front 与 kernel 的职责边界稳定下来
- 跑通 `front -> kernel -> reply` 或等价的文本主链路
- 让旧 `openai_realtime.py` 不再承担主脑职责

### 7.3 本阶段不做什么

- 不做机器人动作映射
- 不做音频/TTS 输出
- 不做视觉联动
- 不做 desktop 入口接入

### 7.4 建议落点

- `src/reachy_mini/agent_runtime/main.py`
- `src/reachy_mini/agent_core/brain_kernel/`
- `src/reachy_mini/agent_core/runtime/`
- `src/reachy_mini/agent_core/front/`

### 7.5 本阶段交付物

- kernel 已接入主文本链路
- front 与 kernel 的边界已经固定
- 文本回复已不再只是 front 的单层输出

### 7.6 本阶段验收标准

- 旧 realtime 主流程已退出文本主链路
- kernel 已参与主回复生成或主决策链路
- 文本链路已经具备继续接动作和音频的稳定基础

## 8. 阶段 4：Reachy 输出执行层接回

### 8.1 目标

在新脑子已接管的前提下，把 Reachy 的身体表现接回来。

核心目标：

- `surface_state -> Reachy 动作`
- `reply -> 音频/TTS`

### 8.2 本阶段做什么

- 建立 `surface_driver.py`
- 建立 `speech_driver.py`
- 做 `phase / presence / motion_hint / expression` 到机器人动作的映射
- 做动作节流、去抖、状态收束
- 做回复文本到音频输出的接线

### 8.3 本阶段不做什么

- 不做全部旧资产的完整迁移
- 不做 profile 深层历史迁移
- 不做 desktop 入口接入

### 8.4 建议落点

- `src/reachy_mini/agent_runtime/surface_driver.py`
- `src/reachy_mini/agent_runtime/speech_driver.py`

### 8.5 本阶段交付物

- 机器人能够根据 `surface_state` 产生基础反馈
- 机器人能够把 `reply` 送到音频输出

### 8.6 本阶段验收标准

- `listening / replying / settling / idle` 这些阶段切换已可观测
- 基本动作反馈自然且不明显抖动
- 回复输出已能带动头部/天线/音频形成统一链路

## 9. 阶段 5：旧资产迁移与主干收口

### 9.1 目标

将旧 conversation app 中真正有价值的资产迁进来，并完成旧主脑语义上的退场。

### 9.2 本阶段做什么

- 迁移 `moves.py`
- 迁移 `camera_worker.py`
- 迁移通用机器人 `tools/`
- 迁移旧 `prompts/`
- 将旧 profile 内容重构进新的 `profiles/<name>/` workspace
- 清理 `fork_conversation` 相关入口
- 清理旧 realtime 主流程

### 9.3 建议落点

- `src/reachy_mini/legacy_conversation_assets/`
- `profiles/<name>/tools/`
- `profiles/<name>/prompts/`
- `profiles/<name>/memory/`
- `profiles/<name>/session/`

### 9.4 本阶段交付物

- 旧资产完成分类迁移
- 主干不再依赖旧 conversation realtime 主流程
- 旧入口只剩必要兼容壳，或被完全清理

### 9.5 本阶段验收标准

- 项目语义已经稳定为“Reachy Mini + emoticorebot”
- 旧脑子不再承担主路径职责
- profile workspace 成为新的用户创建 agent 的正式载体

## 10. CLI 策略

### 10.1 当前明确退役

以下命令不再作为未来主路径保留：

- `reachy-mini-app-assistant check`
- `reachy-mini-app-assistant publish`

### 10.2 当前明确暂不接入

以下 `emoticorebot` CLI 暂不纳入当前迁移主路径：

- `emoticorebot desktop`
- `emoticorebot desktop-dev`

### 10.3 当前明确需要迁入

以下 CLI 入口需要纳入当前迁移主路径：

- `emoticorebot agent`

### 10.4 当前未决但不阻塞阶段推进

以下事项可以后定，不阻塞前五个阶段：

- 是否保留 `reachy-mini-app-assistant create`
- 是否将 `create` 演进为更接近 `onboard` 的 workspace 初始化命令
- 最终是否需要新的 Reachy 侧统一 agent CLI

## 11. 推荐执行顺序

建议严格按以下顺序推进：

1. 先做阶段 1，整理 profile workspace 和 CLI 边界
2. 再做阶段 2，跑通 `profile -> front -> 文本回复`
3. 再做阶段 3，单独接入 kernel
4. 再做阶段 4，把 Reachy 输出执行层接回
5. 最后做阶段 5，迁移旧资产并清理旧入口

## 12. 每阶段的完成信号

为了避免阶段之间互相污染，建议按如下“完成信号”推进：

- 阶段 1 完成信号：
  profile workspace 已成为正式模型，CLI 不再保留旧发布流
- 阶段 2 完成信号：
  `profile -> front -> 文本回复` 已稳定跑通
- 阶段 3 完成信号：
  kernel 已稳定接入文本主链路
- 阶段 4 完成信号：
  新脑子已经可以稳定驱动 Reachy 动作和音频
- 阶段 5 完成信号：
  旧 assets 已被收口，旧脑子已退出主干

## 13. 一句话总结

这次改造不是“把旧 conversation app 修一修”，而是：

先把用户创建 agent 的单位从旧 app/profile 模板升级为新的 profile workspace，  
再让 `emoticorebot` 成为唯一大脑，最后把 Reachy 的身体能力完整接回去。
