import { getPlatform } from './platform';

/**
 * 🎭 Simulation Mode Utility
 *
 * Simulation mode is controlled by the user in the app interface.
 * Uses localStorage to persist the user's choice.
 */

const SIMULATION_MODE_KEY = 'simMode';
const SIMULATION_BACKEND_KEY = 'simulationBackend';

export const SIMULATION_BACKENDS = {
  MUJOCO: 'mujoco',
  MOCKUP: 'mockup',
};

/**
 * Detects if simulation mode is enabled
 * @returns {boolean} true if simulation mode is active
 */
export function isSimulationMode() {
  if (typeof window !== 'undefined') {
    return localStorage.getItem(SIMULATION_MODE_KEY) === 'true';
  }
  return false;
}

/**
 * Enables simulation mode
 */
export function enableSimulationMode() {
  if (typeof window !== 'undefined') {
    localStorage.setItem(SIMULATION_MODE_KEY, 'true');
  }
}

/**
 * Disables simulation mode
 */
export function disableSimulationMode() {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(SIMULATION_MODE_KEY);
  }
}

/**
 * Returns the default simulation backend for the current desktop platform.
 * For now, desktop Simulation maps to MuJoCo on both Windows and macOS.
 */
export function getDefaultSimulationBackend() {
  const platform = getPlatform();

  switch (platform) {
    case 'windows':
    case 'macos':
      return SIMULATION_BACKENDS.MUJOCO;
    default:
      return SIMULATION_BACKENDS.MUJOCO;
  }
}

/**
 * Returns the persisted simulation backend, or the current platform default.
 */
export function getSimulationBackend() {
  if (typeof window === 'undefined') {
    return getDefaultSimulationBackend();
  }

  const value = localStorage.getItem(SIMULATION_BACKEND_KEY);
  if (value === SIMULATION_BACKENDS.MUJOCO || value === SIMULATION_BACKENDS.MOCKUP) {
    return value;
  }

  return getDefaultSimulationBackend();
}

/**
 * Persists the selected simulation backend for retries / reconnect flows.
 */
export function setSimulationBackend(backend) {
  if (typeof window === 'undefined') return;
  if (backend !== SIMULATION_BACKENDS.MUJOCO && backend !== SIMULATION_BACKENDS.MOCKUP) return;
  localStorage.setItem(SIMULATION_BACKEND_KEY, backend);
}

/**
 * Friendly UI label for the selected simulation backend.
 */
export function getSimulationBackendLabel(backend = getSimulationBackend()) {
  switch (backend) {
    case SIMULATION_BACKENDS.MOCKUP:
      return 'Mockup';
    case SIMULATION_BACKENDS.MUJOCO:
    default:
      return 'MuJoCo';
  }
}

/**
 * CLI flag used by the selected simulation backend.
 */
export function getSimulationBackendFlag(backend = getSimulationBackend()) {
  return backend === SIMULATION_BACKENDS.MOCKUP ? '--mockup-sim' : '--sim';
}

/**
 * Simulated USB port for simulation mode
 */
export const SIMULATED_USB_PORT = '/dev/tty.usbserial-SIMULATED';
