# Integrations & Apps

Reachy Mini supports both direct SDK integrations and a resident runtime hosted by generated app projects.

## App Projects and Resident Runtime

The main local AI workflow in this repository is based on user-created app projects under `profiles/<name>/` plus the `reachy-mini-agent` runtime.

Create a new app project:

```bash
reachy-mini-agent create my_app
```

Run the resident runtime:

```bash
reachy-mini-agent agent my_app
```

Run the generated app's web UI without connecting Reachy hardware:

```bash
reachy-mini-agent web my_app
```

Then open:

```text
http://127.0.0.1:8042/
```

Each user-created app project lives under `profiles/<name>/`. The runtime loads the inner `profiles/` directory from that project and uses it as content, config, prompts, tools, memory, and session state. The app project does not ship its own separate runtime host.

Each app project contains:

```text
profiles/my_app/
├── README.md
├── pyproject.toml
├── .gitignore
├── index.html
├── style.css
├── my_app/
│   ├── __init__.py
│   ├── main.py
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── main.js
└── profiles/
    ├── AGENTS.md
    ├── USER.md
    ├── SOUL.md
    ├── TOOLS.md
    ├── FRONT.md
    ├── config.jsonl
    ├── memory/
    ├── skills/
    ├── session/
    ├── tools/
    └── prompts/
```

Suggested responsibilities:

- `AGENTS.md`: hard rules, operating constraints, and stable behavioral policy
- `USER.md`: durable user context and relationship-specific notes
- `SOUL.md`: stable personality, values, and emotional baseline
- `TOOLS.md`: tool policy, permissions, and execution boundaries
- `FRONT.md`: user-visible style and wording constraints
- `config.jsonl`: runtime configuration for front/kernel models and history
- `memory/`: durable memory storage
- `session/`: per-thread session streams such as `front.jsonl` and `brain.jsonl`

Tool loading is layered:

- System tools: built into the runtime and available across app projects
- Profile tools: optional Python tools loaded from `profiles/<name>/profiles/tools/`

The runtime merges them in that order. System tools cover the common workspace actions for the current app project. Profile tools are where app-specific capabilities should be added.

At startup, the runtime creates a resident kernel and keeps it running in the background for the process lifetime. User turns flow through:

`app project -> front -> BrainKernel -> front`

The resident lifecycle is:

- `start()`
- `publish_user_input()`
- `recv_output()`
- `stop()`

Where this happens in code:

- CLI entry: `reachy_mini.runtime.main`
- Runtime assembly: `RuntimeScheduler.from_profile(...)`
- Resident kernel bridge: `RuntimeScheduler.start()` and `RuntimeScheduler.stop()`

From the CLI, this runtime stays alive for as long as the `reachy-mini-agent` process stays alive. When the generated app is installed and launched by the daemon, `AppManager` keeps that app process resident in the background.

There are two different launch modes:

- `reachy-mini-agent agent my_app`: terminal-only text runtime
- `reachy-mini-agent web my_app`: browser UI plus `/ws/agent`, without opening a robot connection
- `python -m my_app.main`: full generated app process, including the normal `ReachyMini(...)` connection path

Use `reachy-mini-agent web` while developing profile/front/kernel behavior on a machine that does not have a daemon or robot connected. Use `python -m my_app.main` when you do want the generated app process to connect to Reachy.

For one-shot runs from the terminal:

```bash
reachy-mini-agent agent my_app --message "Hello"
```

## Creating App Projects

Use `reachy-mini-agent create` for the local AI workflow in this repository. It is the single supported generator for user app projects under `profiles/<name>/`.

## JavaScript Web Apps
Want a zero-install, cross-platform app that runs in the browser? Check out the [JavaScript SDK & Web Apps](javascript-sdk) guide — build static Hugging Face Spaces that control your robot over WebRTC from any device, including your phone.

## HTTP & WebSocket API
Building a dashboard or a non-Python controller? The Daemon exposes full control via REST.

* **Docs:** `http://localhost:8000/docs`
* **Get State:** `GET /api/state/full`
* **WebSocket:** `ws://localhost:8000/api/state/ws/full`

## AI Experimentation Tips

* **Resident app runtime:** Use `reachy-mini-agent` when you want one app project's files, profile data, memory, and prompts to drive the current runtime.
* **Conversation Demo:** Check out our earlier reference implementation combining VAD (Voice Activity Detection), LLMs, and TTS: [reachy_mini_conversation_demo](https://github.com/pollen-robotics/reachy_mini_conversation_demo).
* **Custom vision/audio pipelines:** If your AI pipeline needs direct camera or microphone access (e.g. a custom OpenCV detector, Whisper with sounddevice), you can deactivate the built-in media manager with `media_backend="no_media"`. See [Disabling Media](media-architecture.md#disabling-media--direct-hardware-access) for details.
