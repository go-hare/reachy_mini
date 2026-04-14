## Request Understanding

Create a new donor-style desktop/web chat frontend for `ccmini` without replacing the active terminal runtime in `frontend/`.

The user explicitly wants:

- donor-style frontend migrated into this repository
- keep the current Python `ccmini` backend
- delay non-chat product features for now
- exclude announcements, subscriptions, login, model marketplace, and admin surfaces

## Scope

This MVP will live in a new `frontend-web/` workspace and will focus on:

- donor-style shell layout
- local conversation sidebar
- chat transcript with markdown rendering
- streaming/thinking/tool event display
- basic control-request approval UI
- bridge connection through `frontend_host.py` and `/bridge/*`

Out of scope for this MVP:

- auth / account system
- billing / subscription
- projects / artifacts / admin
- donor backend compatibility
- replacing `frontend/src/screens/CcminiRepl.tsx`

## Technical Approach

1. Create a standalone Vite + React workspace under `frontend-web/`.
2. Reuse donor visual assets and theme styling where helpful.
3. Implement a new `ccmini` bridge adapter instead of donor `src/api.ts`.
4. Keep conversations in frontend local state / localStorage for now.
5. Support local Electron host startup when available, with a manual connection fallback for browser/dev use.

## Verification

- Run `npm install --ignore-scripts` inside `frontend-web/`
- Run `npm run build` inside `frontend-web/`
