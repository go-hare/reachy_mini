import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const tauriCore = vi.hoisted(() => ({
  invoke: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => tauriCore)
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }))

vi.mock('@/components/ChatInterface', () => ({ ChatInterface: () => <div data-testid="chat-interface" /> }))
vi.mock('@/components/CodeView', () => ({ CodeView: () => <div data-testid="code-view" /> }))
vi.mock('@/components/HistoryView', () => ({ HistoryView: () => <div data-testid="history-view" /> }))
vi.mock('@/components/AIAgentStatusBar', () => ({ AIAgentStatusBar: () => <div data-testid="status-bar" /> }))
vi.mock('@/components/SettingsModal', () => ({
  SettingsModal: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div data-testid="settings-modal">Settings Modal</div> : null),
}))

import App from '@/App'

describe('App without login gate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('opens the main shell even when no auth token is stored', async () => {
    tauriCore.invoke.mockImplementation(async (cmd: string) => {
      if (cmd === 'load_app_settings') {
        return {
          show_console_output: true,
          projects_folder: '',
          file_mentions_enabled: true,
          has_completed_onboarding: true,
          code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: false },
        }
      }
      if (cmd === 'list_recent_projects') return []
      if (cmd === 'get_user_home_directory') return '/home/test'
      return null
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText(/welcome to commander/i)).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/test@test.com/i)).not.toBeInTheDocument()
  })

  it('still shows the main shell with stored settings and projects', async () => {
    tauriCore.invoke.mockImplementation(async (cmd: string) => {
      if (cmd === 'load_app_settings') return { show_console_output: true, projects_folder: '', file_mentions_enabled: true, has_completed_onboarding: true, code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: false } }
      if (cmd === 'list_recent_projects') return []
      if (cmd === 'get_user_home_directory') return '/home/test'
      return null
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText(/welcome to commander/i)).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
  })

  it('opens settings from the sidebar footer without restoring user UI', async () => {
    tauriCore.invoke.mockImplementation(async (cmd: string) => {
      if (cmd === 'load_app_settings') return { show_console_output: true, projects_folder: '', file_mentions_enabled: true, has_completed_onboarding: true, code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: false } }
      if (cmd === 'list_recent_projects') return []
      if (cmd === 'get_user_home_directory') return '/home/test'
      return null
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText(/welcome to commander/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /open settings/i }))

    await waitFor(() => {
      expect(screen.getByTestId('settings-modal')).toBeInTheDocument()
    })

    expect(screen.queryByText(/test@test.com/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/sign out/i)).not.toBeInTheDocument()
  })
})
