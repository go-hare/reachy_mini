/**
 * Robot Slice - Manages robot connection, status, and state machine
 *
 * This slice handles:
 * - Connection state (USB, WiFi, Simulation)
 * - Robot status (disconnected, starting, ready, busy, stopping, crashed)
 * - Robot state polling data
 * - Visual effects
 *
 * 🎯 STATE MACHINE: robotStatus is the SINGLE SOURCE OF TRUTH
 * All boolean states (isActive, isStarting, etc.) are DERIVED from robotStatus
 */
import { logConnect, logDisconnect } from '../storeLogger';
import {
  ROBOT_STATUS,
  BUSY_REASON,
  validateTransition,
  buildDerivedState,
} from '../../constants/robotStatus';

// ============================================================================
// SELECTORS - Derive boolean states from robotStatus
// ============================================================================

/**
 * Note: sleeping with safeToShutdown=true is NOT busy (allows Settings access for shutdown)
 */
export const selectIsBusy = state => {
  return (
    (state.robotStatus === ROBOT_STATUS.SLEEPING && !state.safeToShutdown) ||
    state.robotStatus === ROBOT_STATUS.BUSY ||
    state.isCommandRunning ||
    state.isAppRunning ||
    state.isInstalling ||
    state.isStoppingApp ||
    state.isWakeSleepTransitioning
  );
};

export const selectIsReady = state =>
  state.robotStatus === ROBOT_STATUS.READY &&
  !state.isCommandRunning &&
  !state.isAppRunning &&
  !state.isInstalling &&
  !state.isStoppingApp;

// ============================================================================
// INITIAL STATE
// ============================================================================

/**
 * Initial state for robot slice
 *
 * 🎯 robotStatus is the SINGLE SOURCE OF TRUTH
 * The boolean states (isActive, isStarting, etc.) are kept in sync automatically
 * by the transitionTo functions. They exist for backwards compatibility.
 *
 * ⚠️ DO NOT use setIsActive/setIsStarting/setIsStopping - use transitionTo instead!
 */
export const robotInitialState = {
  robotStatus: ROBOT_STATUS.DISCONNECTED,

  // ✨ Reason if status === 'busy'
  // Possible values: null, 'moving', 'command', 'app-running', 'installing'
  busyReason: null,

  // 🔄 Derived states (kept in sync by transitionTo - DO NOT SET DIRECTLY)
  isActive: false, // true when robotStatus is 'ready' or 'busy'
  isStarting: false, // true when robotStatus is 'starting'
  isStopping: false, // true when robotStatus is 'stopping'
  isDaemonCrashed: false, // true when robotStatus is 'crashed'

  // 🛡️ Safety state for power off
  safeToShutdown: false, // true only when sleeping AND sleep sequence is complete
  isWakeSleepTransitioning: false, // true during wake/sleep animations

  // Daemon metadata
  daemonVersion: null,
  startupError: null,
  hardwareError: null,
  consecutiveTimeouts: 0,
  healthFailureReasons: [],
  startupTimeoutId: null,

  // Robot connection state
  isUsbConnected: false,
  usbPortName: null,
  isFirstCheck: true,

  // 🌐 Connection mode (USB vs WiFi vs Simulation)
  connectionMode: null,
  remoteHost: null,

  // 🚀 Centralized robot state (streamed by useRobotStateWebSocket at 20Hz)
  // Contains: head_pose, head_joints, body_yaw, antennas_position, passive_joints, control_mode, doa
  robotStateFull: {
    data: null,
    lastUpdate: null,
    error: null,
  },

  // 🎯 Flag to start WebSocket streaming early (during HardwareScanView)
  // Set to true when daemon is ready and movements detected, before transitioning to ActiveRobotView
  shouldStreamRobotState: false,

  // 🎯 Centralized active moves
  activeMoves: [],

  // Activity Lock
  isCommandRunning: false,
  isAppRunning: false,
  isInstalling: false,
  currentAppName: null,

  // Visual Effects (3D particles)
  activeEffect: null,
  effectTimestamp: 0,

  // 🚫 Blacklist for robots temporarily hidden (e.g., after clearing WiFi networks)
  robotBlacklist: {}, // { 'reachy-mini.local': expiryTimestamp }
};

