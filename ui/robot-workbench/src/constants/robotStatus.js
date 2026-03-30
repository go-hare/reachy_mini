/**
 * Robot Status Constants
 *
 * Single source of truth for robot state machine states.
 * Use these constants instead of magic strings throughout the app.
 */

export const ROBOT_STATUS = {
  DISCONNECTED: 'disconnected',
  READY_TO_START: 'ready-to-start',
  STARTING: 'starting',
  SLEEPING: 'sleeping',
  READY: 'ready',
  BUSY: 'busy',
  STOPPING: 'stopping',
  CRASHED: 'crashed',
};

/**
 * Busy reasons - why the robot is in BUSY state
 */
export const BUSY_REASON = {
  MOVING: 'moving',
  COMMAND: 'command',
  APP_RUNNING: 'app-running',
  INSTALLING: 'installing',
};

// ============================================================================
// TRANSITION MAP - Exhaustive set of valid state transitions
// ============================================================================

const S = ROBOT_STATUS;

export const VALID_TRANSITIONS = {
  [S.DISCONNECTED]: [S.READY_TO_START, S.STARTING],
  [S.READY_TO_START]: [S.STARTING, S.DISCONNECTED],
  [S.STARTING]: [S.SLEEPING, S.READY, S.CRASHED, S.DISCONNECTED, S.STOPPING],
  [S.SLEEPING]: [S.READY, S.BUSY, S.STOPPING, S.CRASHED, S.DISCONNECTED],
  [S.READY]: [S.BUSY, S.SLEEPING, S.STOPPING, S.CRASHED, S.DISCONNECTED],
  [S.BUSY]: [S.READY, S.SLEEPING, S.STOPPING, S.CRASHED, S.DISCONNECTED],
  [S.STOPPING]: [S.DISCONNECTED],
  [S.CRASHED]: [S.DISCONNECTED, S.STARTING],
};

/**
 * Check whether transitioning from `from` to `to` is allowed.
 * Same-state transitions are always allowed (no-op).
 */
export function validateTransition(from, to) {
  if (from === to) return true;
  const allowed = VALID_TRANSITIONS[from];
  return Array.isArray(allowed) && allowed.includes(to);
}

/**
 * Derive the boolean flags that robotSlice keeps in sync with robotStatus.
 * This is the single place where status -> booleans mapping lives.
 */
export function buildDerivedState(status) {
  const active = status === S.SLEEPING || status === S.READY || status === S.BUSY;
  return {
    isActive: active,
    isStarting: status === S.STARTING,
    isStopping: status === S.STOPPING,
    isDaemonCrashed: status === S.CRASHED,
  };
}
