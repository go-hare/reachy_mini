# sim_front_app 轻量化桌宠方案

## 你的当前目标

- 继续使用当前仓库 `D:\work\py\reachy_mini`
- 把 `profiles/sim_front_app` 收敛成桌宠模式对应的 app
- 桌宠模式只负责小窗展示、气泡对话、基础状态反馈
- 不再把 MuJoCo 3D 仿真当成桌宠默认依赖
- 右侧 app / profile 选择逻辑保留，桌宠只是其中一种启动模式

## 已确认的事实

- `profiles/sim_front_app/sim_front_app/main.py` 现在调用的是 `ReachyMiniApp.wrapped_run()`
- 这条链会创建 `ReachyMini(...)`，所以天然带有 daemon / robot 连接语义
- `reachy-mini-daemon` 已支持 `--mockup-sim`
- 源码里对 `mockup_sim` 的定义就是 lightweight simulation mode，也就是轻量仿真，不依赖 MuJoCo
- `reachy-mini-agent web <app>` 已支持 host-only web runtime，可以在不连硬件的情况下跑 app 的 web UI
- 之前那条“全屏透明桌宠”路线已经判定不合适，桌宠后续只能走“小透明独立窗”路线

## 现在不做的事

- 不再让桌宠模式默认启动 MuJoCo
- 不再把桌宠做成覆盖整块桌面的透明层
- 不在这一阶段重写整套 front agent + kernel + multi-task CLI 架构
- 不在这一阶段把 MuJoCo 内嵌进桌宠窗口

## 两条可行路线

### 路线 A：保留现有 app / daemon 契约，桌宠模式切到 `mockup-sim`

这条路线的核心不是重写 app，而是把桌宠模式底层从 `--sim` 切成 `--mockup-sim`。

优点：

- 改动最小
- 继续复用当前桌面端已经接好的 daemon 状态、profile、app 启动链
- `sim_front_app` 不需要立刻重写成纯前端壳
- 最快能把“桌宠启动太重”这个问题压下来

代价：

- 仍然会有 daemon 进程
- 仍然保留“像机器人 app 一样运行”的约束
- 它是轻量化，不是彻底去运行时依赖

适合当前阶段的原因：

- 你现在要先把桌宠模式跑顺
- 你现在最痛的是 MuJoCo 太重，不是 daemon 这层一定不能存在
- 这条路线最容易和当前桌面端结构兼容

### 路线 B：把桌宠 app 抽成 host-only runtime

这条路线的入口是：

```bash
reachy-mini-agent web sim_front_app
```

它更接近“桌宠只是 UI + 状态机 + WebSocket”的目标。

优点：

- 更轻
- 更接近你后面要替换 agent 执行链的方向
- 更适合以后直接接 Python 服务 / WebSocket，而不是绑死在机器人 SDK 生命周期上

代价：

- 需要把 `sim_front_app` 从 `wrapped_run()` 语义里逐步抽出来
- 当前桌面端里和 daemon 状态、右侧 profile 启动相关的链路要重新梳理
- 第一阶段直接走这条路，改动面会更大

## 推荐结论

第一阶段先走路线 A。

推荐原因：

- 目标更聚焦，先解决“桌宠不能再依赖 MuJoCo”
- 不需要现在就重做整套宿主架构
- 右侧 profile / app 选择链路可以先保留
- 等桌宠 UI、气泡对话、小窗交互稳定后，再评估是否演进到路线 B

## 分阶段方案

### Phase 0：收口现状

- 保持桌宠窗口为小透明独立窗
- 保持可拖动、可显示气泡
- 停止继续给桌宠接入 MuJoCo 窗口或全屏覆盖层

### Phase 1：桌宠模式切到轻量仿真

- 桌面端里“桌宠模式”的启动命令从 `--sim` 改成 `--mockup-sim`
- 点击桌宠模式时，不再拉起 MuJoCo
- 右侧 app / profile 选择仍按当前桌面端链路处理
- `sim_front_app` 继续作为桌宠 app 的承载面

这一阶段的判断标准：

- 点击桌宠模式后，不会出现 MuJoCo
- daemon 日志里应表现为 `mockup_sim=True`
- 桌宠小窗能正常打开
- 右侧 app / profile 仍能继续工作

### Phase 2：清理桌宠 app 内容

- 把 `sim_front_app` 继续收敛成桌宠界面
- 只保留桌宠真正需要的元素：2D 角色、状态、气泡、最小控制项
- 继续删除调试面板、监控块和与桌宠无关的页面元素

### Phase 3：评估是否切到 host-only runtime

- 如果后面确认桌宠只需要 UI + WebSocket + 本地 Python 服务
- 并且不再需要复用当前 app / daemon 契约
- 再把桌宠 profile 从 `wrapped_run()` 迁到 `reachy-mini-agent web` 这条更轻的链路

## 当前建议的开发命令

### 轻量仿真验证

```bash
conda run -n reachy reachy-mini-daemon --mockup-sim
```

### 当前 app 验证

```bash
conda run -n reachy python -m sim_front_app.main
```

### 仅验证 host-only web runtime 的备用命令

这个不是第一阶段主线，只作为后续备选验证入口：

```bash
conda run -n reachy reachy-mini-agent web sim_front_app
```

## 这份方案下的默认决策

- 桌宠模式默认不启动 MuJoCo
- 桌宠模式默认走轻量仿真
- MuJoCo 仍然保留为独立模式，不并入桌宠
- 右侧 app / profile 选择逻辑先不推倒重来

## 待你确认的问题

问题 1：桌宠模式被选中后，是否要自动拉起轻量仿真？

答案：

- 暂定是

问题 2：桌宠模式下，右侧选择 app 之后，是否仍然走当前的 profile 启动链？

答案：

- 暂定是

问题 3：桌宠模式是否默认关闭 3D / 摄像头类能力，只保留轻量状态和对话？

答案：

- 暂定是

问题 4：macOS 的桌宠模式启动命令，是否要单独做平台映射，而不是和 Windows 共用一条命令？

答案：

- 暂定是

## 下一步该做什么

下一步不是继续讨论桌宠长什么样，而是直接改桌面端的启动映射：

1. 把“桌宠模式”对应的 daemon 启动命令切到 `--mockup-sim`
2. 确认右侧 profile / app 选择不会再误拉起 MuJoCo
3. 再继续删 `sim_front_app` 里不属于桌宠的页面元素
