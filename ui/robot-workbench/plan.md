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

## 当前子阶段：Web Viewer 接入口

### 目标

- 不做 MuJoCo 原生窗口嵌入
- 先在桌面端铺好 Web Viewer 接入口
- 后续只要本地有 viewer 服务起在固定端口，工作台就能直接接上

### 本轮范围

- 设置页补 `MuJoCo Web Viewer` 的本地预设入口
- 右侧 `Viewer Surface` 支持：
  - 一键填本地预设地址
  - 内嵌 iframe 预览
  - 用系统浏览器打开
  - 手动刷新 iframe
- 保持现在的 `reachy-mini-daemon --sim` 启动链不变

### 本轮不做

- 不改 Python 主业务逻辑
- 不实现真正的 Web Viewer 服务
- 不处理原生 MuJoCo 窗口嵌入

### 当前默认假设

- 本地 Web Viewer 预设地址使用 `http://127.0.0.1:9001/viewer`
- 设置默认值仍保持空，避免误导当前仍走原生窗口的用户
