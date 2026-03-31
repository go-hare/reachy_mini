# 3D -> 2D Sprite Export Plan

## Understanding

目标：基于 `ui/robot-workbench` 现有的 Reachy 3D Viewer，补一个开发工具页，把固定相机下的 6 个桌宠动作姿态直接导出成 2D PNG 帧，尽量做到一键导出。

本轮默认先交付：

- 6 个单帧动作：`idle` / `listen` / `think` / `speak` / `sleep` / `drag`
- 透明背景 PNG
- 固定相机、固定取景
- 在桌面端里一键导出到本地目录

## Technical Approach

1. 在前端新增 `SpriteExporter` 页面
2. 复用现有 `Viewer3D`，直接喂静态 `headJoints` + `antennas`
3. 通过 `canvas.toDataURL('image/png')` 抓取透明 PNG
4. 在 Tauri 侧新增命令，把 6 张图写入 `.codex-runtime/sprite-export/<timestamp>/`
5. 在 `main.jsx` 增加 `#sprite-export` 入口，方便直接打开

## Default Assumptions

- 默认导出目录：`ui/robot-workbench/.codex-runtime/sprite-export/`
- 默认不做多帧动画，只先导出每个动作 1 帧
- 默认不做自动裁边，先保留统一画布尺寸，方便后续拼 sprite sheet

## Clarifications

如果你后面要继续精修，这里有两个可调项：

- 目标像素尺寸：
  默认答案：先用当前 Viewer 画布导出
- 动作命名：
  默认答案：先用 `idle/listen/think/speak/sleep/drag`

当前没有阻塞问题，先按以上默认值直接实现。
