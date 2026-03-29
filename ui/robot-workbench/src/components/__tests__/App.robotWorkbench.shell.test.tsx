import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '@/App'

const project = {
  name: 'Sample Project',
  path: '/projects/sample',
  last_accessed: Math.floor(Date.now() / 1000),
  is_git_repo: true,
  git_branch: 'main',
  git_status: 'clean',
}

const tauriCore = vi.hoisted(() => ({
  invoke: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => tauriCore)
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }))
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
vi.mock('@/components/AIAgentStatusBar', () => ({ AIAgentStatusBar: () => <div data-testid="status-bar" /> }))
vi.mock('@/components/ui/tabs', () => {
  const React = require('react')
  const TabsContext = React.createContext<{ value: string; onValueChange?: (value: string) => void } | null>(null)

  const Tabs = ({ value, onValueChange, children }: any) => (
    <TabsContext.Provider value={{ value, onValueChange }}>
      <div data-testid="tabs" data-active-tab={value}>{children}</div>
    </TabsContext.Provider>
  )

  const TabsContent = ({ value, children, forceMount, ...props }: any) => {
    const context = React.useContext(TabsContext)
    if (!context) {
      throw new Error('TabsContent must be used within Tabs')
    }
    if (!forceMount && context.value !== value) return null
    return (
      <div data-state={context.value === value ? 'active' : 'inactive'} {...props}>
        {children}
      </div>
    )
  }

  return { Tabs, TabsContent }
})

const defaultSettings = {
  show_console_output: true,
  projects_folder: '',
  file_mentions_enabled: true,
  show_welcome_recent_projects: true,
  chat_send_shortcut: 'mod+enter',
  code_settings: { theme: 'github', font_size: 14, auto_collapse_sidebar: false },
  ui_theme: 'auto',
  has_completed_onboarding: true,
}

async function clickRecentProject() {
  const projectButtons = await screen.findAllByTitle('/projects/sample')
  fireEvent.click(projectButtons[0])
  await screen.findByTestId('project-identity-header')
}

if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

