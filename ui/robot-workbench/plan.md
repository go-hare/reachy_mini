# Robot Workbench 阶段计划

## 当前阶段

当前已完成：

1. 同步 Commander 桌面壳子到 `ui/robot-workbench/`
2. 删除登录、Git 设置、热键、旧 agent 状态链等不需要的部分
3. 在项目头部加入基础模型 / 状态信息

## 当前目标

把当前项目页升级成机器人工作台第一版三栏壳子：

- 左侧：工作台导航
- 中间：对话主区
- 右侧上半：MuJoCo
- 右侧下半：Reachy 状态

## 当前阶段范围

- 在 `ui/robot-workbench/` 内落地三栏布局
- 保持 Welcome 首页不改成三栏
- 保留聊天主区壳子与流式事件
- 把桌面端里的本地多 CLI 编排链继续删薄，为后续 Python WebSocket 后端让路
- 新增右侧机器人侧边区组件
- 先做壳子，不在本轮打通真实机器人链路

## 本阶段不做

- 不把 `profiles/` 数据模型彻底落地
- 不完整搬运 `reachy-mini-desktop-app` 全部系统流程
- 不接入完整 MuJoCo 渲染或 Reachy 控制

## 当前完成标准

- 项目页出现三栏结构
- 右侧面板清晰分成 MuJoCo 和 Reachy 两块
- 聊天主区仍可正常工作
- 为后续接入 Reachy 桌面端模块留下明确插槽

## 当前子阶段：内嵌 3D 仿真首版

### 目标

- 在右侧 MuJoCo 面板内直接显示 Reachy 3D 视图
- 保留桌面端 `Start Simulation` 启动链
- 启动后通过 Reachy daemon 的 websocket 状态流驱动机器人姿态
- 不依赖额外的 `9001/viewer` 网页服务

### 本轮范围

- 状态 websocket 补充 `head_joints` 与 `passive_joints`
- 右侧 MuJoCo 面板移除旧的 Web Viewer 交互
- 接入本地 URDF/STL 资产，直接渲染 Reachy 3D 机器人
- 保持现在的 `reachy-mini-daemon --sim` 启动链不变

### 本轮不做

- 不改 Python 主业务逻辑
- 不处理完整 MuJoCo 原生窗口嵌入
- 不实现单独的网页 viewer 服务

### 当前默认假设

- Reachy daemon 继续跑在 `http://localhost:8000`
- 3D 视图优先展示“桌面端内嵌机器人姿态”，而不是完整 MuJoCo 场景页面
