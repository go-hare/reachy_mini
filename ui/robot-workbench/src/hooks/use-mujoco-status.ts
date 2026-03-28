import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildDaemonStatusUrl,
  normalizeReachyDaemonBaseUrl,
  type ReachyConnectionState,
  type ReachyDaemonStatus,
  type RobotWorkbenchSettings,
} from '@/lib/reachy-daemon'

const POLL_INTERVAL_MS = 5_000

export interface MujocoStatusResult {
  connectionState: ReachyConnectionState
  daemonBaseUrl: string
  daemonStatus: ReachyDaemonStatus | null
  error: string | null
  lastUpdatedAt: string | null
}

export function useMujocoStatus(settings: RobotWorkbenchSettings): MujocoStatusResult {
  const daemonBaseUrl = useMemo(
    () => normalizeReachyDaemonBaseUrl(settings.daemon_base_url),
    [settings.daemon_base_url]
  )
  const pollTimerRef = useRef<number | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const [state, setState] = useState<MujocoStatusResult>({
    connectionState: settings.mujoco_live_status_enabled ? 'connecting' : 'disabled',
    daemonBaseUrl,
    daemonStatus: null,
    error: null,
    lastUpdatedAt: null,
  })

  useEffect(() => {
    const clearPoll = () => {
      if (pollTimerRef.current != null) {
        window.clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }

      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
        abortControllerRef.current = null
      }
    }

    if (!settings.mujoco_live_status_enabled) {
      clearPoll()
      setState((prev) => ({
        ...prev,
        connectionState: 'disabled',
        daemonBaseUrl,
        error: null,
      }))
      return clearPoll
    }

    if (typeof fetch === 'undefined') {
      setState((prev) => ({
        ...prev,
        connectionState: 'offline',
        daemonBaseUrl,
        error: 'Fetch unavailable in this environment',
      }))
      return clearPoll
    }

    let cancelled = false

    const poll = async () => {
      if (cancelled) return

      setState((prev) => {
        if (prev.connectionState === 'live' && prev.daemonBaseUrl === daemonBaseUrl) {
          return prev
        }

        return {
          ...prev,
          connectionState: 'connecting',
          daemonBaseUrl,
          error: null,
        }
      })

      const controller = new AbortController()
      abortControllerRef.current = controller

      try {
        const response = await fetch(buildDaemonStatusUrl(daemonBaseUrl), {
          headers: { accept: 'application/json' },
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error(`Daemon status request failed (${response.status})`)
        }

        const daemonStatus = (await response.json()) as ReachyDaemonStatus

        if (cancelled) return

        setState({
          connectionState: 'live',
          daemonBaseUrl,
          daemonStatus,
          error: null,
          lastUpdatedAt: new Date().toISOString(),
        })
      } catch (error) {
        if (cancelled) return
        if (error instanceof DOMException && error.name === 'AbortError') return

        const message =
          error instanceof Error ? error.message : `Unable to reach Reachy daemon at ${daemonBaseUrl}`

        setState((prev) => ({
          ...prev,
          connectionState: 'offline',
          daemonBaseUrl,
          error: message,
        }))
      } finally {
        abortControllerRef.current = null

        if (cancelled) return

        pollTimerRef.current = window.setTimeout(() => {
          pollTimerRef.current = null
          void poll()
        }, POLL_INTERVAL_MS)
      }
    }

    clearPoll()
    void poll()

    return () => {
      cancelled = true
      clearPoll()
    }
  }, [daemonBaseUrl, settings.mujoco_live_status_enabled])

  return state
}
