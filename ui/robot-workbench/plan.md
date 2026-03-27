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
- 保留现有聊天能力
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
