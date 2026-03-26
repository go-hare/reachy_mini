# Integrations & Apps

Reachy Mini supports both direct SDK integrations and a shared resident runtime for app projects.

## App Projects and Resident Runtime

The main local AI workflow in this repository is now based on user-created app projects under `profiles/<name>/` plus the shared `reachy-mini-agent` runtime.

Create a new app project:

```bash
reachy-mini-agent create my_app
```

Run the shared resident runtime:

```bash
reachy-mini-agent agent my_app
```

Each user-created app project lives under `profiles/<name>/`. The shared runtime loads the inner `profiles/` bundle from that project and uses it as content, config, prompts, tools, memory, and session state. The app project does not ship a custom runtime.

A shared-runtime app project contains:

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

Important: this is a shared process-resident runtime, not a separate OS service yet. The kernel stays alive for as long as the `reachy-mini-agent` process stays alive.

For one-shot runs from the terminal:

```bash
reachy-mini-agent agent my_app --message "Hello"
```

## Building Python Apps

If you want a traditional Python app package for Reachy Mini, generate one with `reachy-mini-app-assistant create`. For a shared-runtime app project, use `reachy-mini-agent create`.

## JavaScript Web Apps
Want a zero-install, cross-platform app that runs in the browser? Check out the [JavaScript SDK & Web Apps](javascript-sdk) guide — build static Hugging Face Spaces that control your robot over WebRTC from any device, including your phone.

## HTTP & WebSocket API
Building a dashboard or a non-Python controller? The Daemon exposes full control via REST.

* **Docs:** `http://localhost:8000/docs`
* **Get State:** `GET /api/state/full`
* **WebSocket:** `ws://localhost:8000/api/state/ws/full`

## AI Experimentation Tips

* **Resident app runtime:** Use `reachy-mini-agent` when you want a shared companion runtime driven by one app project's files, profile bundle, memory, and prompts.
* **Conversation Demo:** Check out our earlier reference implementation combining VAD (Voice Activity Detection), LLMs, and TTS: [reachy_mini_conversation_demo](https://github.com/pollen-robotics/reachy_mini_conversation_demo).
* **Custom vision/audio pipelines:** If your AI pipeline needs direct camera or microphone access (e.g. a custom OpenCV detector, Whisper with sounddevice), you can deactivate the built-in media manager with `media_backend="no_media"`. See [Disabling Media](media-architecture.md#disabling-media--direct-hardware-access) for details.
