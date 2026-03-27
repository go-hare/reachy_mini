import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildReachyStateWebSocketUrl,
  type ReachyConnectionState,
  type ReachyFullState,
  type RobotWorkbenchSettings,
  normalizeReachyDaemonBaseUrl,
} from '@/lib/reachy-daemon'

const RECONNECT_DELAY_MS = 2_000

export interface ReachyStatusResult {
  connectionState: ReachyConnectionState
  daemonBaseUrl: string
  snapshot: ReachyFullState | null
  error: string | null
  lastUpdatedAt: string | null
}

export function useReachyStatus(settings: RobotWorkbenchSettings): ReachyStatusResult {
  const daemonBaseUrl = useMemo(
    () => normalizeReachyDaemonBaseUrl(settings.daemon_base_url),
    [settings.daemon_base_url]
  )
  const reconnectTimerRef = useRef<number | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const [state, setState] = useState<ReachyStatusResult>({
    connectionState: settings.live_status_enabled ? 'connecting' : 'disabled',
    daemonBaseUrl,
    snapshot: null,
    error: null,
    lastUpdatedAt: null,
  })

  useEffect(() => {
    const clearSocket = () => {
      if (reconnectTimerRef.current != null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }

      if (socketRef.current) {
        socketRef.current.onopen = null
        socketRef.current.onmessage = null
        socketRef.current.onerror = null
        socketRef.current.onclose = null
        socketRef.current.close()
        socketRef.current = null
      }
    }

    if (!settings.live_status_enabled) {
      clearSocket()
      setState((prev) => ({
        ...prev,
        connectionState: 'disabled',
        daemonBaseUrl,
        error: null,
      }))
      return clearSocket
    }

    if (typeof WebSocket === 'undefined') {
      setState((prev) => ({
        ...prev,
        connectionState: 'offline',
        daemonBaseUrl,
        error: 'WebSocket unavailable in this environment',
      }))
      return clearSocket
    }

    let cancelled = false

    const connect = () => {
      if (cancelled) return

      setState((prev) => ({
        ...prev,
        connectionState: 'connecting',
        daemonBaseUrl,
        error: null,
      }))

      try {
        const socket = new WebSocket(buildReachyStateWebSocketUrl(daemonBaseUrl))
        socketRef.current = socket

        socket.onopen = () => {
          if (cancelled) return
          setState((prev) => ({
            ...prev,
            connectionState: prev.snapshot ? 'live' : 'connecting',
            daemonBaseUrl,
            error: null,
          }))
        }

        socket.onmessage = (event) => {
          if (cancelled) return

          try {
            const nextSnapshot = JSON.parse(event.data) as ReachyFullState
            setState({
              connectionState: 'live',
              daemonBaseUrl,
              snapshot: nextSnapshot,
              error: null,
              lastUpdatedAt: nextSnapshot.timestamp ?? new Date().toISOString(),
            })
          } catch {
            setState((prev) => ({
              ...prev,
              connectionState: 'offline',
              daemonBaseUrl,
              error: 'Received invalid Reachy state payload',
            }))
          }
        }

        socket.onerror = () => {
          if (cancelled) return
          setState((prev) => ({
            ...prev,
            connectionState: 'offline',
            daemonBaseUrl,
            error: prev.error ?? `Unable to reach Reachy daemon at ${daemonBaseUrl}`,
          }))
        }

        socket.onclose = () => {
          if (cancelled) return

          setState((prev) => ({
            ...prev,
            connectionState: 'offline',
            daemonBaseUrl,
            error: prev.error ?? 'Last stream disconnected',
          }))

          reconnectTimerRef.current = window.setTimeout(() => {
            reconnectTimerRef.current = null
            connect()
          }, RECONNECT_DELAY_MS)
        }
      } catch {
        setState((prev) => ({
          ...prev,
          connectionState: 'offline',
          daemonBaseUrl,
          error: `Unable to reach Reachy daemon at ${daemonBaseUrl}`,
        }))
      }
    }

    clearSocket()
    connect()

    return () => {
      cancelled = true
      clearSocket()
    }
  }, [daemonBaseUrl, settings.live_status_enabled])

  return state
}
