import { useState, useCallback, useRef, useEffect } from 'react';
import { DAEMON_CONFIG, fetchWithTimeout, buildApiUrl } from '@config/daemon';
import { useLogger } from '@utils/logging';
import { createJob } from './useAppsStore';

const CHECK_UPDATES_INTERVAL = 5 * 60 * 1000; // 5 minutes

/**
 * Hook for checking and triggering app updates.
 * Calls GET /api/apps/check-updates on mount and every 5 minutes.
 * Exposes helpers to query per-app update status and trigger updates.
 *
 * @param {boolean} isActive - Whether the robot connection is active
 * @param {Array} installedApps - Currently installed apps list
 * @param {Function} setActiveJobs - Setter for activeJobs map (from store)
 * @param {React.MutableRefObject} startJobPollingRef - Ref to startJobPolling function
 */
export function useAppUpdates(isActive, installedApps, setActiveJobs, startJobPollingRef) {
  const [updateStatuses, setUpdateStatuses] = useState(new Map());
  const [isCheckingUpdates, setIsCheckingUpdates] = useState(false);
  const [hasCheckedOnce, setHasCheckedOnce] = useState(false);
  const logger = useLogger();
  const intervalRef = useRef(null);

  /**
   * Fetch update statuses from daemon
   */
  const checkForUpdates = useCallback(
    async (force = false) => {
      if (!isActive || installedApps.length === 0) return;

      setIsCheckingUpdates(true);
      try {
        const response = await fetchWithTimeout(
          buildApiUrl('/api/apps/check-updates'),
          {},
          DAEMON_CONFIG.TIMEOUTS.APPS_LIST,
          { silent: true }
        );

        if (!response.ok) {
          throw new Error(`Check updates failed: ${response.status}`);
        }

        const data = await response.json();

        // Backend returns { apps_with_updates: [ { app_name, update_available, ... } ] }
        const statusMap = new Map();
        if (Array.isArray(data?.apps_with_updates)) {
          for (const app of data.apps_with_updates) {
            if (app.app_name) {
              statusMap.set(app.app_name, app);
            }
          }
        }

        setUpdateStatuses(statusMap);
        setHasCheckedOnce(true);
      } catch (err) {
        // Silent failure - don't disrupt the UI for update checks
        console.warn('[useAppUpdates] Failed to check for updates:', err.message);
      } finally {
        setIsCheckingUpdates(false);
      }
    },
    [isActive, installedApps.length]
  );

  /**
   * Check if a specific app has an update available
   */
  const hasUpdate = useCallback(
    appName => {
      const status = updateStatuses.get(appName);
      return status?.update_available === true;
    },
    [updateStatuses]
  );

  /**
   * Get full update status for a specific app
   */
  const getAppUpdateStatus = useCallback(
    appName => {
      return updateStatuses.get(appName) || null;
    },
    [updateStatuses]
  );

  /**
   * Trigger an update for a specific app.
   * POSTs to /api/apps/update/{app_name}, creates a job with type 'update'.
   */
  const triggerUpdate = useCallback(
    async appName => {
      try {
        const response = await fetchWithTimeout(
          buildApiUrl(`/api/apps/update/${encodeURIComponent(appName)}`),
          { method: 'POST' },
          DAEMON_CONFIG.TIMEOUTS.APP_INSTALL,
          { label: `Update ${appName}` }
        );

        if (!response.ok) {
          if (response.status === 403 || response.status === 401) {
            const permissionError = new Error(
              'Permission denied: System may have blocked the update'
            );
            permissionError.name = 'PermissionDeniedError';
            throw permissionError;
          }
          throw new Error(`Update failed: ${response.status}`);
        }

        const result = await response.json();
        const jobId = result.job_id || Object.keys(result)[0];

        if (!jobId) {
          throw new Error('No job_id returned from API');
        }

        // Create job in activeJobs (DRY: reuse shared helper)
        createJob(jobId, 'update', appName, null, setActiveJobs, startJobPollingRef);

        // Optimistically mark update as no longer available
        setUpdateStatuses(prev => {
          const updated = new Map(prev);
          const existing = updated.get(appName);
          if (existing) {
            updated.set(appName, { ...existing, update_available: false });
          }
          return updated;
        });

        return jobId;
      } catch (err) {
        console.error('[useAppUpdates] Update error:', err);
        logger.error(`Failed to start update ${appName} (${err.message})`);
        throw err;
      }
    },
    [setActiveJobs, startJobPollingRef, logger]
  );

  // Poll on mount and every 5 minutes
  useEffect(() => {
    if (!isActive || installedApps.length === 0) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    // Initial check
    checkForUpdates();

    // Periodic check
    intervalRef.current = setInterval(() => checkForUpdates(), CHECK_UPDATES_INTERVAL);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isActive, installedApps.length, checkForUpdates]);

  return {
    checkForUpdates,
    hasUpdate,
    getAppUpdateStatus,
    triggerUpdate,
    isCheckingUpdates,
    hasCheckedOnce,
  };
}
