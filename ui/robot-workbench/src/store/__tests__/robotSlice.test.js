import { describe, it, expect, beforeEach, vi } from 'vitest';
import { create } from 'zustand';

// Mock storeLogger before importing the slice
vi.mock('../storeLogger', () => ({
  logConnect: vi.fn(),
  logDisconnect: vi.fn(),
  logReset: vi.fn(),
  logReady: vi.fn(),
  logBusy: vi.fn(),
  logCrash: vi.fn(),
}));

import { createRobotSlice } from '../slices/robotSlice';
import { ROBOT_STATUS, BUSY_REASON } from '../../constants/robotStatus';

const S = ROBOT_STATUS;

function createTestStore() {
  return create((set, get) => createRobotSlice(set, get));
}

let store;
beforeEach(() => {
  store = createTestStore();
});

// ============================================================================
// Business logic guards in transitionTo
// ============================================================================

describe('transitionTo guards', () => {
  it('blocks sleeping/ready/busy when connectionMode is null', () => {
    // Force into starting state first
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    // sleeping needs connectionMode set - startConnection sets it, so this works
    expect(store.getState().robotStatus).toBe(S.SLEEPING);

    // Now reset and try without connectionMode
    const freshStore = createTestStore();
    // Manually set to starting without going through startConnection (no connectionMode)
    // We can't easily do this since transitionTo validates... let's test the guard directly
    // by verifying that from disconnected (no connectionMode) we can't go to sleeping
    freshStore.getState().transitionTo.sleeping();
    expect(freshStore.getState().robotStatus).toBe(S.DISCONNECTED);
  });

  it('blocks ready when hardwareError is set', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();

    store.getState().setHardwareError('motor_fault');
    store.getState().transitionTo.ready();

    expect(store.getState().robotStatus).toBe(S.SLEEPING);
    expect(store.getState().hardwareError).toBe('motor_fault');
  });

  it('allows ready when hardwareError is cleared', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().setHardwareError(null);
    store.getState().transitionTo.ready();
    expect(store.getState().robotStatus).toBe(S.READY);
  });
});

// ============================================================================
// State machine transition blocking
// ============================================================================

describe('invalid transitions are silently blocked', () => {
  it('disconnected -> busy is blocked', () => {
    store.getState().transitionTo.busy(BUSY_REASON.COMMAND);
    expect(store.getState().robotStatus).toBe(S.DISCONNECTED);
  });

  it('disconnected -> ready is blocked', () => {
    store.getState().transitionTo.ready();
    expect(store.getState().robotStatus).toBe(S.DISCONNECTED);
  });

  it('ready -> starting is blocked', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
    store.getState().transitionTo.starting();
    expect(store.getState().robotStatus).toBe(S.READY);
  });
});

// ============================================================================
// Busy sub-states: busyReason and lock flags
// ============================================================================

describe('busy reason and lock flags', () => {
  beforeEach(() => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
  });

  it('busy("command") sets isCommandRunning', () => {
    store.getState().transitionTo.busy(BUSY_REASON.COMMAND);
    const s = store.getState();
    expect(s.robotStatus).toBe(S.BUSY);
    expect(s.busyReason).toBe(BUSY_REASON.COMMAND);
    expect(s.isCommandRunning).toBe(true);
    expect(s.isAppRunning).toBe(false);
  });

  it('busy("app-running") sets isAppRunning', () => {
    store.getState().transitionTo.busy(BUSY_REASON.APP_RUNNING);
    expect(store.getState().isAppRunning).toBe(true);
    expect(store.getState().isCommandRunning).toBe(false);
  });

  it('busy("installing") sets isInstalling', () => {
    store.getState().transitionTo.busy(BUSY_REASON.INSTALLING);
    expect(store.getState().isInstalling).toBe(true);
  });

  it('transitioning to ready clears all lock flags', () => {
    store.getState().transitionTo.busy(BUSY_REASON.COMMAND);
    store.getState().transitionTo.ready();
    const s = store.getState();
    expect(s.isCommandRunning).toBe(false);
    expect(s.isAppRunning).toBe(false);
    expect(s.isInstalling).toBe(false);
    expect(s.busyReason).toBeNull();
  });
});

// ============================================================================
// Derived state consistency
// ============================================================================

describe('derived boolean flags', () => {
  it('starting state has isStarting=true, isActive=false', () => {
    store.getState().startConnection('usb', {});
    const s = store.getState();
    expect(s.isStarting).toBe(true);
    expect(s.isActive).toBe(false);
  });

  it('sleeping state has isActive=true', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    expect(store.getState().isActive).toBe(true);
  });

  it('crashed state has isDaemonCrashed=true, isActive=false', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.crashed();
    const s = store.getState();
    expect(s.isDaemonCrashed).toBe(true);
    expect(s.isActive).toBe(false);
  });

  it('stopping state has isStopping=true', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.stopping();
    expect(store.getState().isStopping).toBe(true);
  });
});

// ============================================================================
// Timeout -> crash auto-escalation
// ============================================================================

