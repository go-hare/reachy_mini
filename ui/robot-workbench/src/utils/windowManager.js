/**
 * Window Manager — Dedicated Tauri windows for robot app web UIs.
 *
 * Manages the full lifecycle: create → focus → close → cleanup.
 * Each app gets at most one window; re-opening focuses the existing one.
 *
 * Cleanup is driven by Tauri events (destroyed / error) so the cache
 * stays in sync even when a window is closed via its native controls.
 */

import { WebviewWindow } from '@tauri-apps/api/webviewWindow';
import { currentMonitor, getAllWindows } from '@tauri-apps/api/window';
import { PhysicalPosition } from '@tauri-apps/api/dpi';
import { invoke } from '@tauri-apps/api/core';
import useAppStore from '../store/useAppStore';

const APP_WINDOW_PREFIX = 'app-';
const CASCADE_STEP = 20; // logical px offset per window

// Module-level cache: label → WebviewWindow reference
const windowRefs = new Map();
let cascadeIndex = 0;

async function placeWindow(win, options, offset) {
  if (options.anchor === 'bottom-right') {
    try {
      const monitor = await currentMonitor();
      const scaleFactor = monitor?.scaleFactor || (await win.scaleFactor());
      const width = Math.round((options.width ?? 900) * scaleFactor);
      const height = Math.round((options.height ?? 700) * scaleFactor);
      const workArea = monitor?.workArea;

      if (workArea) {
        const margin = Math.round((options.margin ?? 24) * scaleFactor);
        const x = workArea.position.x + workArea.size.width - width - margin;
        const y = workArea.position.y + workArea.size.height - height - margin;
        await win.setPosition(new PhysicalPosition(x, y));
        return;
      }
    } catch {
      // Fall through to cascade placement
    }
  }

  if (offset > 0) {
    try {
      const factor = await win.scaleFactor();
      const pos = await win.outerPosition();
      const px = Math.round(offset * factor);
      await win.setPosition(new PhysicalPosition(pos.x + px, pos.y + px));
    } catch {
      // Positioning is best-effort
    }
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Convert an app name to a valid Tauri window label.
 * Tauri labels must be alphanumeric / dashes / underscores only.
 */
function appNameToLabel(appName) {
  return APP_WINDOW_PREFIX + appName.replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();
}

/**
 * Resolve a window reference with layered fallbacks.
 * 1. Module cache  2. Tauri label lookup  3. Full enumeration
 */
async function resolveWindow(label) {
  const cached = windowRefs.get(label);
  if (cached) return cached;

  try {
    const win = WebviewWindow.getByLabel(label);
    if (win) {
      windowRefs.set(label, win);
      return win;
    }
  } catch {
    // Label lookup unavailable — try enumeration
  }

  try {
    const all = await getAllWindows();
    const win = all.find(w => w.label === label);
    if (win) {
      windowRefs.set(label, win);
      return win;
    }
  } catch {
    // Enumeration failed — give up
  }

  return null;
}

/**
 * Remove all traces of a window from caches and Zustand store.
 */
function purge(label) {
  windowRefs.delete(label);
  try {
    useAppStore.getState().removeOpenWindow?.(label);
  } catch {
    // Store may be unavailable after HMR
  }

  const hasAppWindows = [...windowRefs.keys()].some(l => l.startsWith(APP_WINDOW_PREFIX));
  if (!hasAppWindows) cascadeIndex = 0;
}

/**
 * Close a window with retry and dual-strategy fallback (JS → Rust).
 */
async function closeByLabel(label, maxAttempts = 2) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const win = await resolveWindow(label);
    if (!win) {
      purge(label);
      return;
    }

    try {
      await win.close();
      purge(label);
      return;
    } catch {
      // JS close failed — fall through to Rust
    }

    try {
      await invoke('close_window', { windowLabel: label });
      purge(label);
      return;
    } catch {
      // Both strategies failed — retry after short delay
    }

    if (attempt < maxAttempts) {
      await new Promise(r => setTimeout(r, 100 * attempt));
    }
  }

  // Exhausted retries — purge state regardless
  purge(label);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Open an app's web interface in a dedicated Tauri window.
 * If the window already exists it is focused instead of re-created.
 * Falls back to `null` on failure so callers can open a browser tab.
 *
 * @param {string} appName  Display name of the app
 * @param {string} url      Full URL to load (e.g. http://localhost:8042/…)
 * @param {object} [options]  Optional `{ width, height }` overrides
 * @returns {Promise<WebviewWindow|null>}
 */
export async function openAppWindow(appName, url, options = {}) {
  const label = appNameToLabel(appName);

  // Focus if already open
  const existing = await resolveWindow(label);
  if (existing) {
    try {
      await existing.setFocus();
      return existing;
    } catch {
      purge(label);
    }
  }

  const offset = cascadeIndex * CASCADE_STEP;

  try {
    const win = new WebviewWindow(label, {
      url,
      title: appName,
      width: options.width ?? 900,
      height: options.height ?? 700,
      center: options.center ?? true,
      resizable: options.resizable ?? true,
      decorations: options.decorations ?? true,
      alwaysOnTop: options.alwaysOnTop ?? false,
      transparent: options.transparent ?? false,
      skipTaskbar: options.skipTaskbar ?? false,
      shadow: options.shadow ?? true,
      focus: options.focus ?? true,
    });

    windowRefs.set(label, win);
    cascadeIndex++;

    win.once('tauri://created', async () => {
      await placeWindow(win, options, offset);

      try {
        if (typeof options.skipTaskbar === 'boolean') {
          await win.setSkipTaskbar(options.skipTaskbar);
        }
      } catch {
        // Skip-taskbar is best-effort
      }

      try {
        useAppStore.getState().addOpenWindow?.(label);
      } catch {
        // Store unavailable
      }
    });

    win.once('tauri://error', () => purge(label));
    win.once('tauri://destroyed', () => purge(label));

    return win;
  } catch {
    purge(label);
    return null;
  }
}

/**
 * Close a specific app's window by app name.
 */
export async function closeAppWindow(appName) {
  await closeByLabel(appNameToLabel(appName));
}

/**
 * Close every open app window (e.g. on robot disconnect).
 */
export async function closeAllAppWindows() {
  const labels = [...windowRefs.keys()].filter(l => l.startsWith(APP_WINDOW_PREFIX));
  await Promise.allSettled(labels.map(l => closeByLabel(l)));
}
