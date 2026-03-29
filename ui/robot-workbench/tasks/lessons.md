# Lessons

## 2026-03-29

- When the user points to a concrete desktop reference and says “I want this kind,” treat it as a request for the overall visual language and surface hierarchy, not just spacing or panel-order tweaks.
- When a floating camera or preview card belongs to the 3D stage, do not reserve a matching column in the header below it; keep the overlay attached to the viewport and let the title/header block take the full row width.
- When the user says “look at their layout, don’t guess,” stop locally optimizing one panel at a time and instead match the reference’s full-screen composition: viewport height, controller density, and per-column overflow behavior together.
- When the reference shell is column-based, do not wrap each column in another oversized rounded card; copy the column padding, split, and overflow behavior first, then refine the inner controls.
- When the user asks for adaptive desktop behavior, stop carrying over fixed reference pixel widths literally; keep the reference structure, but convert shell columns to responsive `minmax(..., 1fr)` behavior so wide windows do not leave dead gutters.
- When the user asks for the top controls to be “smaller,” keep the startup actions in place and compact the toolbar sizing; do not interpret that as a request to remove the controls.
- When the user marks a specific block and points to an empty target area, treat that as a request to relocate the whole block into that target region, not just restyle it in place.
- When the user says a control block should move "up a bit" inside a column, prefer attaching it to the main scroll/content stack instead of leaving it in a separately padded footer region.
- When a stage log panel starts collapsing into a shallow strip, stop relying on leftover flex height and switch it to a stable compact console size like the desktop reference.
- When a reference camera preview is supposed to be live, do not stop at matching the black placeholder box; wire the actual stream transport into the overlay.

## 2026-03-27

- When the user says a previously discussed desktop surface no longer exists, stop extending the partial integration and remove the concept end-to-end from settings, backend commands, and tests instead of only hiding the UI.
- When the user says the current agent execution chain will be replaced, stop polishing status/monitor surfaces around it and trim the shell back to reusable desktop scaffolding.
- When the user asks to remove desktop hotkeys, treat it as a full-surface cleanup: menu accelerators, Tauri global registrations, frontend shortcut listeners, and shortcut documentation should all be removed together.
- When the user asks for a visible header change on the project page, verify the current screenshot state first and do not assume the Welcome screen needs the same treatment.
- When a Tauri desktop change appears missing, verify the running process tree and dev-server origin before touching UI logic again; stale Vite/Tauri processes from sibling apps can mask the real result.

## 2026-03-09

- Avoid wrapping footer UI controls in boxed container treatments when polishing existing Commander surfaces.
- Do not introduce gradient backgrounds for routine product UI controls unless the user explicitly asks for that direction.
- Prefer staying closer to the existing flat toolbar language for chat controls and settings surfaces.
- When a Composer freeze is reported, inspect autocomplete and backend-bound input handlers before spending time on visual tweaks; repeated file scans on every keystroke can mimic a UI lockup.
- When streaming chat updates feel frozen around large HTML or form-heavy logs, check for whole-list rerenders first; a small loader/status change can repeatedly re-run expensive content normalization for every historical row.
- Persisted per-project chat history must be safe across upgrades; when heavy assistant content can block first paint, render a lightweight fallback first and upgrade to rich formatting after idle instead of asking users to clear history.
- Do not report a sidebar or settings interaction as shipped until the actual user-facing component has been updated and verified; docs and tests alone are not enough.
- Core row actions in the sidebar should not depend on hover to become discoverable; keep primary affordances visible and avoid custom open-state logic that fights the headless menu primitive.
- If a row already has a strong primary click target, avoid adding a second redundant expand affordance next to it; let the row own expansion when that is the cleaner interaction.
- Sidebar "open with" targets must be availability-aware; keep unavailable apps visible for context if needed, but never make them clickable unless the backend has confirmed the app exists on disk.
- When a sidebar primitive already owns the selected state, do not stack a second custom active card treatment on top; it creates boxed artifacts and visual noise.
- If a menu enumerates named tools or editors, use recognizable per-tool icons instead of repeating a generic glyph; otherwise the menu reads as placeholder UI.
- Do not use breadcrumb styling for the active project context when there is no real path navigation action; a project identity bar with path and branch/worktree context is clearer.
- Destructive confirmations should not present immutable values as read-only inputs; show the target as text, add a copy affordance if typing is required, and make the warning explicit.
- When a destructive action completes successfully, surface a confirmation toast; silent success is too easy to miss after a dialog closes.
- Project-scoped live execution state must be keyed by the full git context, not only by whichever project prop is currently mounted; reusing a chat view across project switches can leak running indicators to the wrong row.
- If the sidebar exposes branches and worktrees, they must be actionable and update the active project context end-to-end; decorative git refs create stale chat/status state and a broken mental model.
- If a branch already has a dedicated worktree, treat the branch row as navigation into that worktree instead of calling `git checkout`; otherwise the user hits avoidable “branch already checked out” failures.
- Mixed nested branch and worktree rows need explicit grouping and stable spacing; without section labels the sidebar feels like it is collapsing into a single dense block during navigation.
- Sidebar navigation should not refresh the recents list on ordinary project, branch, or worktree selection; MRU refresh belongs only to real list mutations or the list will jump and reorder while the user is navigating.
- Do not assign scrolling to an inner sidebar group with a hard `max-height`; the main sidebar content region must own overflow or the UI will show a dead gap above the footer and a misleading scrollbar.
- Auto-hidden sidebar scrollbars should respond only to actual scrolling state, not hover or focus; otherwise basic pointer movement creates noisy chrome.
- When a D3 chart reads theme or palette CSS variables inside an effect, apply those variables in a layout effect at the provider level; passive effect ordering can leave the first redraw one change behind.
- On Tauri/WebKit sidebars, keep the scrollbar primitive styled even when idle and hide it via a transparent thumb; collapsing the scrollbar width to zero can fall back to a bright native overlay thumb, especially in dark mode.
- Public theme and palette labels should stay concise; keep implementation-specific inspirations in docs or internal keys, not repeated in every user-facing option name.
- Repeated scrollbar regressions usually mean styles are fragmented across CSS hacks and shared primitives; centralize scrollbar theming into one utility and apply it to sidebar, chat, and menu/select surfaces together.
- Sidebar rows should stay focused on navigation; when a project action needs creation or destructive flows, anchor it in the active project header instead of duplicating menus in the sidebar list.
- When the user explicitly points project actions back to the sidebar, do not leave a competing header menu in place; one clear ownership point beats duplicated controls.
- When both sidebar and header dots are needed, split responsibilities explicitly instead of removing one: sidebar for branch/worktree/delete, header for launch/open actions.
