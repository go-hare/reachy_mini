import { useEffect, useRef } from 'react';
import useAppStore from '../../store/useAppStore';
import { getWsBaseUrl, buildApiUrl, fetchWithTimeout, DAEMON_CONFIG } from '../../config/daemon';

/**
 * 🎯 Real-time hook for active moves via WebSocket
 *
 * Responsibilities:
 * - Connect to /api/move/ws/updates WebSocket
 * - Receive real-time updates when moves start/stop
 * - Update activeMoves in store
 *
 * Replaces the old polling of GET /api/move/running every 500ms
 *
 * Benefits:
 * - ⚡ Real-time updates (no 500ms lag)
 * - 🚀 Less network overhead
 * - 🎯 Instant notification when moves complete
 */
export function useActiveMoves(isActive) {
  const { setActiveMoves, isDaemonCrashed } = useAppStore();
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const isMountedRef = useRef(true);
  const reconnectAttemptsRef = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 5;

  useEffect(() => {
    isMountedRef.current = true;

    return () => {
      isMountedRef.current = false;

      // Cleanup WebSocket
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      // Clear reconnect timeout
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    // Don't connect if not active or daemon crashed
    if (!isActive || isDaemonCrashed) {
      // Cleanup existing connection
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      // Clear active moves when not active
      if (!isActive) {
        setActiveMoves([]);
      }

      return;
    }

    // Fetch initial list of active moves via HTTP
    const fetchInitialMoves = async () => {
      try {
        const response = await fetchWithTimeout(
          buildApiUrl('/api/move/running'),
          {},
          DAEMON_CONFIG.TIMEOUTS.COMMAND,
          { silent: true }
        );

        if (response.ok && isMountedRef.current) {
          const data = await response.json();
          if (Array.isArray(data)) {
            setActiveMoves(data);
          }
        }
      } catch (err) {
        // Ignore errors on initial fetch (WebSocket will handle updates)
      }
    };

    const connectWebSocket = () => {
      // Check max reconnection attempts
      if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        return;
      }

      try {
        const wsUrl = `${getWsBaseUrl()}/api/move/ws/updates`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          reconnectAttemptsRef.current = 0; // Reset on successful connection

          // Fetch initial list of active moves via HTTP
          // (WebSocket only sends updates, not initial state)
          fetchInitialMoves();
        };

        ws.onmessage = event => {
          if (!isMountedRef.current) return;

          try {
            const data = JSON.parse(event.data);

            // The WebSocket sends updates with the following structure:
            // { "type": "move_started", "uuid": "...", "details": "" }
            // { "type": "move_completed", "uuid": "...", "details": "" }
            // { "type": "move_failed", "uuid": "...", "details": "..." }
            // { "type": "move_cancelled", "uuid": "...", "details": "" }

            if (data.type === 'move_started') {
              // Add new move
              setActiveMoves(prev => {
                const exists = prev.some(m => m.uuid === data.uuid);
                if (exists) return prev;
                return [...prev, { uuid: data.uuid }];
              });
            } else if (
              data.type === 'move_completed' ||
              data.type === 'move_failed' ||
              data.type === 'move_cancelled'
            ) {
              // Remove completed/failed/cancelled move
              setActiveMoves(prev => prev.filter(m => m.uuid !== data.uuid));
            }
          } catch (err) {}
        };

        ws.onerror = () => {};

        ws.onclose = () => {
          if (!isMountedRef.current) return;

          wsRef.current = null;

          // Attempt to reconnect if still active
          if (isActive && !isDaemonCrashed) {
            reconnectAttemptsRef.current += 1;
            const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 10000);

            reconnectTimeoutRef.current = setTimeout(() => {
              if (isMountedRef.current && isActive && !isDaemonCrashed) {
                connectWebSocket();
              }
            }, delay);
          }
        };

        wsRef.current = ws;
      } catch (err) {}
    };

    // Connect to WebSocket
    connectWebSocket();

    return () => {
      // Cleanup on unmount or when isActive changes
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [isActive, isDaemonCrashed, setActiveMoves]);
}
