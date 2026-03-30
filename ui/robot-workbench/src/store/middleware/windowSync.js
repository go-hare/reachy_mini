import { extractChangedUpdates } from '../../utils/stateComparison';

const ROBOT_STATE_THROTTLE_MS = 250; // 4Hz max for robot state IPC sync

/**
 * Middleware to sync store state to other windows via Tauri events
 *
 * This middleware:
 * - Detects if current window is the main window
 * - Emits state updates to secondary windows via Tauri events
 * - Only syncs relevant state keys
 * - Uses optimized comparison functions to avoid unnecessary emissions
 * - Throttles high-frequency state (robotStateFull) to avoid IPC flooding
 *
 * @param {Function} config - Zustand store config function
 * @returns {Function} Zustand middleware
 */
export const windowSyncMiddleware = config => (set, get, api) => {
  let isMainWindow = false;
  let emitStoreUpdate = null;
  let initPromise = null;

  // Throttle state for robotStateFull
  let pendingRobotState = null;
  let robotStateThrottleTimer = null;

  const relevantKeys = [
    'darkMode',
    'isActive',
    'robotStatus',
    'busyReason',
    'isCommandRunning',
    'isAppRunning',
    'isInstalling',
    'robotStateFull',
    'activeMoves',
    'frontendLogs',
  ];

  const initWindowSync = async () => {
    if (initPromise) return initPromise;

    initPromise = (async () => {
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window');
        const { emit } = await import('@tauri-apps/api/event');
        const currentWindow = await getCurrentWindow();
        isMainWindow = currentWindow.label === 'main';

        if (isMainWindow) {
          emitStoreUpdate = async updates => {
            try {
              if (Object.keys(updates).length > 0) {
                await emit('store-update', updates);
              }
            } catch {
              // Silently fail if not in Tauri or event system not available
            }
          };
        }
      } catch {
        // Not in Tauri environment, skip sync
      }
    })();

    return initPromise;
  };

  initWindowSync();

  /**
   * Flush any pending throttled robot state to secondary windows.
   */
  const flushRobotState = () => {
    robotStateThrottleTimer = null;
    if (pendingRobotState && emitStoreUpdate) {
      emitStoreUpdate({ robotStateFull: pendingRobotState });
      pendingRobotState = null;
    }
  };

  /**
   * Process a Zustand state change and emit relevant diffs to secondary windows.
   * robotStateFull is throttled to avoid serializing a large object 20x/s.
   */
  const processStateUpdate = prevState => {
    const newState = get();
    const changedUpdates = extractChangedUpdates(prevState, newState, relevantKeys);

    if (Object.keys(changedUpdates).length === 0) return;

    // Separate robotStateFull from the rest — it needs throttling
    if ('robotStateFull' in changedUpdates) {
      pendingRobotState = changedUpdates.robotStateFull;
      delete changedUpdates.robotStateFull;

      if (!robotStateThrottleTimer) {
        robotStateThrottleTimer = setTimeout(flushRobotState, ROBOT_STATE_THROTTLE_MS);
      }
    }

    // Emit non-throttled changes immediately
    if (Object.keys(changedUpdates).length > 0) {
      emitStoreUpdate(changedUpdates);
    }
  };

  return config(
    (updates, replace) => {
      const prevState = get();
      const result = set(updates, replace);

      if (emitStoreUpdate) {
        processStateUpdate(prevState);
      } else if (initPromise) {
        initPromise
          .then(() => {
            if (emitStoreUpdate) {
              processStateUpdate(prevState);
            }
          })
          .catch(() => {
            // Not in Tauri environment or init failed — silently skip sync
          });
      }

      return result;
    },
    get,
    api
  );
};
