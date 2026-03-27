import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ToastProvider } from '@/components/ToastProvider'
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

if (typeof document !== 'undefined') describe('SettingsModal open interaction guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders a temporary interaction guard when the modal opens', async () => {
    render(
      <ToastProvider>
        <SettingsProvider>
          <SettingsModal isOpen={true} onClose={() => {}} initialTab={'general'} />
        </SettingsProvider>
      </ToastProvider>
    )

    await screen.findByText(/general settings/i)
    expect(screen.getByTestId('settings-open-guard')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.queryByTestId('settings-open-guard')).toBeNull()
    }, { timeout: 1000 })
  })
})
