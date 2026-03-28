# MuJoCo Web Viewer 服务设计说明

这份文档定义一个面向 `ui/robot-workbench` 的本地 MuJoCo Web Viewer 服务。

目标不是改掉 Reachy Mini 现有仿真内核，而是在桌面端里补一层“可内嵌的浏览器 viewer 服务”，让右侧面板可以显示 MuJoCo 画面，而不是只能依赖原生弹窗。

## 先说结论

当前仓库里并没有现成的 `http://127.0.0.1:9001/viewer` 服务。

现在 Reachy Mini 的 MuJoCo 仿真链路是：

1. 启动 `reachy-mini-daemon --sim`
2. Python 后端直接调用 `mujoco.viewer.launch_passive(...)`
3. MuJoCo 打开一个原生 3D 窗口

也就是说：

- `9001/viewer` 不是现成能力
- 右侧面板里想嵌入网页 viewer，就必须额外补一个本地服务
- 这个服务应该被视为“桌面端配套组件”，不是 Reachy Mini 现有主仿真流程的一部分

## 现状依据

现有文档明确写的是“启动仿真后会弹出窗口”：

- `docs/source/platforms/simulation/get_started.md`

现有 MuJoCo backend 也是直接走原生 viewer：

- `src/reachy_mini/daemon/backend/mujoco/backend.py`

其中关键事实有两个：

1. 仿真主 viewer 是 `mujoco.viewer.launch_passive(...)`
2. 现有离屏渲染线程会把画面发到 UDP，但这条链路不是浏览器页面，也不是 `9001/viewer`

因此，“桌面端右侧内嵌 MuJoCo”这件事，本质上是一个新增能力，不是接个现成地址就能完成。

## 我们要的能力

桌面端最终要的是下面这条链路：

`robot-workbench(Tauri + React) -> 本地 Python viewer service -> MuJoCo 渲染输出`

用户体验目标：

- Reachy 仿真仍然可以正常启动
- 右侧 MuJoCo 区域优先显示桌面内嵌 viewer
- 没有 viewer 服务时，UI 不报错，只显示“服务未启动”
- viewer 服务可以由桌面端一键启动和停止

## 不建议的几条路

### 1. 不建议继续把 `9001/viewer` 当成现成地址

因为当前项目没有这个服务，继续硬写死只会让 UI 长期显示“拒绝连接”。

### 2. 不建议直接把 MuJoCo 原生窗口硬塞进 WebView

`mujoco.viewer.launch_passive(...)` 是原生 GUI，不是浏览器 DOM，也不是网页 canvas。它不能自然地被 Tauri WebView 直接内嵌。

### 3. 不建议第一版就做“第二套完整仿真”

如果新服务自己再起一套完整 MuJoCo 物理世界，再和 daemon 双向同步，状态漂移和维护成本都会很高。

第一版更应该做“渲染服务”，而不是“第二个仿真内核”。

## 推荐方案

推荐采用：

`B 方案：本地 Python 渲染服务（Render-only Viewer Service）`

也就是：

- Reachy 的主仿真仍由 `reachy-mini-daemon --sim` 负责
- 新增一个独立 Python 服务，只负责把 MuJoCo 画面变成浏览器可嵌入的页面
- 桌面端右侧只嵌这个本地 viewer 页面

### 为什么推荐这条路

因为它把职责切得比较清楚：

- daemon 继续管机器人仿真和 API
- viewer service 只管画面输出
- robot-workbench 只管桌面端展示和启动流程

这样不会把 Reachy 主逻辑和桌面 UI 强耦合在一起。

## Viewer Service 的推荐定位

第一版 viewer service 推荐定位为：

- 本地服务
- Python 实现
- 默认只绑定 `127.0.0.1`
- 默认端口 `9001`
- 优先服务于桌面端内嵌，不追求浏览器公网部署

建议默认启动命令形态：

```bash
conda run -n reachy python -m reachy_mini.viewer_service --host 127.0.0.1 --port 9001 --daemon http://127.0.0.1:8000
```

## 第一版该怎么做

第一版不要追求“完全复刻 MuJoCo 原生 viewer 的交互”，先把“看见画面”做通。

### 第一版目标

- 桌面端访问 `http://127.0.0.1:9001/viewer`
- 页面能显示连续渲染画面
- 页面可在 Tauri 右侧 iframe 中稳定展示
- 服务不在时，桌面端明确提示“viewer 服务未启动”