// ============================================================================
// SLICE CREATOR
// ============================================================================

/**
 * Create robot slice
 * @param {Function} set - Zustand set function
 * @param {Function} get - Zustand get function
 * @returns {Object} Robot slice state and actions
 */
export const createRobotSlice = (set, get) => ({
  ...robotInitialState,

  // ============================================================================
  // STATE MACHINE TRANSITIONS
  // These are the ONLY way to change robot state. They keep booleans in sync.
  // ============================================================================

  /**
   * Validated state machine transition. Every transitionTo.xxx() method
   * goes through applyTransition which validates + applies common fields.
   */
  transitionTo: (() => {
    // Shared transition logic: validate, build base fields, merge extras.
    // Returns false if blocked (invalid transition or failed precondition).
    const apply = (get, set, target, extras = {}) => {
      const state = get();
      if (!validateTransition(state.robotStatus, target)) {
        console.warn(`[Store] BLOCKED: ${state.robotStatus} -> ${target}`);
        return false;
      }
      set({
        robotStatus: target,
        busyReason: null,
        safeToShutdown: false,
        isWakeSleepTransitioning: false,
        ...buildDerivedState(target),
        ...extras,
      });
      return true;
    };

    const CLEAR_LOCKS = { isCommandRunning: false, isAppRunning: false, isInstalling: false };

    const requireConnection = (state, target) => {
      if (!state.connectionMode) {
        console.warn(`[Store] BLOCKED ${target}: connectionMode is null`);
        return false;
      }
      return true;
    };

    return {
      disconnected: () => apply(get, set, ROBOT_STATUS.DISCONNECTED, CLEAR_LOCKS),
      readyToStart: () => apply(get, set, ROBOT_STATUS.READY_TO_START),
      starting: () => apply(get, set, ROBOT_STATUS.STARTING),

      sleeping: (options = {}) => {
        if (!requireConnection(get(), ROBOT_STATUS.SLEEPING)) return;
        apply(get, set, ROBOT_STATUS.SLEEPING, {
          safeToShutdown: options.safeToShutdown ?? false,
          ...CLEAR_LOCKS,
        });
      },

      ready: () => {
        const state = get();
        if (state.hardwareError) {
          console.warn('[Store] BLOCKED ready: hardware error present');
          return;
        }
        if (!requireConnection(state, ROBOT_STATUS.READY)) return;
        apply(get, set, ROBOT_STATUS.READY, CLEAR_LOCKS);
      },

      busy: reason => {
        if (!requireConnection(get(), ROBOT_STATUS.BUSY)) return;
        const locks = {};
        if (reason === BUSY_REASON.COMMAND) locks.isCommandRunning = true;
        if (reason === BUSY_REASON.APP_RUNNING) locks.isAppRunning = true;
        if (reason === BUSY_REASON.INSTALLING) locks.isInstalling = true;
        apply(get, set, ROBOT_STATUS.BUSY, { busyReason: reason, ...locks });
      },

      stopping: () => apply(get, set, ROBOT_STATUS.STOPPING, { consecutiveTimeouts: 0 }),
      crashed: () => apply(get, set, ROBOT_STATUS.CRASHED),
    };
  })(),

  // ============================================================================
  // HELPER METHODS
  // ============================================================================

  isBusy: () => selectIsBusy(get()),

  isReady: () => selectIsReady(get()),

  // Wake/Sleep transition management
  setWakeSleepTransitioning: isTransitioning => {
    set({ isWakeSleepTransitioning: isTransitioning });
  },

  getRobotStatusLabel: () => {
    const state = get();
    const { robotStatus, busyReason } = state;

    if (robotStatus === ROBOT_STATUS.BUSY && busyReason) {
      const reasonLabels = {
        [BUSY_REASON.MOVING]: 'Moving',
        [BUSY_REASON.COMMAND]: 'Executing Command',
        [BUSY_REASON.APP_RUNNING]: 'Running App',
        [BUSY_REASON.INSTALLING]: 'Installing',
      };
      return reasonLabels[busyReason] || 'Busy';
    }

    const statusLabels = {
      [ROBOT_STATUS.DISCONNECTED]: 'Disconnected',
      [ROBOT_STATUS.READY_TO_START]: 'Ready to Start',
      [ROBOT_STATUS.STARTING]: 'Starting',
      [ROBOT_STATUS.SLEEPING]: 'Sleeping',
      [ROBOT_STATUS.READY]: 'Ready',
      [ROBOT_STATUS.BUSY]: 'Busy',
      [ROBOT_STATUS.STOPPING]: 'Stopping',
      [ROBOT_STATUS.CRASHED]: 'Crashed',
    };

    return statusLabels[robotStatus] || 'Unknown';
  },

  // ============================================================================
  // APP LOCKING MANAGEMENT
  // ============================================================================

  lockForApp: appName => {
    get().transitionTo.busy(BUSY_REASON.APP_RUNNING);
    set({ currentAppName: appName });
  },

  unlockApp: () => {
    get().transitionTo.ready();
    set({ currentAppName: null });
  },

  // ============================================================================
  // SETTERS
  // ============================================================================

  setDaemonVersion: value => set({ daemonVersion: value }),
  setStartupError: value => set({ startupError: value }),
  setHardwareError: value => set({ hardwareError: value }),

  // ✅ Pure setter - NO side effects
  // USB polling only runs when !connectionMode (searching for robot)
  // Once connected, USB detection is not used (daemon health check handles disconnection)
  setIsUsbConnected: value => set({ isUsbConnected: value }),

  setUsbPortName: value => set({ usbPortName: value }),
  setIsFirstCheck: value => set({ isFirstCheck: value }),

  // 🌐 Connection mode setters
  setConnectionMode: mode => set({ connectionMode: mode }),
  setRemoteHost: host => set({ remoteHost: host }),

  isWifiMode: () => get().connectionMode === 'wifi',

  isLocalDaemon: () => {
    const mode = get().connectionMode;
    return mode === 'usb' || mode === 'simulation';
  },

  // ============================================================================
  // CONNECTION LIFECYCLE
  // ============================================================================

  resetConnection: () => {
    const prevState = get();
    logDisconnect(prevState.connectionMode);

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
      shouldStreamRobotState: false, // 🎯 Reset WebSocket streaming flag
      activeMoves: [],
      consecutiveTimeouts: 0,
      healthFailureReasons: [],
    });
  },

  startConnection: (mode, options = {}) => {
    const { portName, remoteHost } = options;
    logConnect(mode, options);

    set({
      connectionMode: mode,
      remoteHost: remoteHost || null,
      isUsbConnected: mode !== 'wifi',
      usbPortName: portName || null,
      robotStatus: ROBOT_STATUS.STARTING,
      busyReason: null,
      ...buildDerivedState(ROBOT_STATUS.STARTING),
      // Metadata
      hardwareError: null,
      startupError: null,
      consecutiveTimeouts: 0,
      healthFailureReasons: [],
      robotStateFull: { data: null, lastUpdate: null, error: null },
      shouldStreamRobotState: false, // 🎯 Reset WebSocket streaming flag
      activeMoves: [],
      daemonVersion: null,
      isCommandRunning: false,
      isAppRunning: false,
      isInstalling: false,
      currentAppName: null,
    });
  },

  // ============================================================================
  // ROBOT STATE POLLING
  // ============================================================================

  setRobotStateFull: value =>
    set(state => {
      if (typeof value === 'function') {
        return { robotStateFull: value(state.robotStateFull) };
      }
      return { robotStateFull: value };
    }),

  setActiveMoves: value => set({ activeMoves: value }),

  // 🎯 Start WebSocket streaming early (called by HardwareScanView when daemon is ready)
  setShouldStreamRobotState: value => set({ shouldStreamRobotState: value }),

  setIsCommandRunning: value => {
    const state = get();
    if (value) {
      state.transitionTo.busy(BUSY_REASON.COMMAND);
    } else if (state.busyReason === BUSY_REASON.COMMAND) {
      state.transitionTo.ready();
    }
    set({ isCommandRunning: value });
  },

  // ============================================================================
  // TIMEOUT/CRASH MANAGEMENT
  // ============================================================================

  incrementTimeouts: (failureType = 'unknown') => {
    const state = get();
    const newCount = state.consecutiveTimeouts + 1;
    const newReasons = [...state.healthFailureReasons, failureType];
    const maxTimeouts = 4;
    const shouldCrash = newCount >= maxTimeouts;

    set({ consecutiveTimeouts: newCount, healthFailureReasons: newReasons });

    if (shouldCrash && state.robotStatus !== ROBOT_STATUS.CRASHED) {
      state.transitionTo.crashed();
    }
  },

  resetTimeouts: () => {
    // Only reset consecutiveTimeouts counter.
    // isDaemonCrashed is derived from robotStatus via transitionTo - don't set it directly
    // to avoid state desync between isDaemonCrashed and robotStatus.
    const state = get();
    if (state.robotStatus === ROBOT_STATUS.CRASHED) {
      // If already crashed, don't just reset the counter - a proper reconnect
      // flow (via resetAll / startConnection) should handle the transition
      return;
    }
    set({ consecutiveTimeouts: 0, healthFailureReasons: [] });
  },

  markDaemonCrashed: () => {
    get().transitionTo.crashed();
  },

  // ============================================================================
  // STARTUP TIMEOUT MANAGEMENT
  // ============================================================================

  setStartupTimeout: timeoutId => set({ startupTimeoutId: timeoutId }),

  clearStartupTimeout: () => {
    const state = get();
    if (state.startupTimeoutId !== null) {
      clearTimeout(state.startupTimeoutId);
      set({ startupTimeoutId: null });
    }
  },

  // ============================================================================
  // VISUAL EFFECTS
  // ============================================================================

  triggerEffect: effectType => set({ activeEffect: effectType, effectTimestamp: Date.now() }),
  stopEffect: () => set({ activeEffect: null }),

  // ============================================================================
  // ROBOT BLACKLIST (temporary hiding after network operations)
  // ============================================================================

  /**
   * Add a robot to the blacklist for a specified duration
   * @param {string} host - Robot host (e.g., 'reachy-mini.local')
   * @param {number} durationMs - How long to blacklist (default 10 seconds)
   */
  blacklistRobot: (host, durationMs = 10000) => {
    const expiryTime = Date.now() + durationMs;
    set(state => ({
      robotBlacklist: {
        ...state.robotBlacklist,
        [host]: expiryTime,
      },
    }));
  },

  /**
   * Check if a robot is currently blacklisted
   * @param {string} host - Robot host to check
   * @returns {boolean} True if blacklisted and not expired
   */
  isRobotBlacklisted: host => {
    const state = get();
    const expiryTime = state.robotBlacklist[host];
    if (!expiryTime) return false;

    const now = Date.now();
    return now < expiryTime; // Still blacklisted if not expired
  },

  /**
   * Remove expired entries from blacklist
   * Called periodically by useRobotDiscovery
   */
  cleanupBlacklist: () => {
    const now = Date.now();
    set(state => {
      const cleaned = Object.entries(state.robotBlacklist)
        .filter(([_, expiryTime]) => now < expiryTime)
        .reduce((acc, [host, expiryTime]) => ({ ...acc, [host]: expiryTime }), {});

      // Only update if something changed
      if (Object.keys(cleaned).length !== Object.keys(state.robotBlacklist).length) {
        return { robotBlacklist: cleaned };
      }
      return state;
    });
  },

  /**
   * Clear all blacklisted robots
   */
  clearBlacklist: () => set({ robotBlacklist: {} }),
});