describe('incrementTimeouts -> auto crash', () => {
  beforeEach(() => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
  });

  it('crashes after 4 consecutive timeouts', () => {
    const { incrementTimeouts } = store.getState();
    incrementTimeouts('http_timeout');
    incrementTimeouts('http_timeout');
    incrementTimeouts('http_timeout');
    expect(store.getState().robotStatus).toBe(S.READY);

    incrementTimeouts('http_timeout');
    expect(store.getState().robotStatus).toBe(S.CRASHED);
    expect(store.getState().isDaemonCrashed).toBe(true);
    expect(store.getState().consecutiveTimeouts).toBe(4);
  });

  it('tracks failure reasons accurately', () => {
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('ws_error');
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');
    expect(store.getState().healthFailureReasons).toEqual([
      'http_timeout',
      'ws_error',
      'http_timeout',
      'http_timeout',
    ]);
  });

  it('resetTimeouts prevents crash when called in time', () => {
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');
    store.getState().resetTimeouts();
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');
    expect(store.getState().robotStatus).toBe(S.READY);
  });

  it('resetTimeouts is a no-op when already crashed', () => {
    for (let i = 0; i < 5; i++) store.getState().incrementTimeouts('x');
    expect(store.getState().robotStatus).toBe(S.CRASHED);

    store.getState().resetTimeouts();
    expect(store.getState().consecutiveTimeouts).toBe(5);
  });
});

// ============================================================================
// startConnection: atomic setup from disconnected
// ============================================================================

describe('startConnection', () => {
  it('transitions to starting and sets connectionMode atomically', () => {
    store.getState().startConnection('wifi', { remoteHost: '192.168.1.42' });
    const s = store.getState();
    expect(s.robotStatus).toBe(S.STARTING);
    expect(s.connectionMode).toBe('wifi');
    expect(s.remoteHost).toBe('192.168.1.42');
    expect(s.isStarting).toBe(true);
  });

  it('sets usbPortName for USB connections', () => {
    store.getState().startConnection('usb', { portName: '/dev/ttyACM0' });
    expect(store.getState().usbPortName).toBe('/dev/ttyACM0');
  });

  it('clears previous error state', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.crashed();
    // Simulate reconnect
    store.getState().transitionTo.disconnected();
    store.getState().startConnection('usb', {});
    const s = store.getState();
    expect(s.hardwareError).toBeNull();
    expect(s.startupError).toBeNull();
    expect(s.consecutiveTimeouts).toBe(0);
  });
});

// ============================================================================
// resetConnection: full cleanup
// ============================================================================

describe('resetConnection', () => {
  it('returns to disconnected and nullifies connection metadata', () => {
    store.getState().startConnection('wifi', { remoteHost: '10.0.0.1' });
    store.getState().transitionTo.sleeping();
    store.getState().resetConnection();

    const s = store.getState();
    expect(s.robotStatus).toBe(S.DISCONNECTED);
    expect(s.connectionMode).toBeNull();
    expect(s.remoteHost).toBeNull();
    expect(s.isActive).toBe(false);
    expect(s.consecutiveTimeouts).toBe(0);
  });
});

// ============================================================================
// setIsCommandRunning: compound action
// ============================================================================

describe('setIsCommandRunning compound action', () => {
  beforeEach(() => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
  });

  it('setting true transitions to busy("command")', () => {
    store.getState().setIsCommandRunning(true);
    expect(store.getState().robotStatus).toBe(S.BUSY);
    expect(store.getState().busyReason).toBe(BUSY_REASON.COMMAND);
  });

  it('setting false transitions back to ready (only if busyReason was "command")', () => {
    store.getState().setIsCommandRunning(true);
    store.getState().setIsCommandRunning(false);
    expect(store.getState().robotStatus).toBe(S.READY);
    expect(store.getState().isCommandRunning).toBe(false);
  });

  it('setting false does not change status if busyReason was NOT "command"', () => {
    store.getState().transitionTo.busy(BUSY_REASON.APP_RUNNING);
    store.getState().setIsCommandRunning(false);
    expect(store.getState().robotStatus).toBe(S.BUSY);
    expect(store.getState().busyReason).toBe(BUSY_REASON.APP_RUNNING);
  });
});

// ============================================================================
// lockForApp / unlockApp: compound actions
// ============================================================================

describe('lockForApp / unlockApp', () => {
  beforeEach(() => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
  });

  it('lockForApp transitions to busy and sets app name', () => {
    store.getState().lockForApp('face-tracker');
    expect(store.getState().robotStatus).toBe(S.BUSY);
    expect(store.getState().busyReason).toBe(BUSY_REASON.APP_RUNNING);
    expect(store.getState().currentAppName).toBe('face-tracker');
  });

  it('unlockApp returns to ready and clears app name', () => {
    store.getState().lockForApp('face-tracker');
    store.getState().unlockApp();
    expect(store.getState().robotStatus).toBe(S.READY);
    expect(store.getState().currentAppName).toBeNull();
  });
});
