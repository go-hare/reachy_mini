# Sim Front App Plan

## What You Want

- Create one generated Reachy Mini app project
- Run it inside the virtual robot workflow first
- Keep it as the place where we continue front/runtime work

## Current State

- App project has been created at `profiles/sim_front_app/`
- Generated entrypoint is `sim_front_app/main.py`
- Generated web app URL is `http://0.0.0.0:8042`
- Current generated models in `profiles/config.jsonl` are still `mock`

## Technical Approach

1. Start the MuJoCo simulated robot daemon
2. Start this generated app as a resident runtime
3. Open the web UI and verify websocket chat + surface state
4. After that, replace mock model config with the real model you want

## Run Commands

### 1. Start virtual robot

```bash
conda run -n reachy mjpython -m reachy_mini.daemon.app.main --sim
```

### 2. Start this app

```bash
conda run -n reachy python -m sim_front_app.main
```

Or from the repo root:

```bash
conda run -n reachy reachy-mini-agent web sim_front_app
```

## What We Should Decide Next

- Do we keep this app name: `sim_front_app`
- Do we switch `front_model` / `kernel_model` from `mock` to real API now
- Do we want this app to be chat-only first, or directly bind robot embodiment behavior
