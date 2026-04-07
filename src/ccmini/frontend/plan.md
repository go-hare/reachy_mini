## Request Understanding

Use the donor project at `C:\Users\Administrator\Downloads\doge-code-main\doge-code-main` as the reference to improve the UI of the current `frontend` workspace.

The user explicitly asked for:

- donor-inspired UI polish
- changes inside this workspace's `frontend` directory
- improvement of the active frontend, not just source extraction

## Current Findings

- The active runtime entry is `frontend/src/main.tsx`, which renders `frontend/src/screens/CcminiRepl.tsx`.
- The donor source already exists under `frontend/src/donor-ui/`, but `frontend/tsconfig.json` excludes it, so it is reference material rather than the code path that currently runs.
- Most visible UI for the current frontend is concentrated in `CcminiRepl.tsx`, which makes it the safest adaptation surface.

## Technical Approach

1. Keep the current ccmini bridge/session runtime intact.
2. Replace the startup welcome area with a donor-inspired dashboard layout.
3. Restyle command/help/theme/tool panels so the whole terminal UI has a more consistent donor-like console chrome.
4. Reuse live ccmini state such as connection status, command catalog metadata, transcript state, and inbox summaries instead of transplanting the donor runtime.

## Clarifying Questions

1. Should the donor runtime be transplanted wholesale into the current frontend?
Answer: No. Preserve the current ccmini runtime and borrow UI patterns only.

2. Is the target still the terminal UI package instead of a separate web UI?
Answer: Yes. This task applies to the active terminal frontend in `frontend/`.

3. When donor behavior does not map 1:1, should reasonable UI adaptations be made?
Answer: Yes. Prefer lightweight, stable adaptation over fragile full-source parity.
