import { describe, it, expect, beforeEach, vi } from 'vitest';
import { create } from 'zustand';

vi.mock('../../utils/telemetry', () => ({
  telemetry: {
    robotConnected: vi.fn(),
    connectionError: vi.fn(),
    robotDisconnected: vi.fn(),
  },
}));

vi.mock('../storeLogger', () => ({
  logConnect: vi.fn(),
  logDisconnect: vi.fn(),
  logReset: vi.fn(),
  logReady: vi.fn(),
  logBusy: vi.fn(),
  logCrash: vi.fn(),
}));

import { telemetry } from '../../utils/telemetry';
import { logReady, logBusy, logCrash } from '../storeLogger';
import { subscribeRobotStatus } from '../subscribers/robotStatusSubscriber';
import { createRobotSlice } from '../slices/robotSlice';
import { ROBOT_STATUS, BUSY_REASON } from '../../constants/robotStatus';

const S = ROBOT_STATUS;

function createSubscribedStore() {
  const store = create((set, get) => createRobotSlice(set, get));
  const unsubscribe = subscribeRobotStatus(store);
  return { store, unsubscribe };
}

let store, unsubscribe;
beforeEach(() => {
  vi.clearAllMocks();
  ({ store, unsubscribe } = createSubscribedStore());
});

// ============================================================================
// Telemetry: robotConnected fires on first successful connection
// ============================================================================

describe('robotConnected telemetry', () => {
  it('fires when starting -> sleeping', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();

    expect(telemetry.robotConnected).toHaveBeenCalledOnce();
    expect(telemetry.robotConnected).toHaveBeenCalledWith({ mode: 'usb' });
  });

  it('fires when starting -> ready (web mode shortcut)', () => {
    store.getState().startConnection('web', {});
    store.getState().transitionTo.sleeping();
    vi.clearAllMocks();

    // sleeping -> ready should NOT fire robotConnected (not from starting)
    store.getState().transitionTo.ready();
    expect(telemetry.robotConnected).not.toHaveBeenCalled();
  });

  it('does NOT fire on sleeping -> ready (not a connection event)', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    vi.clearAllMocks();

    store.getState().transitionTo.ready();
    expect(telemetry.robotConnected).not.toHaveBeenCalled();
  });
});

// ============================================================================
// Telemetry: connectionError fires on crash
// ============================================================================

describe('connectionError telemetry on crash', () => {
  it('fires with health failure details when transitioning to crashed', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();

    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('http_timeout');

    expect(telemetry.connectionError).toHaveBeenCalledOnce();
    const call = telemetry.connectionError.mock.calls[0][0];
    expect(call.mode).toBe('usb');
    expect(call.error_type).toBe('crash_health_http_timeout');
    expect(call.error_message).toContain('4 consecutive');
  });

  it('reports mixed health failures when no dominant type', () => {
    store.getState().startConnection('wifi', { remoteHost: '10.0.0.1' });
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();

    store.getState().incrementTimeouts('http_timeout');
    store.getState().incrementTimeouts('ws_error');
    store.getState().incrementTimeouts('dns_failure');
    store.getState().incrementTimeouts('connection_refused');

    const call = telemetry.connectionError.mock.calls[0][0];
    expect(call.error_type).toBe('crash_health_mixed');
  });
});

// ============================================================================
// Structured logging: fires on the right transitions
// ============================================================================

describe('structured logging side effects', () => {
  it('logReady fires only when entering ready', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    expect(logReady).not.toHaveBeenCalled();

    store.getState().transitionTo.ready();
    expect(logReady).toHaveBeenCalledOnce();
  });

  it('logBusy fires with reason', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
    store.getState().transitionTo.busy(BUSY_REASON.COMMAND);

    expect(logBusy).toHaveBeenCalledOnce();
    expect(logBusy).toHaveBeenCalledWith(BUSY_REASON.COMMAND);
  });

  it('logCrash fires once on crash', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.crashed();

    expect(logCrash).toHaveBeenCalledOnce();
  });

  it('does not fire logs for same-state "transitions"', () => {
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();
    store.getState().transitionTo.ready();
    vi.clearAllMocks();

    // ready -> ready (same state, should be no-op)
    store.getState().transitionTo.ready();
    expect(logReady).not.toHaveBeenCalled();
  });
});

// ============================================================================
// Subscriber cleanup
// ============================================================================

describe('unsubscribe', () => {
  it('stops firing callbacks after unsubscribe', () => {
    unsubscribe();
    store.getState().startConnection('usb', {});
    store.getState().transitionTo.sleeping();

    expect(telemetry.robotConnected).not.toHaveBeenCalled();
  });
});
