# Reachy Status Live Panel TDD Plan

## Scope

Deliver the first real Reachy websocket-backed status card for `ui/robot-workbench`.

## Assumptions

- Default daemon URL is `http://localhost:8000`.
- The live panel reads from `ws://<daemon>/api/state/ws/full?with_doa=true`.
- The stream uses the default `XYZRPYPose` payload shape from the Reachy daemon.

## Tests First

### Frontend

1. Add a focused Reachy status panel test file.
   - Disabled mode shows disabled copy and does not create a WebSocket.
   - Live mode renders streamed state after a WebSocket message.
   - Disconnect/error mode falls back to an offline badge/message.

2. Add a focused Settings modal/general settings test.
   - Toggling live status persists `robot_settings.live_status_enabled`.
   - Editing the daemon URL persists `robot_settings.daemon_base_url`.

### Rust / Persistence

1. Extend app settings serde tests.
   - `robot_settings` defaults round-trip.
   - Blank or malformed daemon URL normalizes to the default URL.

2. Extend settings persistence integration coverage.
   - Saving and reloading preserves `robot_settings`.

## Implementation Steps

1. Extend the shared settings types in Rust and TypeScript.
2. Thread `robot_settings` through the settings context and settings modal autosave flow.
3. Add a small `useReachyStatus` hook for WebSocket lifecycle + reconnect handling.
4. Replace the placeholder metrics in `ReachyStatusPanel` with live state rendering.
5. Keep `MujocoPanel` untouched except for any shared copy cleanup.

## Verification

- `bun run test src/components/__tests__/RobotSidePanel.reachyStatus.test.tsx`
- `bun run test src/components/settings/__tests__/SettingsModal.robotStatus.autosave.test.tsx`
- `cargo test app_settings --manifest-path src-tauri/Cargo.toml`
- `cargo test settings_file_persistence --manifest-path src-tauri/Cargo.toml`
- Re-run the existing robot workbench shell test after the live card lands.
