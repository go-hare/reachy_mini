import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { ReachyStatusPanel } from '@/components/workbench/RobotSidePanel'

const settingsMock = vi.hoisted(() => ({
  useSettings: vi.fn(),
}))

vi.mock('@/contexts/settings-context', () => settingsMock)

class MockWebSocket {
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

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  emitOpen() {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  emitMessage(payload: unknown) {
    this.onmessage?.({
      data: JSON.stringify(payload),
    } as MessageEvent<string>)
  }

  emitClose() {
    this.readyState = 3
    this.onclose?.(new CloseEvent('close'))
  }
}

if (typeof document !== 'undefined') describe('ReachyStatusPanel live status', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('shows a disabled state and skips websocket wiring when live status is turned off', () => {
    settingsMock.useSettings.mockReturnValue({
      settings: {
        robot_settings: {
          live_status_enabled: false,
          daemon_base_url: 'http://localhost:8000',
        },
      },
    })

    render(<ReachyStatusPanel />)

    expect(screen.getByText('Live status is disabled')).toBeInTheDocument()
    expect(screen.getAllByText('Disabled').length).toBeGreaterThan(0)
    expect(MockWebSocket.instances).toHaveLength(0)
  })

  it('renders streamed Reachy state when the websocket delivers updates', async () => {
    settingsMock.useSettings.mockReturnValue({
      settings: {
        robot_settings: {
          live_status_enabled: true,
          daemon_base_url: 'http://localhost:8000/',
        },
      },
    })

    render(<ReachyStatusPanel />)

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0]?.url).toBe(
      'ws://localhost:8000/api/state/ws/full?with_doa=true&with_head_joints=true&with_passive_joints=true',
    )

    await act(async () => {
      MockWebSocket.instances[0]?.emitOpen()
      MockWebSocket.instances[0]?.emitMessage({
        control_mode: 'enabled',
        body_yaw: 0.2,
        antennas_position: [0.15, -0.15],
        head_joints: [0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        passive_joints: new Array(21).fill(0),
        head_pose: {
          x: 0,
          y: 0.01,
          z: 0.12,
          roll: 0.05,
          pitch: -0.15,
          yaw: 0.25,
        },
        timestamp: '2026-03-28T08:15:30.000Z',
      })
    })

    await waitFor(() => {
      expect(screen.getByText('Live')).toBeInTheDocument()
    })

    expect(screen.getByText('enabled')).toBeInTheDocument()
    expect(screen.getByText('14.3deg')).toBeInTheDocument()
    expect(screen.getByText('8.6deg / -8.6deg')).toBeInTheDocument()
    expect(screen.getByText('Yaw 14.3deg | Pitch -8.6deg | Roll 2.9deg')).toBeInTheDocument()
  })

  it('falls back to an offline state when the websocket closes', async () => {
    settingsMock.useSettings.mockReturnValue({
      settings: {
        robot_settings: {
          live_status_enabled: true,
          daemon_base_url: 'http://reachy-mini.local:8000',
        },
      },
    })

    render(<ReachyStatusPanel />)

    await act(async () => {
      MockWebSocket.instances[0]?.emitOpen()
      MockWebSocket.instances[0]?.emitClose()
    })

    await waitFor(() => {
      expect(screen.getByText('Offline')).toBeInTheDocument()
    })

    expect(screen.getByText('Last stream disconnected')).toBeInTheDocument()
  })

  it('keeps the offline UI stable while reconnect retries are scheduled', async () => {
    vi.useFakeTimers()

    settingsMock.useSettings.mockReturnValue({
      settings: {
        robot_settings: {
          live_status_enabled: true,
          daemon_base_url: 'http://reachy-mini.local:8000',
        },
      },
    })

    render(<ReachyStatusPanel />)

    await act(async () => {
      MockWebSocket.instances[0]?.emitClose()
    })

    expect(screen.getByText('Offline')).toBeInTheDocument()
    expect(screen.getByText('Last stream disconnected')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(2_000)
    })

    expect(MockWebSocket.instances).toHaveLength(2)
    expect(screen.getByText('Offline')).toBeInTheDocument()
    expect(screen.getByText('Last stream disconnected')).toBeInTheDocument()
    expect(screen.queryByText('Connecting')).not.toBeInTheDocument()

  })
})
