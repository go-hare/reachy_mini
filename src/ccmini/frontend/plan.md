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
- The current screen already contains donor-inspired sections such as the welcome dashboard, compact status bar, command panel, theme picker, task board, and composer.
- The main remaining mismatch is now the transcript area: the donor project renders messages as expanded, independent rows, while the current transcript still visually compresses tool progress, tool results, and informational system updates into a single shared region.

## Technical Approach

1. Keep the current ccmini bridge/session runtime intact.
2. Keep using `CcminiRepl.tsx` as the active surface and refine the existing donor-style sections instead of transplanting the donor runtime.
3. Rework the transcript area first so message blocks render as expanded rows rather than a single compact region.
4. Promote tool progress, tool results, and informational system updates into standalone rows instead of subordinate arrow blocks whenever possible.
5. Reuse live ccmini state such as tool lookup metadata, transcript state, and runtime events instead of transplanting the donor runtime.

## Verification

- Run `bunx tsc --noEmit` inside `frontend/` after the UI changes.

## Clarifying Questions

1. Should the donor runtime be transplanted wholesale into the current frontend?
Answer: No. Preserve the current ccmini runtime and borrow UI patterns only.

2. Is the target still the terminal UI package instead of a separate web UI?
Answer: Yes. This task applies to the active terminal frontend in `frontend/`.

3. When donor behavior does not map 1:1, should reasonable UI adaptations be made?
Answer: Yes. Prefer lightweight, stable adaptation over fragile full-source parity.
