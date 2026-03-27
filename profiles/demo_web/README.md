---
title: Demo Web
emoji: 🤖
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Reachy Mini app project
tags:
 - reachy_mini
 - reachy_mini_python_app
---

This app includes a startup shell in `demo_web/main.py`.

The editable app profile files live in `profiles/`:

- `AGENTS.md`
- `USER.md`
- `SOUL.md`
- `TOOLS.md`
- `FRONT.md`
- `config.jsonl`
- `memory/`
- `session/`
- `tools/`
- `prompts/`
- `skills/`

When this app is running, `ReachyMiniApp` hosts the resident runtime directly and exposes:

- `GET /`
- `WS /ws/agent`

The dialogue flow is streamed over WebSocket:

- browser sends `user_text`
- browser can also emit `user_speech_started` / `user_speech_stopped`
- supported browsers can turn microphone speech into the final `user_text`
- runtime emits `front_hint_*`
- runtime keeps pushing `surface_state`
- runtime emits `front_final_*`
- errors are emitted as `turn_error`
