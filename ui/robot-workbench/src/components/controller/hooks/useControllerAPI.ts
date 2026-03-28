import { useCallback, useEffect, useRef, useState } from "react"
import {
  buildControllerMoveWebSocketUrl,
  buildControllerSetTargetUrl,
  normalizeReachyDaemonBaseUrl,
  type ReachyXYZRPYPose,
} from "@/lib/reachy-daemon"

const SEND_THROTTLE_MS = 50
const WS_RECONNECT_DELAY_MS = 1_000
const WS_MAX_RECONNECT_ATTEMPTS = 5

export type ControllerTransportState = "disabled" | "connecting" | "websocket" | "http" | "offline"

export interface ControllerApiCommand {
  target_head_pose: ReachyXYZRPYPose
  target_antennas: [number, number]
  target_body_yaw: number
}

function buildCommand(headPose: ReachyXYZRPYPose, antennas: [number, number], bodyYaw: number) {
  return {
    target_head_pose: headPose,
    target_antennas: antennas,
    target_body_yaw: bodyYaw,
  } satisfies ControllerApiCommand
}

export function useControllerAPI({
  daemonBaseUrl,
  enabled = true,
}: {
  daemonBaseUrl: string
  enabled?: boolean
}) {
  const normalizedBaseUrl = normalizeReachyDaemonBaseUrl(daemonBaseUrl)
  const lastSendTimeRef = useRef(0)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<number | null>(null)
  const manualCloseRef = useRef(false)
  const [transportState, setTransportState] = useState<ControllerTransportState>(
    enabled ? "connecting" : "disabled"
  )
  const [error, setError] = useState<string | null>(null)

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const disconnectWebSocket = useCallback(() => {
    clearReconnectTimer()
    manualCloseRef.current = true

    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      wsRef.current.close(1000)
      wsRef.current = null
    }
  }, [clearReconnectTimer])

  const connectWebSocket = useCallback(() => {
    if (!enabled) {
      disconnectWebSocket()
      setTransportState("disabled")
      setError(null)
      return
    }

    manualCloseRef.current = false
    clearReconnectTimer()

    if (wsRef.current) {
      wsRef.current.close(1000)
      wsRef.current = null
    }

    setTransportState((prev) => (prev === "websocket" ? prev : "connecting"))

    try {
      const socket = new WebSocket(buildControllerMoveWebSocketUrl(normalizedBaseUrl))
      wsRef.current = socket

      socket.onopen = () => {
        reconnectAttemptsRef.current = 0
        setTransportState("websocket")
        setError(null)
      }

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { status?: string; detail?: string }
          if (payload.status === "error") {
            setError(payload.detail ?? "Controller command failed")
          }
        } catch {
          // Ignore non-JSON daemon frames.
        }
      }

      socket.onerror = () => {
        setError(`Unable to reach Reachy daemon at ${normalizedBaseUrl}`)
      }

      socket.onclose = (event) => {
        wsRef.current = null

        if (manualCloseRef.current) {
          return
        }

        setTransportState("offline")

        if (event.code !== 1000 && reconnectAttemptsRef.current < WS_MAX_RECONNECT_ATTEMPTS) {
          reconnectAttemptsRef.current += 1
          reconnectTimerRef.current = window.setTimeout(() => {
            reconnectTimerRef.current = null
            connectWebSocket()
          }, WS_RECONNECT_DELAY_MS)
        }
      }
    } catch {
      setTransportState("offline")
      setError(`Unable to reach Reachy daemon at ${normalizedBaseUrl}`)
    }
  }, [clearReconnectTimer, disconnectWebSocket, enabled, normalizedBaseUrl])

  useEffect(() => {
    connectWebSocket()

    return () => {
      disconnectWebSocket()
    }
  }, [connectWebSocket, disconnectWebSocket])

  const sendViaWebSocket = useCallback((command: ControllerApiCommand) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(command))
      setTransportState((prev) => (prev === "websocket" ? prev : "websocket"))
      return true
    }

    return false
  }, [])

  const sendViaHttp = useCallback(
    async (command: ControllerApiCommand) => {
      try {
        const response = await fetch(buildControllerSetTargetUrl(normalizedBaseUrl), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(command),
        })

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        setTransportState("http")
        setError(null)
        return response
      } catch (nextError) {
        setTransportState("offline")
        setError(
          nextError instanceof Error
            ? nextError.message
            : `Unable to reach Reachy daemon at ${normalizedBaseUrl}`
        )
        throw nextError
      }
    },
    [normalizedBaseUrl]
  )

  const sendCommand = useCallback(
    (headPose: ReachyXYZRPYPose, antennas: [number, number], bodyYaw: number) => {
      if (!enabled) return

      const now = Date.now()
      if (now - lastSendTimeRef.current < SEND_THROTTLE_MS) {
        return
      }

      lastSendTimeRef.current = now
      const command = buildCommand(headPose, antennas, bodyYaw)

      if (!sendViaWebSocket(command)) {
        void sendViaHttp(command).catch(() => undefined)
      }
    },
    [enabled, sendViaHttp, sendViaWebSocket]
  )

  const forceSendCommand = useCallback(
    async (headPose: ReachyXYZRPYPose, antennas: [number, number], bodyYaw: number) => {
      if (!enabled) return null

      lastSendTimeRef.current = Date.now()
      const command = buildCommand(headPose, antennas, bodyYaw)

      if (sendViaWebSocket(command)) {
        return { status: "ok", transport: "websocket" as const }
      }

      return sendViaHttp(command)
    },
    [enabled, sendViaHttp, sendViaWebSocket]
  )

  return {
    transportState,
    error,
    sendCommand,
    forceSendCommand,
  }
}
