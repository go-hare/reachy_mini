import { invoke } from '@tauri-apps/api/core'
import { useCallback, useEffect, useState } from 'react'

import type { RobotDaemonProcessStatus } from '@/lib/reachy-daemon'

const POLL_INTERVAL_MS = 2_000

const EMPTY_STATUS: RobotDaemonProcessStatus = {
  lifecycle: 'stopped',
  pid: null,
  command: 'reachy-mini-daemon --sim',
  working_dir: null,
  started_at: null,
  exit_code: null,
  last_error: null,
  recent_logs: [],
}

function normalizeStatus(status?: RobotDaemonProcessStatus | null): RobotDaemonProcessStatus {
  return {
    ...EMPTY_STATUS,
    ...(status || {}),
    lifecycle: status?.lifecycle === 'running' ? 'running' : 'stopped',
    recent_logs: Array.isArray(status?.recent_logs) ? status?.recent_logs : [],
  }
}

function toErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Desktop daemon command failed'
}

export function useRobotDaemonProcess(projectPath?: string) {
  const [status, setStatus] = useState<RobotDaemonProcessStatus>(EMPTY_STATUS)
  const [actionState, setActionState] = useState<'idle' | 'starting' | 'stopping'>('idle')
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const next = await invoke<RobotDaemonProcessStatus>('get_robot_sim_daemon_status')
    setStatus(normalizeStatus(next))
    return next
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer: number | null = null

    const poll = async () => {
      try {
        const next = await invoke<RobotDaemonProcessStatus>('get_robot_sim_daemon_status')
        if (cancelled) return
        setStatus(normalizeStatus(next))
        setError(null)
      } catch (nextError) {
        if (cancelled) return
        setError(toErrorMessage(nextError))
      } finally {
        if (cancelled) return
        timer = window.setTimeout(() => {
          timer = null
          void poll()
        }, POLL_INTERVAL_MS)
      }
    }

    void poll()

    return () => {
      cancelled = true
      if (timer != null) {
        window.clearTimeout(timer)
      }
    }
  }, [])

  const start = useCallback(async () => {
    setActionState('starting')
    setError(null)

    try {
      const next = await invoke<RobotDaemonProcessStatus>('start_robot_sim_daemon', {
        workingDir: projectPath ?? null,
      })
      setStatus(normalizeStatus(next))
      return next
    } catch (nextError) {
      const message = toErrorMessage(nextError)
      setError(message)
      throw nextError
    } finally {
      setActionState('idle')
    }
  }, [projectPath])

  const stop = useCallback(async () => {
    setActionState('stopping')
    setError(null)

    try {
      const next = await invoke<RobotDaemonProcessStatus>('stop_robot_sim_daemon')
      setStatus(normalizeStatus(next))
      return next
    } catch (nextError) {
      const message = toErrorMessage(nextError)
      setError(message)
      throw nextError
    } finally {
      setActionState('idle')
    }
  }, [])

  return {
    status,
    error,
    refresh,
    start,
    stop,
    isStarting: actionState === 'starting',
    isStopping: actionState === 'stopping',
    isBusy: actionState !== 'idle',
  }
}
