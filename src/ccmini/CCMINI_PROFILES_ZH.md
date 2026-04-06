# ccmini Profiles

`ccmini` 现在是一个共用内核，可以按不同宿主形态切成不同默认模式。

## 两个默认 profile

### `coding_assistant`
适合：
- 命令行编程助手
- UI 编程助手
- 代码审查、修改、调试、协作

默认特点：
- 开启内置命令层
- 开启 bundled skills
- 默认带协调者工具集
- 默认包含 `TeamDelete`

默认工具：
- `Agent`
- `SendMessage`
- `TaskStop`
- `TeamCreate`
- `ListPeers`
- `TeamDelete`

### `robot_brain`
适合：
- 机器人运行时
- 具身 agent
- 后台认知 + 前台外显场景

默认特点：
- 不自动加载 CLI 命令层
- 不自动加载 bundled skills
- 保留协调者核心闭环
- 默认不带 `TeamDelete`

默认工具：
- `Agent`
- `SendMessage`
- `TaskStop`
- `TeamCreate`
- `ListPeers`

## 推荐创建方式

通用工厂：

```python
from ccmini import create_agent

agent = create_agent(
    provider=provider,
    system_prompt="You are a helpful assistant.",
    profile="robot_brain",
)
```

快捷工厂：

```python
from ccmini import create_coding_agent, create_robot_agent

coding_agent = create_coding_agent(
    provider=provider,
    system_prompt="You are a coding assistant.",
)

robot_agent = create_robot_agent(
    provider=provider,
    system_prompt="You are a robot brain.",
)
```

## 设计原则

- 不删共用内核能力
- 差异主要体现在默认配置和默认工具装配
- 需要特殊能力时，宿主仍然可以手动传 `tools=...` 覆盖默认装配
