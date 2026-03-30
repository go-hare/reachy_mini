import { useCallback, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import useAppStore from '../../store/useAppStore';
import { useShallow } from 'zustand/react/shallow';
import { useLogger } from '../../utils/logging';

// Global ref to prevent overlapping log fetches across re-renders
const isFetchingLogsRef = { current: false };

export const useLogs = () => {
  const { logs, setLogs } = useAppStore(
    useShallow(state => ({ logs: state.logs, setLogs: state.setLogs }))
  );
  const logger = useLogger();

  const fetchLogs = useCallback(async () => {
    // Skip if already fetching (prevents callback accumulation)
    if (isFetchingLogsRef.current) {
      return;
    }

    isFetchingLogsRef.current = true;

    try {
      const fetchedLogs = await invoke('get_logs');
      setLogs(fetchedLogs);
    } catch (e) {
    } finally {
      isFetchingLogsRef.current = false;
    }
  }, [setLogs]);

  // Function to add a frontend log
  const logCommand = useCallback(
    (message, type = 'info') => {
      // Timestamp is now automatically added by logger
      logger.info(message);
    },
    [logger]
  );

  // Log an API action (request to daemon)
  const logApiAction = useCallback(
    (action, details = '', success = true) => {
      // Timestamp is now automatically added by logger
      if (success) {
        logger.success(details ? `${action}: ${details}` : action);
      } else {
        logger.error(details ? `${action}: ${details}` : action);
      }
    },
    [logger]
  );

  return {
    logs,
    fetchLogs,
    logCommand,
    logApiAction,
  };
};
