/**
 * Zustand subscriber that reacts to robotStatus changes and fires
 * side effects (telemetry, structured logging). Keeps transitionTo
 * pure state mutations with no embedded I/O.
 */
import { telemetry } from '../../utils/telemetry';
import { logReady, logBusy, logCrash } from '../storeLogger';
import { ROBOT_STATUS } from '../../constants/robotStatus';

function classifyHealthFailures(reasons) {
  if (!reasons.length) return 'crash_health_unknown';

  const counts = {};
  for (const r of reasons) {
    counts[r] = (counts[r] || 0) + 1;
  }

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const [dominantType, dominantCount] = sorted[0];

  if (dominantCount > reasons.length / 2) {
    return `crash_health_${dominantType}`;
  }

  return 'crash_health_mixed';
}

export function subscribeRobotStatus(store) {
  let previousStatus = store.getState().robotStatus;

  return store.subscribe(state => {
    const current = state.robotStatus;
    if (current === previousStatus) return;

    const from = previousStatus;
    previousStatus = current;

    // Telemetry: first successful connection (starting -> sleeping|ready)
    if (
      from === ROBOT_STATUS.STARTING &&
      (current === ROBOT_STATUS.SLEEPING || current === ROBOT_STATUS.READY)
    ) {
      telemetry.robotConnected({ mode: state.connectionMode });
    }

    // Logging per status
    if (current === ROBOT_STATUS.READY) {
      logReady();
    }

    if (current === ROBOT_STATUS.BUSY) {
      logBusy(state.busyReason);
    }

    if (current === ROBOT_STATUS.CRASHED) {
      logCrash();
      const errorType = classifyHealthFailures(state.healthFailureReasons);
      telemetry.connectionError({
        mode: state.connectionMode,
        error_type: errorType,
        error_message: `Daemon unresponsive after ${state.consecutiveTimeouts} consecutive health check failures (${state.healthFailureReasons.join(', ')})`,
      });
    }
  });
}
