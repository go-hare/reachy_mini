/**
 * Fast comparison functions for Zustand state updates
 *
 * These functions are optimized to replace JSON.stringify for frequent comparisons.
 * They provide much better performance for state synchronization.
 */

/**
 * Compare two numeric arrays (Float64Array or regular arrays) by value.
 * Returns true if all elements are identical.
 */
function arraysEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return a === b;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * Compare robotStateFull objects efficiently.
 * Ignores metadata fields (lastUpdate, dataVersion, timestamp) that change on every
 * WebSocket message; only compares actual robot data by value.
 *
 * Structure: { data: { control_mode, head_pose, head_joints, body_yaw,
 *              antennas_position, passive_joints, doa, timestamp, dataVersion }, ... }
 *
 * @param {object} prev - Previous state
 * @param {object} next - Next state
 * @returns {boolean} True if semantically equal
 */
export function compareRobotStateFull(prev, next) {
  if (prev === next) return true;
  if (!prev || !next) return prev === next;

  if (prev.error !== next.error) return false;

  if (!prev.data && !next.data) return true;
  if (!prev.data || !next.data) return false;

  const a = prev.data;
  const b = next.data;

  if (a.control_mode !== b.control_mode) return false;
  if (a.body_yaw !== b.body_yaw) return false;

  if (!arraysEqual(a.head_pose, b.head_pose)) return false;
  if (!arraysEqual(a.head_joints, b.head_joints)) return false;
  if (!arraysEqual(a.antennas_position, b.antennas_position)) return false;
  if (!arraysEqual(a.passive_joints, b.passive_joints)) return false;

  const prevDoa = a.doa;
  const nextDoa = b.doa;
  if (prevDoa !== nextDoa) {
    if (!prevDoa || !nextDoa) return false;
    if (prevDoa.angle !== nextDoa.angle) return false;
    if (prevDoa.speech_detected !== nextDoa.speech_detected) return false;
  }

  return true;
}

/**
 * Compare arrays of strings (activeMoves: string[])
 * Much faster than JSON.stringify for simple string arrays
 *
 * @param {Array<string>} prev - Previous array
 * @param {Array<string>} next - Next array
 * @returns {boolean} True if equal
 */
export function compareStringArray(prev, next) {
  if (prev === next) return true;
  if (!prev || !next) return prev === next;
  if (prev.length !== next.length) return false;

  for (let i = 0; i < prev.length; i++) {
    if (prev[i] !== next[i]) return false;
  }
  return true;
}

/**
 * Compare frontendLogs arrays
 * Structure: Array<{ timestamp: string, message: string, source: string }>
 *
 * @param {Array} prev - Previous logs array
 * @param {Array} next - Next logs array
 * @returns {boolean} True if equal
 */
export function compareFrontendLogs(prev, next) {
  if (prev === next) return true;
  if (!prev || !next) return prev === next;
  if (prev.length !== next.length) return false;

  if (prev.length > 0 && next.length > 0) {
    const lastPrev = prev[prev.length - 1];
    const lastNext = next[next.length - 1];
    if (
      lastPrev.timestamp !== lastNext.timestamp ||
      lastPrev.message !== lastNext.message ||
      lastPrev.source !== lastNext.source
    ) {
      return false;
    }
  }

  return true;
}

/**
 * Deep equality comparison for objects
 * Used for other object types that need deep comparison
 *
 * @param {any} a - First value
 * @param {any} b - Second value
 * @returns {boolean} True if equal
 */
export function deepEqual(a, b) {
  if (a === b) return true;
  if (a == null || b == null) return false;
  if (typeof a !== 'object' || typeof b !== 'object') return false;

  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((val, idx) => deepEqual(val, b[idx]));
  }

  if (Array.isArray(a) || Array.isArray(b)) return false;

  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;

  return keysA.every(key => deepEqual(a[key], b[key]));
}

/**
 * Compare state values and extract changed keys
 * Uses fast comparison functions instead of JSON.stringify
 *
 * @param {object} prevState - Previous state
 * @param {object} newState - New state
 * @param {Array<string>} relevantKeys - Keys to compare
 * @returns {object} Object with only changed keys
 */
export function extractChangedUpdates(prevState, newState, relevantKeys) {
  const changedUpdates = {};

  if (!prevState || !newState) {
    return changedUpdates;
  }

  relevantKeys.forEach(key => {
    const prevValue = prevState[key];
    const newValue = newState[key];

    if (prevValue === newValue) return;

    if (key === 'robotStateFull') {
      if (!compareRobotStateFull(prevValue, newValue)) {
        changedUpdates[key] = newValue;
      }
    } else if (key === 'activeMoves') {
      if (!compareStringArray(prevValue, newValue)) {
        changedUpdates[key] = newValue;
      }
    } else if (key === 'frontendLogs') {
      if (!compareFrontendLogs(prevValue, newValue)) {
        changedUpdates[key] = newValue;
      }
    } else if (
      typeof prevValue === 'object' &&
      typeof newValue === 'object' &&
      prevValue !== null &&
      newValue !== null
    ) {
      if (!deepEqual(prevValue, newValue)) {
        changedUpdates[key] = newValue;
      }
    } else {
      if (prevValue !== newValue) {
        changedUpdates[key] = newValue;
      }
    }
  });

  return changedUpdates;
}