if (typeof document !== 'undefined') describe('App robot workbench shell', () => {
  beforeEach(() => {
    window.localStorage.clear()

    const invoke = tauriCore.invoke as unknown as ReturnType<typeof vi.fn>
    invoke.mockReset()
    invoke.mockImplementation(async (cmd: string) => {
      switch (cmd) {
        case 'load_app_settings':
          return defaultSettings
        case 'load_all_agent_settings':
          return { max_concurrent_sessions: 3 }
        case 'list_recent_projects':
        case 'refresh_recent_projects':
          return [project]
        case 'open_existing_project':
          return project
        case 'get_cli_project_path':
          return null
        case 'clear_cli_project_path':
          return null
        case 'get_user_home_directory':
          return '/projects'
        case 'get_available_project_applications':
        case 'get_project_git_worktrees':
          return []
        case 'set_window_theme':
        case 'add_project_to_recent':
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
  })

  it('does not render the robot side panel on the welcome screen', async () => {
    render(<App />)

    expect(await screen.findByText('Welcome to Commander')).toBeInTheDocument()
    expect(screen.queryByTestId('robot-side-panel')).toBeNull()
  })

  it('renders the robot side panel after opening a project', async () => {
    render(<App />)

    await clickRecentProject()

    expect(await screen.findByTestId('chat-interface')).toBeInTheDocument()
    expect(await screen.findByTestId('robot-side-panel')).toBeInTheDocument()
    expect(screen.getByTestId('robot-side-panel-scroll')).toBeInTheDocument()
    expect(screen.getByTestId('robot-side-panel-collapse')).toBeInTheDocument()
    expect(screen.getByTestId('robot-side-panel-collapse').className).toContain('top-1/2')
    expect(screen.getByTestId('robot-side-panel-collapse').className).toContain('left-0')
    expect(screen.getByTestId('mujoco-panel')).toBeInTheDocument()
    expect(screen.getByTestId('reachy-status-panel')).toBeInTheDocument()
    expect(screen.getByText('MuJoCo')).toBeInTheDocument()
    expect(screen.getByText('Reachy Status')).toBeInTheDocument()
  })

  it('switches to a dedicated robot workbench view from the top header action', async () => {
    render(<App />)

    await clickRecentProject()

    fireEvent.click(screen.getByRole('button', { name: 'Robot Workbench' }))

    await waitFor(() => {
      expect(screen.getByTestId('tabs')).toHaveAttribute('data-active-tab', 'robot')
    })

    expect(screen.getByTestId('robot-workbench-main-panel')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-main-scroll')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-immersive-shell')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-main-panel').className).toContain(
      'bg-[linear-gradient(180deg,#f8fafc_0%,#eef2f7_100%)]',
    )
    expect(screen.getByTestId('robot-workbench-main-scroll').className).toContain('overflow-hidden')
    expect(screen.getByTestId('robot-workbench-stage-column')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-column').className).toContain('h-full')
    expect(screen.getByTestId('robot-workbench-stage-column').className).toContain('px-3')
    expect(screen.getByTestId('robot-workbench-stage-column').className).toContain('pt-[33px]')
    expect(screen.getByTestId('robot-workbench-controller-column')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-controller-column').className).toContain('h-full')
    expect(screen.getByTestId('robot-workbench-controller-column').className).toContain('pt-[33px]')
    expect(screen.getByTestId('robot-workbench-controller-scroll')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-logs').className).toContain('flex-none')
    expect(screen.getByTestId('robot-workbench-stage-logs-scroll')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-logs-scroll').className).toContain(
      'h-[clamp(108px,18vh,140px)]',
    )
    expect(screen.getByTestId('robot-workbench-stage-toolbar')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Simulation' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Simulation' }).className).toContain('h-12')
    expect(screen.getByRole('button', { name: 'Start Simulation' }).className).toContain('min-w-[148px]')
    expect(screen.getByRole('button', { name: 'Stop Runtime' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop Runtime' }).className).toContain('h-12')
    expect(screen.getByRole('button', { name: 'Stop Runtime' }).className).toContain('min-w-[148px]')
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' }).className).toContain('h-12')
    expect(screen.getByTestId('robot-workbench-stage-hero')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-camera-overlay')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('robot-workbench-camera-overlay')).getByTestId(
        'robot-workbench-camera-feed'
      )
    ).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-camera-overlay').className).toContain('w-[140px]')
    expect(screen.getByTestId('robot-workbench-camera-overlay').className).toContain('h-[105px]')
    expect(screen.getByTestId('robot-workbench-stage-hero').className).not.toContain('pb-16')
    expect(
      within(screen.getByTestId('robot-workbench-stage-hero'))
        .getByTestId('reachy-simulation-viewport')
        .className,
    ).toContain('aspect-[4/3]')
    expect(
      within(screen.getByTestId('robot-workbench-stage-hero'))
        .getByTestId('reachy-simulation-viewport')
        .className,
    ).toContain('min-h-[250px]')
    expect(screen.getByTestId('robot-workbench-stage-header')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-title-block')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-header').className).toContain(
      'lg:grid-cols-[minmax(0,1fr)_140px]',
    )
    expect(screen.getByTestId('robot-workbench-stage-title-block').className).toContain('min-w-0')
    expect(screen.getByTestId('robot-workbench-stage-title').className).toContain('text-[20px]')
    expect(screen.getByTestId('robot-workbench-stage-title').className).toContain('whitespace-nowrap')
    expect(screen.getByTestId('robot-workbench-stage-camera-slot')).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-stage-version-line')).toHaveTextContent('Daemon')
    expect(
      within(screen.getByTestId('robot-workbench-stage-column')).queryByTestId('robot-workbench-audio-controls')
    ).toBeNull()
    expect(screen.queryByText(/Project sample/i)).toBeNull()
    expect(screen.queryByText(/Workbench UI/i)).toBeNull()
    expect(screen.queryByText(/^State /)).toBeNull()
    expect(
      within(screen.getByTestId('robot-workbench-controller-scroll')).getByTestId(
        'robot-workbench-audio-controls'
      )
    ).toBeInTheDocument()
    expect(screen.getByTestId('robot-workbench-audio-controls').className).toContain('md:grid-cols-2')
    expect(screen.getByTestId('robot-workbench-speaker-card').className).toContain('h-16')
    expect(screen.getByTestId('robot-workbench-microphone-card').className).toContain('h-16')
    expect(screen.getByTestId('robot-workbench-main-layout').className).not.toContain(
      'xl:grid-cols-[450px_450px]',
    )
    expect(screen.getByTestId('robot-workbench-main-layout').className).toContain(
      'xl:grid-cols-[minmax(420px,520px)_minmax(480px,1fr)]',
    )
    expect(screen.getByTestId('robot-workbench-main-layout').className).toContain('h-full')
    expect(screen.getByTestId('robot-workbench-main-layout').className).toContain('gap-0')
    expect(screen.getByText('Speaker')).toBeInTheDocument()
    expect(screen.getByText('Microphone')).toBeInTheDocument()
    expect(screen.getByText('Logs')).toBeInTheDocument()
    expect(screen.getByText('Controller')).toBeInTheDocument()
    expect(screen.getByText('Antennas')).toBeInTheDocument()
    expect(screen.getByText('Head')).toBeInTheDocument()
    expect(screen.getByText('Body')).toBeInTheDocument()
    expect(screen.queryByTestId('robot-side-panel')).toBeNull()
  })

  it('collapses the robot side panel into a right rail and expands it again', async () => {
    render(<App />)

    await clickRecentProject()

    fireEvent.click(await screen.findByTestId('robot-side-panel-collapse'))

    expect(screen.queryByTestId('robot-side-panel')).toBeNull()
    expect(screen.getByTestId('robot-side-panel-collapsed')).toBeInTheDocument()
    expect(screen.getByTestId('robot-side-panel-expand')).toBeInTheDocument()
    expect(screen.getByTestId('robot-side-panel-expand').className).toContain('top-1/2')
    expect(screen.getByTestId('robot-side-panel-expand').className).toContain('left-0')

    fireEvent.click(screen.getByTestId('robot-side-panel-expand'))

    expect(await screen.findByTestId('robot-side-panel')).toBeInTheDocument()
  })

  it('resizes the robot workbench panel from the left edge drag handle', async () => {
    render(<App />)

    await clickRecentProject()

    const panel = await screen.findByTestId('robot-side-panel')
    const handle = screen.getByTestId('robot-side-panel-resize-handle')

    expect(panel).toHaveStyle({ width: '360px' })

    fireEvent.mouseDown(handle, { clientX: 900 })

    await waitFor(() => {
      expect(document.body.style.cursor).toBe('col-resize')
    })

    fireEvent.mouseMove(document, { clientX: 820 })
    fireEvent.mouseUp(document)

    await waitFor(() => {
      expect(panel).toHaveStyle({ width: '440px' })
    })
  })
})
