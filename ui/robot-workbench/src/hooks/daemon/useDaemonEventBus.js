/**
 * Event Bus for Daemon Lifecycle Management
 *
 * True module-level singleton shared by every consumer.
 * All events are logged for debugging purposes.
 *
 * Events:
 * - daemon:start:attempt - User initiated daemon start
 * - daemon:start:success - Daemon process started successfully
 * - daemon:start:error - Error during daemon startup
 * - daemon:start:timeout - Daemon didn't become active within timeout
 * - daemon:crash - Daemon process terminated unexpectedly
 * - daemon:hardware:error - Hardware error detected from stderr
 * - daemon:health:success - Daemon responding successfully
 * - daemon:health:failure - Daemon not responding (timeout)
 * - daemon:stop - Daemon stop initiated
 */
class DaemonEventBus {
  constructor() {
    this.listeners = new Map();
    this.eventLog = [];
    this.maxLogSize = 100;
  }

  /**
   * Emit an event to all registered listeners
   * @param {string} event - Event name
   * @param {*} data - Event data
   */
  emit(event, data = null) {
    const timestamp = Date.now();

    const logEntry = { event, data, timestamp };
    this.eventLog.push(logEntry);

    if (this.eventLog.length > this.maxLogSize) {
      this.eventLog.shift();
    }

    const handlers = this.listeners.get(event) || [];
    handlers.forEach(handler => {
      try {
        handler(data, logEntry);
      } catch {}
    });
  }

  /**
   * Register an event listener
   * @param {string} event - Event name
   * @param {Function} handler - Event handler function
   * @returns {Function} Unsubscribe function
   */
  on(event, handler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(handler);

    return () => {
      const handlers = this.listeners.get(event);
      if (handlers) {
        const index = handlers.indexOf(handler);
        if (index > -1) {
          handlers.splice(index, 1);
        }
        if (handlers.length === 0) {
          this.listeners.delete(event);
        }
      }
    };
  }
}

// Module-level singleton — every call to useDaemonEventBus() returns the same instance.
const daemonEventBus = new DaemonEventBus();

/**
 * Hook to access the daemon event bus (true singleton).
 * The returned object is referentially stable across renders and components.
 */
export const useDaemonEventBus = () => daemonEventBus;
