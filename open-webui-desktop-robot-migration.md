# Open WebUI Desktop 改造成机器人平台方案

这份文档定义了一个新的桌面端路线：

- 不再把 `codex-rs/tui_app_server` 当成最终桌面 UI
- 改为把 `Open WebUI Desktop` 当成桌面壳底座
- 再把它替换成面向机器人平台的桌面应用

当前参考源码位置是：

- `C:\Users\Administrator\Downloads\desktop-main\desktop-main`

目标落地点是：

- `D:\work\py\reachy_mini\ui\robot-desktop`

## 为什么选这条线

相对 `codex-main` 来说，这条线更适合作为你的桌面端起点。

原因很直接：

1. 它是真正的桌面源码仓，不是只有产品入口命令。
2. 它已经具备标准 Electron 三层结构：
   - `main`
   - `preload`
   - `renderer`
3. 它已经具备“本地服务管理 + 远程连接 + 内容窗口加载”能力。
4. 它天然更适合改造成“你的 Python 服务 + 你的桌面壳”。

## 当前看到的关键事实

基于本地仓 `desktop-main` 的检查，已经确认：

- [README.md](C:/Users/Administrator/Downloads/desktop-main/desktop-main/README.md) 明确说明它是跨平台桌面应用
- [package.json](C:/Users/Administrator/Downloads/desktop-main/desktop-main/package.json) 明确使用了：
  - `electron`
  - `electron-vite`
  - `electron-builder`
- [src/main/index.ts](C:/Users/Administrator/Downloads/desktop-main/desktop-main/src/main/index.ts) 是桌面主进程
- [src/preload/index.ts](C:/Users/Administrator/Downloads/desktop-main/desktop-main/src/preload/index.ts) 是预加载桥
- [src/renderer/src/App.svelte](C:/Users/Administrator/Downloads/desktop-main/desktop-main/src/renderer/src/App.svelte) 是桌面壳前端入口

同时也确认了它当前和 Open WebUI 的耦合点：

- [src/main/utils/index.ts](C:/Users/Administrator/Downloads/desktop-main/desktop-main/src/main/utils/index.ts) 会：
  - 安装 Python
  - 安装 `uv`
  - 安装 `open-webui`
  - 运行 `uv run open-webui serve`
- [Connections.svelte](C:/Users/Administrator/Downloads/desktop-main/desktop-main/src/renderer/src/lib/components/Main/Connections.svelte) 有：
  - 本地安装流程
  - 远程连接流程
  - 连接状态管理

这说明：

- 这个仓能当桌面壳
- 但不能原样直接当机器人平台桌面端

## 修正后的目标架构

应该把目标架构定成下面这样：

```text
robot-desktop (Electron)
    ->
desktop preload bridge
    ->
robot backend launcher / process manager
    ->
Python robot gateway
    ->
front agent + kernel + 多任务 code-cli + robot runtime
```

在用户体验上，它应该表现为：

```text
启动 robot-desktop
    ->
自动检查/启动 Python 机器人服务
    ->
打开机器人工作台界面
    ->
建立 WebSocket / HTTP 通信
    ->
显示对话、任务、工具、机器人状态
```

## 这条线怎么改

### 第一层：保留桌面壳

保留这些能力：

- Electron 主窗口
- 系统托盘
- 自动更新框架
- 日志目录
- 本地配置文件
- 进程守护
- 终端/PTY 能力

这些已经是成熟桌面壳该有的基础设施，不需要重造。

### 第二层：删 Open WebUI 专用安装链

优先删或替换这些内容：

- `install open-webui package`
- `start open-webui serve`
- Open WebUI 专用连接命名
- Open WebUI 品牌文案
- Open WebUI 专用本地安装引导

保留“本地安装/本地服务/远程连接”这个框架，但把其中的目标从 `open-webui` 换成你的机器人服务。

### 第三层：替换内容区

当前它的内容连接逻辑，本质上是：

- 管连接
- 校验 URL
- `loadURL(...)`

这个机制可以先保留。

第一阶段最稳的策略不是立刻重写全部前端，而是：

