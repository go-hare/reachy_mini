## Request Understanding

Use the donor project at `C:\Users\Administrator\Downloads\doge-code-main\doge-code-main` as the reference to improve the UI of the current `frontend` workspace.

The user explicitly asked for:

- donor-inspired UI polish
- changes inside this workspace's `frontend` directory
- improvement of the active frontend, not just source extraction
- fill the remaining active-UI gaps instead of polishing only isolated sections
- keep the current `ccmini` runtime and session bridge intact while making the running UI feel much closer to the donor REPL

## Current Findings

- The active runtime entry is `frontend/src/main.tsx`, which renders `frontend/src/screens/CcminiRepl.tsx`.
- The donor source already exists under `frontend/src/donor-ui/`, but `frontend/tsconfig.json` excludes it, so it is reference material rather than the code path that currently runs.
- Most visible UI for the current frontend is concentrated in `CcminiRepl.tsx`, which makes it the safest adaptation surface.
- The current screen already contains donor-inspired sections such as the welcome dashboard, command panel, theme picker, simplified transcript, pending tool editors, buddy companion, and composer.
- The donor project still has major active surfaces that are not yet represented in the running UI:
  - richer transcript rows and message chrome
  - status notices and runtime summaries
  - task / background-work visibility
  - a more capable prompt input footer and contextual hints
  - a live welcome/dashboard header instead of static placeholders
- The active frontend already ships useful runtime data that was not yet being surfaced:
  - task data from `frontend/src/ccmini/tasksStore.ts`
  - prompt suggestion and speculation state already tracked in `CcminiRepl.tsx`
  - pending tool requests and inbox summaries from the bridge layer

## Technical Approach

1. Keep the current ccmini bridge/session runtime intact.
2. Keep using `CcminiRepl.tsx` as the active surface and refine the existing donor-style sections instead of transplanting the donor runtime wholesale.
3. Surface live runtime state that already exists in the active frontend:
   - task board and background work summaries
   - prompt speculation / suggestion state
   - pending tool state
   - runtime connection context
4. Rework the transcript area so messages render as more structured donor-style rows with clearer separation between:
   - user prompts
   - assistant text
   - tool use
   - tool progress
   - tool results
   - system / warning / error notices
5. Upgrade the composer so it behaves more like a donor control surface instead of a plain single-line input shell.
6. Refresh the welcome/dashboard panel so it reflects real session data instead of hard-coded placeholders.

## Verification

- Run `bunx tsc --noEmit` inside `frontend/` after the UI changes.

## Clarifying Questions

1. Should the donor runtime be transplanted wholesale into the current frontend?
Answer: No. Preserve the current ccmini runtime and borrow UI patterns only.

2. Is the target still the terminal UI package instead of a separate web UI?
Answer: Yes. This task applies to the active terminal frontend in `frontend/`.

3. When donor behavior does not map 1:1, should reasonable UI adaptations be made?
Answer: Yes. Prefer lightweight, stable adaptation over fragile full-source parity.

4. Should the remaining donor-inspired gaps all be addressed instead of only the transcript?
Answer: Yes. Implement the missing active UI layers across transcript, status/task surfaces, composer, and welcome/dashboard while preserving the current runtime.
