# Reachy Mini Local Configuration

## Setup Status
Setup complete: PARTIAL

## User Environment
- Robot type: Unknown
- OS: macOS
- Shell: zsh
- Python env tool: conda
- Conda env: reachy
- Resources path: Not recorded yet

## Notes for Future Sessions
- Use `conda run -n reachy ...` when Python-backed commands need the project environment.
- `profiles/sim_front_app` now targets real local media again, not `no_media`.
- Local speech stack currently uses reply-audio playback plus FunASR websocket streaming for microphone transcription.
