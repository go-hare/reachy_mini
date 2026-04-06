# Command Status

This document tracks how donor slash commands are currently routed in the
ccmini mini runtime.

## Routing Model

- Frontend-local commands are handled inside the terminal UI.
- Shared commands are passed through to the Python backend builtin/prompt runtime.
- Donor commands with no matching backend implementation stay discoverable in the
  frontend command browser, but are not executed.

## Frontend-Local Commands

- exact `/`
- exact `/commands`
- exact `/help`
- `/help <donor-command>`
- exact `/theme`
- exact `/exit`
- exact `/quit`

## Backend Commands Currently Connected

These commands are supported by the current backend runtime and are passed
through from the frontend instead of being intercepted locally:

- `/agents`
- `/brief`
- `/buddy`
- `/clear`
- `/compact`
- `/config`
- `/context`
- `/cost`
- `/doctor`
- `/feedback`
- `/files`
- `/help`
- `/hooks`
- `/keybindings`
- `/login`
- `/logout`
- `/mcp`
- `/memory`
- `/model`
- `/output-style`
- `/permissions`
- `/plan`
- `/plugin`
- `/rename`
- `/review`
- `/rewind`
- `/session`
- `/skills`
- `/stats`
- `/status`
- `/statusline`
- `/tasks`
- `/terminal-setup`
- `/theme`
- `/usage`
- `/version`
- `/voice`

Notes:

- `/help` without arguments is frontend-local, but `/help <backend-only-command>`
  is allowed to fall through to the backend.
- `/theme` without arguments is frontend-local, but `/theme <value>` is allowed
  to fall through to the backend theme command.

## Newly Added Backend Commands In This Extraction Pass

- `/mcp`
- `/hooks`
- `/tasks`
- `/session`
- `/usage`
- `/version`

## Donor Commands Still Metadata-Only

These donor commands remain browseable from the frontend command catalog, but
the backend does not execute them yet:

- `/add-dir`
- `/add-model`
- `/advisor`
- `/agents-platform`
- `/branch`
- `/bridge-kick`
- `/btw`
- `/chrome`
- `/color`
- `/commit`
- `/commit-push-pr`
- `/copy`
- `/desktop`
- `/diff`
- `/effort`
- `/export`
- `/extra-usage`
- `/fuck`
- `/heapdump`
- `/ide`
- `/init-verifiers`
- `/install`
- `/install-github-app`
- `/install-slack-app`
- `/mobile`
- `/pr-comments`
- `/privacy-settings`
- `/project_areas`
- `/rate-limit-options`
- `/release-notes`
- `/reload-plugins`
- `/remote-control`
- `/remote-env`
- `/remove-model`
- `/resume`
- `/security-review`
- `/stickers`
- `/tag`
- `/think-back`
- `/thinkback-play`
- `/ultraplan`
- `/upgrade`
- `/vim`
- `/web-setup`

## Notes

- The backend builtin slash commands live in `commands/builtin.py`.
- The frontend slash-command routing lives in `frontend/src/screens/CcminiRepl.tsx`.
- Donor command discovery metadata is extracted from `frontend/src/donor-ui/commands/`
  via `frontend/src/ccmini/donorCommandCatalog.ts`.
- The current runtime does not appear to attach a live MCP manager yet, so
  `/mcp tools` depends on future runtime wiring to show connected tool state.
