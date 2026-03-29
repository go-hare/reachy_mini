# `attending` Phase 升级草案

这份文档只回答一个问题：

- `attending` 要不要从 front-local phase 升级为系统级 stable phase？

当前答案已经收口成：

- 已升级
- 但只做最小升级
- 不附带大规模行为重写

否则它会停留在一个尴尬状态：

- front 觉得自己进入了 `attending`
- 执行层却把它当成 `idle`

这会让术语和真实行为分裂。

## 当前现状

当前代码已经出现了 `attending`：

- `FrontService` 会把 `vision_attention_updated` 映射成 `attending`
- 同时默认生成 `phase: attending` 的 `surface_patch`

但执行层当前仍然只稳定识别五个主相位：

- `idle`
- `settling`
- `listening_wait`
- `replying`
- `listening`

`SurfaceDriver` 不认识 `attending`。
`EmbodimentCoordinator` 也不认识 `attending`。

所以当前最准确的判断是：

- `attending` 最初来自 front 语义
- 现在已经是系统级 stable phase
- 但身体语义仍保持轻量承接

## 为什么不能只在 front 增加一个词

如果一个 phase 只是 front 自己知道，而执行层不知道，就会出现三个问题。

### 1. surface 归一化时被吞掉

当前 phase normalization 不识别 `attending` 时，它会退回到 `idle`。

这意味着：

- front 以为自己在关注
- 身体层却只看到待机

### 2. phase 优先级不明确

当前 `_PHASE_PRIORITY` 只覆盖五个稳定相位。

如果把 `attending` 放进系统，却不给它优先级，就没法明确回答：

- 它比 `idle` 高吗
- 它比 `listening_wait` 高吗
- 它会不会压过 `replying`

### 3. UI / shell 语义不一致

当前 Web 端也主要按五个主 phase 呈现状态。

如果 phase vocabulary 不统一，就会出现：

- front decision 说的是一套词
- UI 展示的是另一套词

## 升级判断标准

`attending` 只有在同时满足下面三个条件时，才值得升格。

### 条件 1：它有稳定输入源

也就是：

- `vision_attention_updated`
- 或新的 reactive vision 事件

已经是稳定生产事件，不只是测试里的消费占位。

如果没有稳定输入源，升 phase 只会制造空壳概念。

### 条件 2：它有独立行为语义

要能清楚说出：

- `attending` 和 `idle` 的身体行为差在哪
- `attending` 和 `listening` 的交互语义差在哪

如果只是“稍微更像在看人”，但行为上完全等同于 `idle`，
那它没必要单列成 stable phase。

### 条件 3：它需要跨层一致

也就是：

- front 要认
- surface 要认
- embodiment 要认
- UI 要认

如果它只在 front 层有意义，那它更适合作为 front-local phase，
而不是系统级 stable phase。

## 我建议的判断

按当前项目状态，这一步已经具备升级条件：

1. reactive vision event source 已进入 front 总线
2. `SurfaceDriver` 和 `EmbodimentCoordinator` 已能稳定识别 `attending`
3. UI/shell 已能直接展示 `attending`

所以当前优先级已经从“是否升级”变成：

1. 保持 `attending` 的轻量语义不膨胀
2. 后续只在确实需要时再补独立身体策略

## 一旦升级，推荐语义

如果 `attending` 升为 stable phase，我建议它只表达一种非常明确的状态：

- 系统已感知到值得关注的人或目标
- 当前没有进入正式 listening
- 也没有进入 replying
- 机器人处于轻量视觉关注和在场姿态

换句话说：

- 它不是“正在听”
- 它不是“正在答”
- 它是“我看到你，并且在看着你”

## 升级后的相位关系

推荐关系如下：

| Phase | 说明 | 相对优先级建议 |
| --- | --- | --- |
| `replying` | 正在组织/播报回复 | 最高之一 |
| `listening` | 正在接用户输入 | 最高之一 |
| `listening_wait` | 语音结束后的短等待 | 高 |
| `attending` | 轻量视觉关注 | 中 |
| `settling` | 回复后的短收尾 | 低中 |
| `idle` | 无显式交互关注 | 最低 |

我建议：

- `attending` 高于 `idle`
- `attending` 低于 `listening_wait`
- `attending` 不应压过 `listening` 和 `replying`

这样最符合直觉。

## 推荐身体语义

如果升级，`attending` 不应该只是一个标签。

它至少应有这些可见区别：

- 保持轻量朝向目标
- 抑制过度 idle scan
- 维持 presence，但不抢占 speaking/listening
- 在目标消失后平滑退回 `idle`

更重要的是，它不应该做这些事：

- 不应触发大幅显式动作
- 不应开启复杂情绪外显
- 不应压过用户说话

## 推荐迁移顺序

这块最怕同时改太多层。

最稳的顺序是四步。

### 第一步：补齐视觉事件生产

现在已经完成：

- reactive vision event emitter
- 到 front 的正式输入通路

### 第二步：给执行层补最小承接

现在已经完成三层最小承接：

- `SurfaceDriver` phase normalization
- `_PHASE_PRIORITY`
- `EmbodimentCoordinator` phase normalization

这一步只做“认得它”，没有附带大规模行为重写。

### 第三步：补 UI / shell 呈现

现在已经补上：

- Web 状态展示
- debug 视图
- 可能的 telemetry 文本

这样可以避免 UI 先讲一套，身体层还是另一套。

## 需要改动的具体面

如果进入升级实施，至少会碰这几个面。

### 1. front

当前已有：

- `attending` lifecycle 映射
- `attending` surface patch

这里主要不用大改。

### 2. surface driver

需要补：

- `_PHASE_PRIORITY["attending"]`
- `_normalize_phase()` 接受 `attending`

这是 phase 成为 stable phase 的第一道门。

### 3. embodiment coordinator

需要补：

- `_normalize_phase()` 接受 `attending`

必要时再定义：

- `attending` 对 head tracking / movement activity 的轻量行为

### 4. app/web 呈现

需要补：

- `formatSurfaceStatus()` 的 `attending` 文案
- 任何 phase badge / debug 可视化

## 不推荐的做法

### 1. 现在就把 `attending` 强行塞进所有层

这会导致：

- 视觉输入源还没稳定
- phase 先扩散全系统

收益不大，反而增加维护成本。

### 2. 把 `attending` 做成和 `listening` 一样重

`attending` 本质上应该是轻量相位。

它是：

- “在看”

不是：

- “在正式交互收音”

### 3. 用 `attending` 替代所有视觉语义

`attending` 只是 phase，不是完整视觉状态模型。

不要把下面这些也全塞进它：

- target id
- engagement score
- tracking mode
- person count

这些应该保留在视觉事件 metadata 或 front state 里。

## 最小升级门槛

我建议只要满足下面四项，就可以考虑正式升级：

1. `attention_acquired / attention_released` 已有稳定事件生产
2. `vision_attention_updated` 已经不只是测试入口
3. 执行层已经有最小 `attending` 语义
4. UI 至少能正确显示 `attending`

在这之前，保留 front-local status 更稳。

## 当前结论

`attending` 不是不能升。

只是当前最合理的节奏不是“先升词”，而是：

1. 先补视觉事件源
2. 再让 phase 跨层一致

所以现在的最佳判断是：

- `attending` 已经是一个正确的 front 语义
- 但还没到必须升级成系统级 stable phase 的时机
