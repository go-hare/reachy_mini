# Runtime checks

## 2026-04-09 manual feature checks
- In session `caa37ddcafe84022`, a host tool roundtrip check succeeded: the assistant called `HostEcho` with token `LIVE_PENDING_OK` and then replied exactly `ROUNDTRIP_OK:LIVE_PENDING_OK`.
- `cognitive_events.jsonl` records that turn as a successful `tool_loop` outcome at `2026-04-09T10:02:09.114439+08:00`.
- A separate Kairos-triggered session `7cca78c93d7d4116` was running from `kairos_workspace` during consolidation, but the transcript only showed the cron wake prompt and host task-state events; no completed assistant wake reply was recorded yet.
