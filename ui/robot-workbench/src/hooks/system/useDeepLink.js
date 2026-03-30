import { useEffect, useRef } from 'react';
import { ROBOT_STATUS, BUSY_REASON } from '../../constants/robotStatus';

/**
 * Hook to handle deep links for app installation
 * URL format: reachymini://install/<app-name>
 *
 * @param {Object} options
 * @param {boolean} options.isActive - Whether the robot/daemon is active
 * @param {boolean} options.isAppRunning - Whether an app is currently running
 * @param {string} options.robotStatus - Current robot status ('sleeping', 'busy', 'ready', etc.)
 * @param {string} options.busyReason - Why robot is busy ('moving', 'command', 'installing', etc.)
 * @param {boolean} options.isInstalling - Whether an installation is in progress
 * @param {boolean} options.isStoppingApp - Whether an app is being stopped
 * @param {boolean} options.isCommandRunning - Whether a command/quick action is running
 * @param {Function} options.onInstallRequest - Callback when install is requested (appName) => void
 * @param {Function} options.showToast - Toast notification function (message, severity) => void
 */
export function useDeepLink({
  isActive,
  isAppRunning,
  robotStatus,
  busyReason,
  isInstalling,
  isStoppingApp,
  isCommandRunning,
  onInstallRequest,
  showToast,
}) {
  // Track if we've already set up the listener
  const listenerSetupRef = useRef(false);

  // Track if we've already processed initial URLs
  const initialUrlsProcessedRef = useRef(false);

  // Store latest callback refs to avoid stale closures
  const callbacksRef = useRef({
    onInstallRequest,
    showToast,
    isActive,
    isAppRunning,
    robotStatus,
    busyReason,
    isInstalling,
    isStoppingApp,
    isCommandRunning,
  });

  // Update refs when values change
  useEffect(() => {
    callbacksRef.current = {
      onInstallRequest,
      showToast,
      isActive,
      isAppRunning,
      robotStatus,
      busyReason,
      isInstalling,
      isStoppingApp,
      isCommandRunning,
    };
  }, [
    onInstallRequest,
    showToast,
    isActive,
    isAppRunning,
    robotStatus,
    busyReason,
    isInstalling,
    isStoppingApp,
    isCommandRunning,
  ]);

  useEffect(() => {
    // Only run in Tauri environment
    if (!window.__TAURI__) {
      return;
    }

    // Avoid duplicate listeners
    if (listenerSetupRef.current) {
      return;
    }

    let unlisten = null;

    const setupListener = async () => {
      try {
        // Dynamic import to avoid issues in non-Tauri environments
        const { onOpenUrl, getCurrent } = await import('@tauri-apps/plugin-deep-link');

        // First, check if app was launched with a deep link URL
        if (!initialUrlsProcessedRef.current) {
          initialUrlsProcessedRef.current = true;
          try {
            const initialUrls = await getCurrent();
            if (initialUrls && initialUrls.length > 0) {
              // Process after a short delay to ensure app is ready
              setTimeout(() => {
                handleDeepLink(initialUrls[0]);
              }, 1000);
            }
          } catch (err) {
            // No initial URLs - normal case
          }
        }

        // Then set up listener for future deep links
        unlisten = await onOpenUrl(urls => {
          if (!urls || urls.length === 0) return;
          handleDeepLink(urls[0]);
        });

        listenerSetupRef.current = true;
      } catch (err) {}
    };

    /**
     * Get a specific error message based on the current robot state
     */
    const getBusyMessage = state => {
      const {
        robotStatus,
        busyReason,
        isInstalling,
        isStoppingApp,
        isCommandRunning,
        isAppRunning,
      } = state;

      // Check specific conditions in priority order
      if (robotStatus === ROBOT_STATUS.SLEEPING) {
        return 'Robot is asleep. Wake it up first!';
      }

      if (isInstalling) {
        return 'Another app is being installed. Please wait...';
      }

      if (isStoppingApp) {
        return 'An app is stopping. Please wait a moment...';
      }

      if (isAppRunning) {
        return 'An app is currently running. Stop it first!';
      }

      if (isCommandRunning) {
        return 'A command is in progress. Please wait...';
      }

      // Check busyReason for more specific messages
      if (busyReason === BUSY_REASON.MOVING) {
        return 'Robot is moving. Please wait...';
      }

      if (busyReason === BUSY_REASON.COMMAND) {
        return 'A command is running. Please wait...';
      }

      if (busyReason === BUSY_REASON.INSTALLING) {
        return 'Installation in progress. Please wait...';
      }

      if (busyReason === BUSY_REASON.APP_RUNNING) {
        return 'An app is running. Stop it first!';
      }

      // Generic busy message as fallback
      if (robotStatus === ROBOT_STATUS.BUSY) {
        return 'Robot is busy. Please wait...';
      }

      return null; // Not busy
    };

    const handleDeepLink = url => {
      const state = callbacksRef.current;
      const { onInstallRequest, showToast, isActive } = state;

      try {
        // Parse the URL - handle both formats:
        // reachymini://install/app-name
        // reachymini://install?app=app-name
        let appName = null;

        // Try parsing as URL
        try {
          const parsed = new URL(url);
          const host = parsed.host || parsed.hostname;
          const pathname = parsed.pathname;

          // Format: reachymini://install/app-name
          if (host === 'install' && pathname && pathname !== '/') {
            appName = pathname.replace(/^\//, ''); // Remove leading slash
          }
          // Format: reachymini://install?app=app-name
          else if (host === 'install') {
            appName = parsed.searchParams.get('app');
          }
          // Format: reachymini:///install/app-name (triple slash)
          else if (pathname.startsWith('/install/')) {
            appName = pathname.replace('/install/', '');
          }
        } catch {
          // Fallback: simple string parsing for malformed URLs
          const match = url.match(/reachymini:\/\/install\/([^/?]+)/);
          if (match) {
            appName = match[1];
          }
        }

        if (!appName) {
          showToast?.('Invalid install link: no app name provided', 'error');
          return;
        }

        // Check conditions with specific messages
        if (!isActive) {
          showToast?.('Robot is not connected. Connect first!', 'warning');
          return;
        }

        // Check for busy state with specific message
        const busyMessage = getBusyMessage(state);
        if (busyMessage) {
          showToast?.(busyMessage, 'warning');
          return;
        }

        // All conditions met - trigger install
        onInstallRequest?.(appName);
      } catch (err) {
        showToast?.('Failed to process install link', 'error');
      }
    };

    setupListener();

    return () => {
      if (unlisten) {
        unlisten();
        listenerSetupRef.current = false;
      }
    };
  }, []); // Empty deps - setup once on mount
}
