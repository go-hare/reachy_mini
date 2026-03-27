import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import App from '@/App'

const tauriCore = vi.hoisted(() => ({
  invoke: vi.fn(),
}))

const eventListeners = vi.hoisted(() => new Map<string, Set<(...args: any[]) => void>>())

vi.mock('@tauri-apps/api/core', () => tauriCore)
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (event: string, handler: (...args: any[]) => void) => {
    if (!eventListeners.get(event)) eventListeners.set(event, new Set())
    eventListeners.get(event)!.add(handler)
    return () => { eventListeners.get(event)?.delete(handler) }
  }),
}))
vi.mock('@/services/auth-service', () => ({
  initiateDeviceAuth: vi.fn(),
  pollForAuth: vi.fn(),
  validateToken: vi.fn().mockResolvedValue({
    id: '1', email: 'test@test.com', name: 'Test User', avatar_url: null,
  }),
  logoutFromApi: vi.fn(),
  AUTH_CONFIG: {
    apiBaseUrl: 'https://autohand.ai/api/auth',
    verificationBaseUrl: 'https://autohand.ai/cli-auth',
    pollInterval: 2000,
    authTimeout: 300000,
    sessionExpiryDays: 30,
  },
}))
vi.mock('@/components/ChatInterface', () => ({ ChatInterface: () => <div data-testid="chat-interface" /> }))
vi.mock('@/components/CodeView', () => ({ CodeView: () => <div data-testid="code-view" /> }))
vi.mock('@/components/HistoryView', () => ({ HistoryView: () => <div data-testid="history-view" /> }))

function setupInvokeMock() {
  const invoke = tauriCore.invoke as unknown as ReturnType<typeof vi.fn>
  invoke.mockReset()
  invoke.mockImplementation(async (cmd: string) => {
    switch (cmd) {
      case 'load_app_settings':
        return {
          show_console_output: true,
          projects_folder: '',
          file_mentions_enabled: true,
          show_welcome_recent_projects: true,
          chat_send_shortcut: 'mod+enter' as const,
          ui_theme: 'auto',
          default_cli_agent: 'claude' as const,
          has_completed_onboarding: true,
          code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: true },
        }
      case 'list_recent_projects':
      case 'refresh_recent_projects':
        return []
      case 'get_cli_project_path':
        return null
      case 'get_user_home_directory':
        return '/projects'
      case 'set_window_theme':
      case 'save_app_settings':
        return null
      case 'get_auth_token':
        return 'valid-token'
      case 'get_auth_user':
        return { id: '1', email: 'test@test.com', name: 'Test User', avatar_url: null }
      default:
        return null
    }
  })
}

if (typeof document !== 'undefined') describe('App hotkey removal', () => {
  beforeEach(() => {
    eventListeners.clear()
    setupInvokeMock()
  })

  it('does not register the legacy chat history shortcut listener', async () => {
    render(<App />)

    expect(eventListeners.get('shortcut://toggle-chat-history')).toBeUndefined()
    expect(eventListeners.get('shortcut://open-settings')).toBeUndefined()
    expect(eventListeners.get('shortcut://toggle-chat')).toBeUndefined()
    expect(eventListeners.get('shortcut://copy-project-path')).toBeUndefined()
  })
})
