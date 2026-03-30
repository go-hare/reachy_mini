import { describe, it, expect } from 'vitest';
import {
  ROBOT_STATUS,
  VALID_TRANSITIONS,
  validateTransition,
  buildDerivedState,
} from '../robotStatus';

const S = ROBOT_STATUS;
const ALL_STATES = Object.values(S);

// ============================================================================
// Structural integrity - catches typos and missing states in the map
// ============================================================================

describe('VALID_TRANSITIONS structural integrity', () => {
  it('has an entry for every status', () => {
    for (const status of ALL_STATES) {
      expect(VALID_TRANSITIONS).toHaveProperty(status);
    }
  });

  it('only references known statuses as targets', () => {
    for (const [from, targets] of Object.entries(VALID_TRANSITIONS)) {
      for (const to of targets) {
        expect(ALL_STATES, `unknown target "${to}" in ${from}`).toContain(to);
      }
    }
  });

  it('has no duplicate targets per source', () => {
    for (const [from, targets] of Object.entries(VALID_TRANSITIONS)) {
      expect(new Set(targets).size, `duplicates in ${from}`).toBe(targets.length);
    }
  });
});

// ============================================================================
// validateTransition - focus on the tricky/surprising cases
// ============================================================================

describe('validateTransition', () => {
  it('same-state is always allowed (no-op)', () => {
    for (const status of ALL_STATES) {
      expect(validateTransition(status, status)).toBe(true);
    }
  });

  // These are the transitions that protect against real bugs
  it('STOPPING is a dead-end: only DISCONNECTED is reachable', () => {
    const fromStopping = ALL_STATES.filter(s => s !== S.STOPPING && s !== S.DISCONNECTED);
    for (const target of fromStopping) {
      expect(validateTransition(S.STOPPING, target), `stopping -> ${target}`).toBe(false);
    }
    expect(validateTransition(S.STOPPING, S.DISCONNECTED)).toBe(true);
  });

  it('CRASHED cannot go directly to an active state (must restart)', () => {
    expect(validateTransition(S.CRASHED, S.READY)).toBe(false);
    expect(validateTransition(S.CRASHED, S.BUSY)).toBe(false);
    expect(validateTransition(S.CRASHED, S.SLEEPING)).toBe(false);
  });

  it('DISCONNECTED cannot skip to active states', () => {
    expect(validateTransition(S.DISCONNECTED, S.READY)).toBe(false);
    expect(validateTransition(S.DISCONNECTED, S.BUSY)).toBe(false);
    expect(validateTransition(S.DISCONNECTED, S.SLEEPING)).toBe(false);
    expect(validateTransition(S.DISCONNECTED, S.CRASHED)).toBe(false);
  });

  it('rejects garbage input gracefully', () => {
    expect(validateTransition('nonexistent', S.READY)).toBe(false);
    expect(validateTransition(S.READY, 'nonexistent')).toBe(false);
    expect(validateTransition(undefined, S.READY)).toBe(false);
  });
});

// ============================================================================
// buildDerivedState - verify mutual exclusivity of boolean flags
// ============================================================================

describe('buildDerivedState', () => {
  it('exactly one "category" flag is true for each status', () => {
    for (const status of ALL_STATES) {
      const d = buildDerivedState(status);
      const trueCount = [d.isActive, d.isStarting, d.isStopping, d.isDaemonCrashed].filter(
        Boolean
      ).length;

      if (status === S.DISCONNECTED || status === S.READY_TO_START) {
        expect(trueCount, `${status}: expected all false`).toBe(0);
      } else {
        expect(trueCount, `${status}: expected exactly 1 true flag`).toBe(1);
      }
    }
  });

  it('sleeping/ready/busy are all isActive (consistent definition)', () => {
    for (const status of [S.SLEEPING, S.READY, S.BUSY]) {
      expect(buildDerivedState(status).isActive, status).toBe(true);
    }
  });
});

// ============================================================================
// Full lifecycle scenarios - real user flows, not random pairs
// ============================================================================

describe('real user flows', () => {
  function assertPath(path) {
    for (let i = 0; i < path.length - 1; i++) {
      expect(
        validateTransition(path[i], path[i + 1]),
        `step ${i}: ${path[i]} -> ${path[i + 1]}`
      ).toBe(true);
    }
  }

  it('USB happy path: connect -> sleep -> wake -> use app -> shutdown', () => {
    assertPath([
      S.DISCONNECTED,
      S.STARTING,
      S.SLEEPING,
      S.READY,
      S.BUSY,
      S.READY,
      S.STOPPING,
      S.DISCONNECTED,
    ]);
  });

  it('WiFi discovery: disconnected -> ready-to-start -> starting -> sleeping', () => {
    assertPath([S.DISCONNECTED, S.READY_TO_START, S.STARTING, S.SLEEPING]);
  });

  it('crash during startup -> user retries', () => {
    assertPath([S.DISCONNECTED, S.STARTING, S.CRASHED, S.DISCONNECTED, S.STARTING, S.SLEEPING]);
  });

  it('crash while active -> user reconnects', () => {
    assertPath([S.READY, S.CRASHED, S.DISCONNECTED, S.STARTING, S.READY]);
  });

  it('crash recovery shortcut: crashed -> starting (restart without going through disconnected)', () => {
    assertPath([S.CRASHED, S.STARTING, S.SLEEPING]);
  });

  it('rapid sleep/wake cycle', () => {
    assertPath([S.SLEEPING, S.READY, S.SLEEPING, S.READY, S.SLEEPING]);
  });

  it('busy -> sleep (robot goes to sleep while moving)', () => {
    assertPath([S.BUSY, S.SLEEPING]);
  });

  it('force disconnect from any active state', () => {
    for (const active of [S.SLEEPING, S.READY, S.BUSY]) {
      expect(validateTransition(active, S.DISCONNECTED), `${active} -> disconnected`).toBe(true);
    }
  });
});
