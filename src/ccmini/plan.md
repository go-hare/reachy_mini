## Implementation Plan

### Summary
使用用户提供的 `base_url / api_key / model`，新增一套不依赖现有测试文件的 live 主流程测试脚本，覆盖 `ccmini` 的核心运行链路与关键交互模式。

### Goals
1. 验证 OpenAI 兼容 provider 能正常完成文本请求
2. 验证 `Agent.query()` 阻塞式主流程
3. 验证 server-side tool call、tool progress、tool result 回流
4. 验证 client-side pending tool call 与 `submit_tool_results()` 续跑
5. 验证 `Agent.submit()/wait_event()/wait_reply()` 非阻塞主流程
6. 验证多轮上下文连续性
7. 验证 `AgentTool` 子代理同步与后台运行路径
8. 验证 `AgentPool / Pipeline / Debate / Handoff` 多代理编排
9. 验证 `TeamCreate / SendMessage / TeamDelete` 与 persistent teammate 邮箱协作
10. 验证 Kairos 核心激活、channel 入队、sleep/wake、cron 存储路径
11. 验证 Buddy hatch 与 companion intro 自动注入
12. 验证 runtime memory 写入、cognitive 事件与 projection 更新
13. 验证任务系统 CRUD、依赖关系与 TaskOutput
14. 验证 bridge 本地远程执行链路与事件回放
15. 验证 frontend host 辅助启动路径与 ready payload 生成
16. 验证目录型 plugin registry 的工具 / hook / command 装载
17. 验证 distribution entry points 的 tool / hook 装载
18. 验证 MCP manager / wrapper / instructions surface
19. 验证 slash command 层的真实路由、运行时状态读取与本地副作用路径
20. 验证高价值本地工具层：文件、shell、REPL、web、notebook、workflow、plan mode、worktree
21. 验证内建 HTTP server 的 REST 路由、session/event 生命周期与 tool-results 回流
22. 验证 bridge 的 WebSocket / SSE 传输层与 WebRTC 当前失败边界
23. 验证 MCP 的 resource/client 转换、skill bridge 与 OAuth 状态持久化面
24. 验证 frontend 会话管理层、bridge 事件适配层与 CLI 入口
25. 验证 attachments / image / document / multimodal host content 处理链
26. 验证长会话 soak、多 session 并发、故障注入恢复与 FastMCP stdio 真 server 成功链路

### Approach
1. 只阅读源码与文档，不复用现有 `tests/` 内测试文件
2. 新增独立 live harness，使用最小自定义工具集做可控 E2E
3. 通过 CLI 参数或环境变量注入密钥，避免把密钥写入仓库
4. 将主链、delegation/team、runtime features、integration surfaces 分为多个入口，分别运行并汇总结果
5. 对会写本地状态的模块优先指向临时目录，减少对真实环境的污染
6. 实际连用户提供的接口执行测试，输出逐项结果与失败细节

### Files to Modify
- `D:\work\py\reachy_mini\src\ccmini\plan.md`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\full_flow_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\delegation_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\harness_core.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\runtime_features_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\integration_surfaces_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\command_surfaces_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\tool_surfaces_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\server_transport_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\mcp_extended_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\frontend_session_live.ts`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\frontend_runtime_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\attachments_multimodal_live.py`
- `D:\work\py\reachy_mini\src\ccmini\live_checks\robustness_live.py`

### Risks
- 第三方网关对 `chat.completions`、tool calling 或 streaming 的兼容度可能不完整
- 强依赖模型按提示稳定调用指定工具，个别步骤可能出现模型偏差

### Clarifications
- 当前假设“全方位主流程测试”以 `ccmini` 核心运行链路为准，不扩展到前端 UI 自动化或 bridge HTTP 端到端
- 当前假设允许新增独立脚本目录，但不进入现有 `tests/`

### Verification Steps
1. 运行新增 live harness，使用用户提供的真实接口配置
2. 检查每个覆盖项的 PASS / FAIL、事件类型、最终回复与错误明细
3. 如有失败，定位是 provider 兼容问题、模型行为偏差，还是框架执行链问题
