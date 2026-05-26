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

When this app is running, `ReachyMiniApp` hosts the resident v4 RuntimeSession and exposes:

- `GET /`
- `WS /ws/agent`

The dialogue flow uses the v4 wire protocol (each message is `{type, ts_ms, payload}`):

- browser sends `browser_input(kind="text")` for typed turns
- browser sends `audio_chunk` + `audio_stop` for raw PCM mic audio, with optional `speech_activity` boundaries
- runtime emits `transcription` previews and `brain_reply` with the full Brain decision
- runtime emits `action_result` and `worker_event` for action / worker progress
- `worker_event(task_id="__surface__")` carries surface-state transitions
- runtime emits `tts_audio` synthesized PCM
- errors arrive as `pipeline_error`