### 第一版先不做

- 浏览器内自由拖拽 3D 视角
- 复杂的场景编辑
- 与 MuJoCo 原生 viewer 完全一致的交互
- 多客户端同步控制

## 渲染来源有三条路

实现这个服务，技术上有三条路。

### 路线 A：复用 daemon 的现有输出

思路：

- 让 daemon 继续产出离屏画面
- viewer service 只负责把它包装成浏览器可访问的流

优点：

- 不复制渲染逻辑
- 和当前仿真链路接近

缺点：

- 现有离屏输出更接近“相机流”，不一定是真正想要的 3D viewer 视角
- 如果要改成自由视角，最终还是要动 daemon 侧渲染输出

### 路线 B：viewer service 自己做“渲染副本”

思路：

- viewer service 加载同一份 MJCF
- 订阅 daemon 的状态流
- 把机器人姿态同步到本地渲染副本
- 用离屏 renderer 输出浏览器画面

优点：

- 不需要把桌面端和主 daemon 绑死在同一渲染实现上
- 桌面端可以独立演进 viewer 样式和相机逻辑

缺点：

- 第一版只能保证机器人姿态同步
- 如果以后要看完整场景物体状态，还需要额外同步更多世界状态

### 路线 C：捕获原生窗口

思路：

- 把原生 MuJoCo viewer 窗口抓屏，再转成 Web 页面流

优点：

- 可能最快看到“像 3D viewer 一样的画面”

缺点：

- 实现脆弱
- 平台差异大
- 不适合长期维护

## 推荐的技术路线

推荐顺序是：

1. 先按路线 B 做最小可用版
2. 如果后面发现 daemon 本身已经适合直接输出 3D viewer 画面，再评估切到路线 A
3. 不采用路线 C 作为正式方案

原因很简单：

- 路线 B 适合先把桌面端内嵌能力做出来
- 路线 A 以后可能更高效，但前提是先把 daemon 渲染接口理顺
- 路线 C 更像临时 hack，不适合做平台底座

## 最小服务接口

第一版 viewer service 建议只暴露这几个接口。

### 1. 健康检查

```http
GET /health
```

返回示例：

```json
{
  "ok": true,
  "service": "mujoco-viewer",
  "mode": "render-only",
  "daemon_url": "http://127.0.0.1:8000",
  "fps": 20
}
```

用途：

- 桌面端探测服务是否在线
- 右侧面板决定是否显示 iframe

### 2. Viewer 页面

```http
GET /viewer
```

返回：

- 一个本地 HTML 页面
- 页面内部连接本服务的流接口
- 桌面端 iframe 直接加载这个地址

### 3. 画面流

第一版建议二选一：

- `GET /stream.mjpeg`
- `WS /ws/frames`

推荐先用：

```http
GET /stream.mjpeg
```

因为它实现最简单，浏览器兼容性也最好。

### 4. 预留状态接口

```http
GET /state
```

返回 viewer 当前状态，例如：

```json
{
  "connected_to_daemon": true,
  "rendering": true,
  "scene": "empty",
  "camera": "free",
  "frame_size": [1280, 720]
}
```

## 建议的服务内部结构

建议做成下面这几个模块：

### 1. `config.py`

负责：

- host
- port
- daemon url
- 帧率
- 图像尺寸
- 默认相机参数

### 2. `daemon_client.py`

负责：

- 连接 Reachy daemon
- 订阅状态
- 处理重连
- 对外提供最新机器人状态

### 3. `renderer.py`

负责：

- 加载 MJCF
- 维护本地 MuJoCo 渲染副本
- 根据 daemon 状态更新机器人姿态
- 输出 JPEG 帧

### 4. `server.py`

负责：

- `FastAPI` 或 `Starlette`
- `/health`
- `/viewer`
- `/stream.mjpeg`
- `/state`

### 5. `static/viewer.html`

负责：

- 浏览器页面
- 显示 MJPEG 或 websocket 帧
- 后续可加相机控制按钮

## 推荐的 Python 技术栈

第一版建议尽量简单：

- `FastAPI`
- `uvicorn`
- `mujoco`
- `Pillow` 或 OpenCV 用于 JPEG 编码
- `websockets` 或 `httpx` 处理 daemon 连接

