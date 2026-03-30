import { useCallback } from 'react';
import { DAEMON_CONFIG, fetchWithTimeout, buildApiUrl, fetchExternal } from '@config/daemon';

// Website API URL - centralized app store with 24h cache
const WEBSITE_API_URL = 'https://pollen-robotics-reachy-mini.hf.space/api/apps';

/**
 * Merge website catalog apps with daemon-installed apps into a unified list.
 * Pure function, no side effects — used by both useAppsStore and HardwareScanView.
 *
 * @param {Array} websiteApps - Apps from the website API (may be empty if offline)
 * @param {Array} daemonApps - Installed apps from the local daemon
 * @returns {{ enrichedApps: Array, installedApps: Array }}
 */
export function mergeAppsData(websiteApps, daemonApps) {
  const installedAppNames = new Set(daemonApps.map(app => app.name?.toLowerCase()).filter(Boolean));
  const installedAppsMap = new Map(daemonApps.map(app => [app.name?.toLowerCase(), app]));

  // Apps installed locally but not in the website catalog
  const availableAppNames = new Set(websiteApps.map(app => app.name?.toLowerCase()));
  const localOnlyApps = daemonApps
    .filter(app => !availableAppNames.has(app.name?.toLowerCase()))
    .map(app => ({
      ...app,
      source_kind: app.source_kind || 'local',
      isOfficial: false,
    }));

  const allApps = [...websiteApps, ...localOnlyApps];

  const enrichedApps = allApps.map(app => {
    const appNameLower = app.name?.toLowerCase();
    const isInstalled = installedAppNames.has(appNameLower);
    const installedAppData = installedAppsMap.get(appNameLower);

    return {
      ...app,
      isInstalled,
      // custom_app_url is only known by the daemon (local runtime info)
      ...(isInstalled &&
        installedAppData?.extra?.custom_app_url && {
          extra: {
            ...app.extra,
            custom_app_url: installedAppData.extra.custom_app_url,
          },
        }),
    };
  });

  const installedApps = enrichedApps.filter(app => app.isInstalled);

  return { enrichedApps, installedApps };
}

/**
 * Hook for fetching apps from different sources
 * Uses the website API as primary source (cached, pre-enriched data)
 * Falls back to daemon for installed apps only
 */
export function useAppFetching() {
  /**
   * Fetch all available apps from the website API
   * This is the primary source - returns pre-enriched data with:
   * - Official/community flags
   * - Likes, downloads, runtime
   * - Full cardData (emoji, description, sdk, tags)
   *
   * @returns {Promise<Array>} Array of apps in desktop-compatible format
   */
  const fetchAppsFromWebsite = useCallback(async () => {
    try {
      const response = await fetchExternal(WEBSITE_API_URL, {}, DAEMON_CONFIG.TIMEOUTS.APPS_LIST, {
        silent: true,
      });

      if (!response.ok) {
        const error = new Error(`Website API returned ${response.status}`);
        error.name = 'NetworkError';
        throw error;
      }

      const data = await response.json();
      const apps = data.apps || [];

      console.log(
        `[Apps] Fetched ${apps.length} apps from website API (cache age: ${data.cacheAge}s)`
      );

      return apps;
    } catch (error) {
      const isNetworkError =
        error.name === 'NetworkError' ||
        error.name === 'AbortError' ||
        error.name === 'TimeoutError' ||
        error.isOffline ||
        error.message?.toLowerCase().includes('network') ||
        error.message?.toLowerCase().includes('timeout') ||
        error.message?.toLowerCase().includes('connection') ||
        error.message?.toLowerCase().includes('fetch');

      if (isNetworkError) {
        const networkError = new Error('No internet connection');
        networkError.name = 'NetworkError';
        networkError.originalError = error;
        throw networkError;
      }

      console.error('[Apps] Failed to fetch apps from website:', error.message);
      throw error;
    }
  }, []);

  /**
   * Fetch installed apps from daemon
   */
  const fetchInstalledApps = useCallback(async (retryCount = 0) => {
    const MAX_RETRIES = 3;
    const RETRY_DELAYS = [1000, 2000, 3000];

    try {
      const installedUrl = buildApiUrl('/api/apps/list-available/installed');
      const installedResponse = await fetchWithTimeout(
        installedUrl,
        {},
        DAEMON_CONFIG.TIMEOUTS.APPS_LIST,
        { silent: true }
      );

      if (installedResponse.ok) {
        const rawInstalledApps = await installedResponse.json();
        const installedApps = rawInstalledApps.map(app => ({
          ...app,
          source_kind: app.source_kind || 'local',
        }));
        return { apps: installedApps, error: null };
      }

      if (installedResponse.status >= 500) {
        if (retryCount < MAX_RETRIES) {
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[retryCount]));
          return fetchInstalledApps(retryCount + 1);
        }
        return { apps: [], error: `Server error: ${installedResponse.status}` };
      }

      return { apps: [], error: `HTTP ${installedResponse.status}` };
    } catch (err) {
      const isRetryableError =
        err.name === 'TimeoutError' ||
        err.name === 'AbortError' ||
        err.message?.includes('timeout') ||
        err.message?.includes('Load failed') ||
        err.message?.includes('Failed to fetch') ||
        err.message?.includes('network') ||
        err.message?.includes('ECONNREFUSED');

      if (retryCount < MAX_RETRIES && isRetryableError) {
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[retryCount]));
        return fetchInstalledApps(retryCount + 1);
      }

      return { apps: [], error: err.message };
    }
  }, []);

  return {
    fetchAppsFromWebsite,
    fetchInstalledApps,
  };
}
