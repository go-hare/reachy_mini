## Request Understanding

Extract the UI elements and command source from `/Users/apple/Downloads/doge-code-main` into this `frontend` workspace.

The user explicitly asked for:

- only UI and commands
- extraction into `frontend`
- no invented rewrites or custom redesign

## Current Findings

- `frontend/src/donor-ui/components/` already matches the donor project's `src/components/`.
- `frontend` does not currently contain the donor project's `src/commands/`.
- `frontend/tsconfig.json` excludes `src/donor-ui/**/*`, so raw donor extraction can live there without affecting the current ccmini build.

## Technical Approach

1. Keep existing donor UI files as-is.
2. Copy donor command files from `src/commands/` into `frontend/src/donor-ui/commands/`.
3. Copy the donor command registry file `src/commands.ts` into `frontend/src/donor-ui/commands.ts`.
4. Only add minimal command-adjacent donor files if they are directly part of the command extraction surface.
5. Do not wire these donor commands into the current ccmini runtime unless separately requested.

## Clarifying Questions

1. Should donor commands be wired into the current runtime?
Answer: No assumption for now. Keep them as extracted source only.

2. Should any donor business logic outside UI and commands be copied?
Answer: No. Limit extraction to UI/command surface unless a command file directly requires a tiny companion shell file.

3. Where should extracted donor files live inside `frontend`?
Answer: Under `src/donor-ui/` to match the existing donor extraction layout and avoid mixing with ccmini host code.
