import { create } from 'zustand';
import { windowSyncMiddleware } from './middleware/windowSync';
import {
  createRobotSlice,
  createLogsSlice,
  createUISlice,
  createAppsSlice,
  setupSystemPreferenceListener,
} from './slices';
import { logReset } from './storeLogger';
import { disableSimulationMode } from '../utils/simulationMode';
import { ROBOT_STATUS, BUSY_REASON, buildDerivedState } from '../constants/robotStatus';

/**
 * ✨ Unified Store with Slices Architecture
 *
 * This store combines all domain slices into a single Zustand store:
 * - Robot: Connection, status, state machine
 * - Logs: Daemon, frontend, app logs
 * - UI: Theme, windows, panel views
 * - Apps: Application data, installation
 *
 * Benefits:
 * - Single store = no subscription sync overhead
 * - Atomic cross-slice actions (resetAll)
 * - Modular code organization
 * - Full backwards compatibility via proxy
 */
export const useStore = create(
  windowSyncMiddleware((set, get, api) => ({
    // ============================================
    // SLICES
    // ============================================
    ...createRobotSlice(set, get),
    ...createLogsSlice(set, get),
    ...createUISlice(set, get),
    ...createAppsSlice(set, get),

    // ============================================
    // CROSS-SLICE ACTIONS
    // ============================================

    /**
     * ✅ CRITICAL: Reset all state on disconnect
     * This is an atomic action that resets robot AND apps state together
     * Solving the issue where apps weren't cleared on mode switch
     */
    resetAll: () => {
      logReset('all');

      // 🧹 Clean up simulation mode flag from localStorage
      // Prevents stale simMode from persisting after crash/force-quit
      disableSimulationMode();

      set({
        robotStatus: ROBOT_STATUS.DISCONNECTED,
        busyReason: null,
        ...buildDerivedState(ROBOT_STATUS.DISCONNECTED),
        // Connection
        connectionMode: null,
        remoteHost: null,
        isUsbConnected: false,
        usbPortName: null,
        isFirstCheck: true,
        daemonVersion: null,
        robotStateFull: { data: null, lastUpdate: null, error: null },
        activeMoves: [],
        consecutiveTimeouts: 0,
        hardwareError: null,
        startupError: null,
        isCommandRunning: false,
        isAppRunning: false,
        isInstalling: false,
        currentAppName: null,
        activeEffect: null,

        // Apps state reset
        availableApps: [],
        installedApps: [],
        currentApp: null,
        activeJobs: {},
        appsLoading: false,
        appsError: null,
        appsLastFetch: null,
        appsCacheValid: false,
        installingAppName: null,
        installJobType: null,
        installResult: null,
        installStartTime: null,
        processedJobs: [],
        jobSeenOnce: false,
        isStoppingApp: false,

        // Logs reset (optional - can be preserved)
        appLogs: [],
        // frontendLogs: [], // Keep frontend logs for debugging
        // logs: [], // Keep daemon logs for debugging
      });
    },

    // ============================================
    // INSTALLATION WITH ROBOT STATE
    // ============================================

    /**
     * Lock for install with robot state transition
     * Combines appsSlice.lockForInstall with robotSlice.transitionTo.busy
     */
    lockForInstallWithRobot: (appName, jobType = 'install') => {
      const state = get();
      state.transitionTo.busy(BUSY_REASON.INSTALLING);
      state.lockForInstall(appName, jobType);
    },

    /**
     * Unlock install with robot state transition
     * Combines appsSlice.unlockInstall with robotSlice.transitionTo.ready
     */
    unlockInstallWithRobot: () => {
      const state = get();
      state.transitionTo.ready();
      state.unlockInstall();

      // Safety: if transitionTo.ready() was blocked (hardwareError, connection lost),
      // force-clear isInstalling to prevent permanent UI lockout
      if (get().isInstalling) {
        console.warn(
          '[Store] unlockInstallWithRobot: transition blocked, force-clearing install lock'
        );
        set({ isInstalling: false, busyReason: null });
      }
    },

    // ============================================
    // GENERIC UPDATE ACTION (backwards compat)
    // ============================================

    /**
     * Generic update for backwards compatibility.
     * Protected fields (robotStatus and derived booleans) are stripped
     * to prevent bypassing the state machine. Use transitionTo instead.
     */
    update: updates => {
      const PROTECTED = ['robotStatus', 'isActive', 'isStarting', 'isStopping', 'isDaemonCrashed'];
      const safe = { ...updates };
      let stripped = false;
      for (const key of PROTECTED) {
        if (key in safe) {
          delete safe[key];
          stripped = true;
        }
      }
      if (stripped) {
        console.warn(
          '[Store] update() stripped protected fields. Use transitionTo instead.',
          Object.keys(updates).filter(k => PROTECTED.includes(k))
        );
      }
      if (Object.keys(safe).length > 0) {
        set(safe);
      }
    },
  }))
);

// Side-effect subscriber for robotStatus transitions (telemetry, logging)
import { subscribeRobotStatus } from './subscribers/robotStatusSubscriber';
const _unsubscribeRobotStatus = subscribeRobotStatus(useStore);

// Setup system preference listener for dark mode
const _cleanupSystemPreference =
  typeof window !== 'undefined'
    ? setupSystemPreferenceListener(useStore.getState, useStore.setState)
    : null;

// ============================================================================
// HMR STATE PRESERVATION (dev only)
// Keeps store data intact across Vite hot-module replacement so the app
// doesn't reset to the connection screen on every code change.
// ============================================================================
if (import.meta.hot) {
  if (import.meta.hot.data?.storeState) {
    const saved = import.meta.hot.data.storeState;
    const dataOnly = {};
    for (const [key, value] of Object.entries(saved)) {
      if (typeof value !== 'function') {
        dataOnly[key] = value;
      }
    }
    useStore.setState(dataOnly);
    console.log('[HMR] Store state restored');
  }

  import.meta.hot.dispose(data => {
    data.storeState = useStore.getState();
    _unsubscribeRobotStatus?.();
    _cleanupSystemPreference?.();
  });

  import.meta.hot.accept();
}

export default useStore;
