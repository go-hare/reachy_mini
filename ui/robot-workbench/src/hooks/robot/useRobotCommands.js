import { useCallback } from 'react';
import useAppStore from '../../store/useAppStore';
import { DAEMON_CONFIG, fetchWithTimeout, buildApiUrl } from '../../config/daemon';

export const useRobotCommands = () => {
  const { isActive, isCommandRunning, robotStatus, isAppRunning, isInstalling } = useAppStore();

  const sendCommand = useCallback(
    async (
      endpoint,
      label,
      lockDuration = DAEMON_CONFIG.MOVEMENT.COMMAND_LOCK_DURATION,
      silent = false
    ) => {
      if (!isActive) {
        return;
      }

      // ✅ Check global lock (quick action OR app running)
      // Calculate isBusy directly from state (functions are not synced in secondary windows)
      const state = useAppStore.getState();
      const isBusy =
        state.robotStatus === 'busy' ||
        state.isCommandRunning ||
        state.isAppRunning ||
        state.isInstalling;

      if (isBusy) {
        return;
      }

      // Use getState() to access setIsCommandRunning (works in all windows)
      const store = useAppStore.getState();
      if (store.setIsCommandRunning && typeof store.setIsCommandRunning === 'function') {
        store.setIsCommandRunning(true);
      } else {
        // Fallback: use setState directly
        useAppStore.setState({ isCommandRunning: true });
      }

      // Fire and forget avec logging automatique via fetchWithTimeout
      // Note: fetchWithTimeout will automatically log success/error via logSuccess/logError
      fetchWithTimeout(
        buildApiUrl(endpoint),
        { method: 'POST' },
        DAEMON_CONFIG.TIMEOUTS.COMMAND,
        { label, silent } // ⚡ Label will be used in automatic log if not silent
      )
        .catch(() => {})
        .finally(() => {
          // Unlock commands after lock duration
          setTimeout(() => {
            const store = useAppStore.getState();
            if (store.setIsCommandRunning && typeof store.setIsCommandRunning === 'function') {
              store.setIsCommandRunning(false);
            } else {
              // Fallback: use setState directly
              useAppStore.setState({ isCommandRunning: false });
            }
          }, lockDuration);
        });
    },
    [isActive, isCommandRunning]
  );

  const playRecordedMove = useCallback(
    async (dataset, move) => {
      if (!isActive) return;
      // Choreographies and emotions are longer, lock for 5 seconds
      // Silent: true to avoid logging the technical name (e.g. "fear1")
      // The log with emoji is already done in ExpressionsSection.jsx
      await sendCommand(
        `/api/move/play/recorded-move-dataset/${dataset}/${move}`,
        move,
        DAEMON_CONFIG.MOVEMENT.RECORDED_MOVE_LOCK_DURATION,
        true
      );
    },
    [isActive, sendCommand]
  );

  return {
    sendCommand,
    playRecordedMove,
    isCommandRunning,
  };
};
