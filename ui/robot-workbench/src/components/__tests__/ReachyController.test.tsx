import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import ReachyController from "@/components/controller/ReachyController"

class MockWebSocket {
  static OPEN = 1
  static instances: MockWebSocket[] = []

  url: string
  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  close = vi.fn(() => {
    this.readyState = 3
  })
  send = vi.fn()

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event("open"))
  }
}

if (typeof document !== "undefined")
  describe("ReachyController", () => {
    beforeEach(() => {
      MockWebSocket.instances = []
      vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket)
      vi.stubGlobal("fetch", vi.fn())
      vi.stubGlobal("requestAnimationFrame", ((callback: FrameRequestCallback) => {
        return window.setTimeout(() => callback(performance.now()), 0)
      }) as typeof requestAnimationFrame)
      vi.stubGlobal("cancelAnimationFrame", ((handle: number) => {
        window.clearTimeout(handle)
      }) as typeof cancelAnimationFrame)
    })

    afterEach(() => {
      vi.unstubAllGlobals()
      vi.clearAllMocks()
    })

    it("sends reset commands over websocket when the daemon socket is open", async () => {
      render(
        <ReachyController
          daemonBaseUrl="http://localhost:8000"
          snapshot={null}
          syncState="live"
        />
      )

      expect(MockWebSocket.instances[0]?.url).toBe("ws://localhost:8000/api/move/ws/set_target")

      await act(async () => {
        MockWebSocket.instances[0]?.emitOpen()
        fireEvent.click(screen.getByRole("button", { name: "回正" }))
      })

      await waitFor(() => {
        expect(MockWebSocket.instances[0]?.send).toHaveBeenCalled()
      })

      const payload = JSON.parse(String(MockWebSocket.instances[0]?.send.mock.calls[0]?.[0]))
      expect(payload.target_body_yaw).toBe(0)
      expect(payload.target_antennas).toEqual([0, 0])
      expect(payload.target_head_pose).toMatchObject({
        x: 0,
        y: 0,
        z: 0,
        pitch: 0,
        yaw: 0,
        roll: 0,
      })
    })

    it("falls back to HTTP when the websocket is not ready", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok" }),
      } as Response)

      render(
        <ReachyController
          daemonBaseUrl="http://localhost:8000"
          snapshot={null}
          syncState="disabled"
        />
      )

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "回正" }))
      })

      await waitFor(() => {
        expect(fetch).toHaveBeenCalledWith(
          "http://localhost:8000/api/move/set_target",
          expect.objectContaining({
            method: "POST",
          })
        )
      })
    })
  })