1. 先让桌面壳能启动你的 Python 服务
2. 先让桌面壳能打开你的机器人前端页面
3. 再逐步把设置页、连接页、状态栏改成机器人平台语义

### 第四层：把“机器人平台语义”塞进去

后续再往桌面壳里加入：

- 机器人状态
- 任务队列
- agent 列表
- 内核状态
- 工具调用日志
- 终端工作区
- 机器人动作与安全提示

## 分阶段方案

## 阶段 0：落仓，不改逻辑

目标：

- 先把 `desktop-main` 同步进项目
- 保留原始结构
- 让后续每一次修改都发生在 `ui/robot-desktop/`

完成标准：

- `ui/robot-desktop/` 目录存在
- 有独立 `plan.md`
- 有改造说明文档

## 阶段 1：保留桌面壳，只保留远程连接模式

目标：

- 先把“Open WebUI 本地安装器”这条线关掉
- 保留桌面框架和远程连接能力

要做的事：

- 去掉本地安装入口默认引导
- 去掉自动安装 `open-webui` 的逻辑入口
- 保留：
  - 连接配置
  - URL 校验
  - 桌面窗口
  - 内容窗口
  - 配置持久化

完成标准：

- 应用仍能编译
- 应用能打开
- 应用能连接一个远程页面

## 阶段 2：把本地安装链替换成机器人服务启动链

目标：

- 让桌面端不再启动 `open-webui serve`
- 改为启动你的 Python 机器人服务

要做的事：

- 新增机器人服务启动脚本约定
- 替换 `startServer()` 的内部实现
- 替换安装状态文案
- 替换本地服务健康检查
- 替换默认端口和配置字段

完成标准：

- 从桌面壳里可以启动本地 Python 机器人服务
- 可以检测服务 ready
- 可以停止/重启服务

## 阶段 3：把远程页面切成机器人工作台

目标：

- 让内容窗口加载你的机器人平台页面

可选做法有两种：

1. 第一版先加载本地 Web 页面
2. 第二版再把更多 UI 移到 Electron renderer 内部

当前更推荐第一种，因为风险更低。

完成标准：

- 点击本地连接后，打开机器人工作台页面
- 页面能连接 Python WebSocket/HTTP 后端

## 阶段 4：裁掉 Open WebUI 专属外围功能

目标：

- 清掉和你产品无关的功能残留

候选删除项：

- Hugging Face 模型下载相关
- llama.cpp 相关
- Open Terminal 的 Open WebUI 专属联动
- Open WebUI 文案和资源图
- Open WebUI 专用设置项

原则：

- 只删和机器人平台无关的支线
- 先保壳，再做减法

## 阶段 5：变成真正的机器人平台桌面应用

目标：

- 从“能启动页面的桌面壳”变成“机器人控制台”

要补的能力：

- 机器人连接状态
- 机器人动作和安全状态
- agent 任务状态
- 多任务 code-cli 调度可视化
- 终端与日志面板
- 调试与运维入口

## 第一阶段最重要的决策

当前最关键的不是“要不要做桌面端”，而是先统一第一版技术形态。

第一版建议采用：

`Electron 桌面壳 + 本地/远程页面加载 + Python 机器人服务`

先不要一上来就做：

- 重写整个 renderer
- 直接做原生复杂机器人控制台
- 一次性改完全部品牌和功能

## 当前建议

当前建议是：

1. 先把 `desktop-main` 整体同步到 `ui/robot-desktop/`
2. 第一刀只做“保壳，去掉 Open WebUI 本地安装默认路径”
3. 优先保留远程连接模式
4. 用它先接你的 Python 机器人服务
5. 等链路跑通后，再做 UI 语义替换和功能裁剪

## 风险提醒

这条线有两个需要提前知道的点：

1. 许可证是 `AGPL-3.0`
2. 当前仓仍是 alpha，后续结构可能还会变

所以这条线适合：

- 快速拿到可改的桌面端底座
- 尽快推进原型和内测版本

但如果未来要做商业发行，需要单独复核许可证和发布策略。

## 一句话结论

`Open WebUI Desktop` 适合当你的桌面壳底座，但第一版必须走“保留桌面壳，替换后端，先远程后本地”的路线。`
