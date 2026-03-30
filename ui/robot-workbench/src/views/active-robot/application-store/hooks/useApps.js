/**
 * ✅ REFACTORED: Simplified hook that uses centralized store
 *
 * This hook is now a thin wrapper around useAppsStore which manages
 * all app state in the global store. This ensures:
 * - Single source of truth
 * - Shared cache across all components (1 day cache)
 * - Single global fetch of locally installed apps
 *
 * @param {boolean} isActive - Whether the robot is active
 * @param {boolean} _official - Deprecated legacy parameter, ignored
 */
import { useAppsStore } from './useAppsStore';

export function useApps(isActive, _official = true) {
  // Delegate to centralized store hook
  // Note: the legacy second parameter is intentionally ignored.
  return useAppsStore(isActive);
}
