import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SettingsModal } from '@/components/SettingsModal'
import { SettingsProvider } from '@/contexts/settings-context'

const invokeMock = vi.fn(async (cmd: string) => {
  switch (cmd) {
    case 'load_app_settings':
      return {
        show_console_output: true,
        projects_folder: '',
        file_mentions_enabled: true,
        ui_theme: 'auto',
        chat_send_shortcut: 'mod+enter',
        show_welcome_recent_projects: true,
        code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: false },
        robot_settings: {
          live_status_enabled: true,
          daemon_base_url: 'http://localhost:8000',
        },
      }
    case 'set_window_theme':
      return null
    case 'save_app_settings':
      return null
    case 'get_default_projects_folder':
      return ''
    case 'load_agent_settings':
      return { claude: true, codex: true, gemini: true }
    case 'load_all_agent_settings':
      return { max_concurrent_sessions: 10 }
    default:
      return null
  }
})

vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args: any[]) => invokeMock(...args) }))
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }))
vi.mock('@/components/settings', () => ({
  GeneralSettings: ({
    tempReachyLiveStatusEnabled,
    onReachyLiveStatusEnabledChange,
    tempReachyDaemonBaseUrl,
    onReachyDaemonBaseUrlChange,
  }: any) => (
    <div>
      <label htmlFor="reachy-live-status-toggle">Enable Reachy Live Status</label>
      <input
        id="reachy-live-status-toggle"
        aria-label="Enable Reachy Live Status"
        type="checkbox"
        checked={!!tempReachyLiveStatusEnabled}
        onChange={(event) => onReachyLiveStatusEnabledChange?.(event.target.checked)}
      />
      <p>
        {tempReachyLiveStatusEnabled
          ? 'Robot status live stream enabled for the configured daemon.'
          : 'Robot status live stream disabled'}
      </p>
      <label htmlFor="reachy-daemon-url">Reachy Daemon URL</label>
      <input
        id="reachy-daemon-url"
        aria-label="Reachy Daemon URL"
        value={tempReachyDaemonBaseUrl}
        onChange={(event) => onReachyDaemonBaseUrlChange?.(event.target.value)}
      />
    </div>
  ),
  AppearanceSettings: () => null,
  ChatSettings: () => null,
  AgentSettings: () => null,
  LLMSettings: () => null,
  CodeSettings: () => null,
  SubAgentsSettings: () => null,
  PromptsUISettings: () => null,
}))
vi.mock('@/components/settings/DocsSettings', () => ({
  DocsSettings: () => null,
}))
vi.mock('@/components/ToastProvider', () => ({
  ToastProvider: ({ children }: { children: any }) => children,
  useToast: () => ({
    showSuccess: vi.fn(),
    showError: vi.fn(),
    showToast: vi.fn(),
  }),
}))

if (typeof document !== 'undefined') describe('SettingsModal robot status autosave', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('persists the live status toggle through app settings', async () => {
    render(
      <SettingsProvider>
        <SettingsModal isOpen={true} onClose={() => {}} initialTab="general" />
      </SettingsProvider>
    )

    fireEvent.click(await screen.findByLabelText('Enable Reachy Live Status'))

    await screen.findByText('Robot status live stream disabled')

    await waitFor(() => {
      const saveCalls = invokeMock.mock.calls.filter(([cmd]) => cmd === 'save_app_settings')
      expect(saveCalls.length).toBeGreaterThan(0)
      const lastArgs = saveCalls[saveCalls.length - 1]?.[1]
      expect(lastArgs.settings.robot_settings.live_status_enabled).toBe(false)
    })
  })

  it('persists the daemon URL when edited from settings', async () => {
    render(
      <SettingsProvider>
        <SettingsModal isOpen={true} onClose={() => {}} initialTab="general" />
      </SettingsProvider>
    )

    const input = await screen.findByLabelText('Reachy Daemon URL')
    fireEvent.change(input, { target: { value: 'http://reachy-mini.local:8000/' } })

    await screen.findByDisplayValue('http://reachy-mini.local:8000/')

    await waitFor(() => {
      const saveCalls = invokeMock.mock.calls.filter(([cmd]) => cmd === 'save_app_settings')
      expect(saveCalls.length).toBeGreaterThan(0)
      const matchingCall = [...saveCalls]
        .reverse()
        .find(([, args]) => args?.settings?.robot_settings?.daemon_base_url === 'http://reachy-mini.local:8000')
      expect(matchingCall).toBeTruthy()
    })
  })
})
