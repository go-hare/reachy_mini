import React, { useCallback } from 'react';
import { Box } from '@mui/material';
import HardwareScanView from './HardwareScanView';
import useAppStore from '../../store/useAppStore';
import { DAEMON_CONFIG, fetchWithTimeout, buildApiUrl, getWsBaseUrl } from '../../config/daemon';

/**
 * View displayed during daemon startup
 * Wrapper around HardwareScanView that handles the transition logic
 */
function StartingView({ startupError, startDaemon }) {
  const { darkMode, transitionTo, setHardwareError } = useAppStore();

  const handleScanComplete = useCallback(async () => {
    setHardwareError(null);

    try {
      await fetchWithTimeout(
        buildApiUrl('/api/motors/set_mode/enabled'),
        { method: 'POST' },
        DAEMON_CONFIG.TIMEOUTS.COMMAND,
        { label: 'Enable motors' }
      );

      await new Promise(resolve => setTimeout(resolve, 300));

      const response = await fetchWithTimeout(
        buildApiUrl('/api/move/play/wake_up'),
        { method: 'POST' },
        DAEMON_CONFIG.TIMEOUTS.COMMAND,
        { label: 'Wake up animation' }
      );

      const moveData = await response.json();
      const moveUuid = moveData?.uuid;

      if (moveUuid) {
        await new Promise(resolve => {
          let resolved = false;
          const finish = () => {
            if (!resolved) {
              resolved = true;
              resolve();
            }
          };

          const timeout = setTimeout(finish, 10000);

          try {
            const ws = new WebSocket(`${getWsBaseUrl()}/api/move/ws/updates`);
            ws.onmessage = event => {
              try {
                const data = JSON.parse(event.data);
                if (
                  data.uuid === moveUuid &&
                  (data.type === 'move_completed' ||
                    data.type === 'move_failed' ||
                    data.type === 'move_cancelled')
                ) {
                  clearTimeout(timeout);
                  ws.close();
                  finish();
                }
              } catch {}
            };
            ws.onerror = () => {
              clearTimeout(timeout);
              ws.close();
              setTimeout(finish, 1000);
            };
            ws.onclose = () => {
              if (!resolved) setTimeout(finish, 1000);
            };
          } catch {
            clearTimeout(timeout);
            setTimeout(finish, 4000);
          }
        });
      } else {
        await new Promise(resolve => setTimeout(resolve, 4000));
      }
    } catch (err) {
      console.error('[StartingView] Auto wake-up error:', err);
    }

    transitionTo.ready();
  }, [transitionTo, setHardwareError]);

  return (
    <Box
      sx={{
        width: '100vw',
        height: '100vh',
        background: darkMode ? 'rgba(26, 26, 26, 0.95)' : 'rgba(250, 250, 252, 0.85)',
        backdropFilter: 'blur(40px)',
        WebkitBackdropFilter: 'blur(40px)',
        overflow: 'hidden',
      }}
    >
      {/* Centered content */}
      <Box
        sx={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <HardwareScanView
          startupError={startupError}
          onScanComplete={handleScanComplete}
          startDaemon={startDaemon}
        />
      </Box>
    </Box>
  );
}

export default StartingView;
