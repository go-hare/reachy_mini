# Manual feature checks

## 2026-04-09 deep live smoke rerun

Context: `.ccmini/manual_feature_checks/deep_live_smoke_rerun_20260409_095700`

Durable observations:
- A live smoke check verified the pending-output tool roundtrip path using `HostEcho` with token `LIVE_PENDING_OK`.
- In session `0c8161e3e3b94c62`, the tool returned `LIVE_PENDING_OK` and the assistant completed with the exact expected response `ROUNDTRIP_OK:LIVE_PENDING_OK`.
- Transcript metadata for that session shows `turn_phase: "completed"`, `last_stop_reason: "stop"`, and `pending_tool_count: 0`, so the roundtrip finished cleanly.
- Another related session, `0560b9d8df634869`, ran in `kairos_workspace` under the same manual feature check directory and contains host `task_state` events for local-agent runs, but the narrow transcript skim did not expose a durable success/failure detail worth storing yet.

Implication:
- The deep live smoke rerun on 2026-04-09 provides at least one confirmed good example of end-to-end pending tool output handling in this environment.