如果只做 MJPEG，甚至可以先不引入 websocket 输出层。

## 桌面端如何接

桌面端目前已经有这两个设置字段：

- `mujoco_viewer_url`
- `mujoco_viewer_launch_command`

所以桌面端只需要继续沿着这个模型走：

1. 设置里保存 viewer URL
2. 设置里保存 viewer 启动命令
3. 右侧点击 `Start Viewer`
4. Tauri 拉起 Python viewer service
5. 前端先探测 `/health`
6. 在线后再嵌入 `/viewer`

也就是说，桌面端不需要知道 MuJoCo 细节，只要会：

- 启动进程
- 轮询健康状态
- 嵌入页面

## 桌面端需要遵守的显示规则

为了避免现在这种“直接显示浏览器拒绝连接错误页”，右侧面板应当遵守下面的规则：

### 情况 1：没填 URL

显示：

- “当前未配置 MuJoCo Web Viewer”

### 情况 2：填了 URL，但服务没启动

显示：

- “Viewer 服务未启动”
- “当前地址不可达”
- 可见 `Start Viewer` 按钮

### 情况 3：服务在线，但没有画面

显示：

- “Viewer 已启动，等待第一帧”

### 情况 4：服务在线且有画面

显示 iframe

## 阶段性实施方案

### 阶段 0：先把 UI 占位体验修好

目标：

- 没有 viewer 服务时不再展示错误页
- 改为健康探测 + 占位提示

这一步只改桌面端。

### 阶段 1：先做 viewer service 空壳

目标：

- 启动 Python 服务
- `/health` 可访问
- `/viewer` 返回一个简单页面
- 桌面端可以一键启动并探测在线状态

这一步先不输出真实 MuJoCo 帧。

### 阶段 2：接入最小画面流

目标：

- 页面能看到连续帧
- 先保证“能看”
- 先不做鼠标交互

### 阶段 3：补 viewer 控制

目标：

- 重载视角
- 预设相机位
- 暂停/恢复渲染

### 阶段 4：再评估是否需要复杂交互

只有在前面三步都稳定后，再考虑：

- 拖拽旋转
- 自由缩放
- 场景对象调试
- 多路同步

## 推荐的默认配置

建议桌面端默认值采用下面这组：

```text
mujoco_viewer_url = http://127.0.0.1:9001/viewer
mujoco_viewer_launch_command = conda run -n reachy python -m reachy_mini.viewer_service --host 127.0.0.1 --port 9001 --daemon http://127.0.0.1:8000
```

如果用户没有实现这个模块，桌面端应视为“未配置完成”，而不是直接尝试打开错误页面。

## 风险与边界

### 1. 第一版不一定是“真 MuJoCo 原生 viewer”

第一版更准确地说，是“MuJoCo 渲染服务”，不是要 1:1 复刻原生 GUI。

### 2. 世界状态同步可能不完整

如果 viewer service 只是同步机器人关节状态，而不是同步整个物理世界状态，那么场景里动态物体可能与主仿真不完全一致。

### 3. 帧率与 CPU 需要平衡

桌面端内嵌 viewer 不需要一开始就冲高帧率。

建议第一版：

- 15 到 20 FPS 即可
- 优先保证稳定

### 4. Windows 打包要注意 Python 环境

如果以后要把 viewer service 做成“最终用户可用”的桌面应用能力，需要进一步考虑：

- Python 环境发现
- conda/venv 兼容
- 命令失败提示

## 当前推荐结论

对这个项目，当前最合理的路线是：

1. 承认 `9001/viewer` 现在并不存在
2. 把它定义为一个新的本地 Python viewer service
3. 第一版只做 render-only
4. 桌面端先按“健康探测后再嵌入”的模式接入

## 下一步建议

下一步建议按下面顺序做：

1. 先改桌面端：没有服务时不显示 iframe 错误页
2. 再补 Python viewer service 空壳：先通 `/health` 和 `/viewer`
3. 最后再接真实 MuJoCo 画面流

如果继续往下做，下一份文档建议补：

- `viewer-service-minimal-api.zh-CN.md`
- `viewer-service-python-skeleton.zh-CN.md`

这样后面就可以直接照着接口开始落代码，而不会再在“这个服务到底是什么”上反复来回。
