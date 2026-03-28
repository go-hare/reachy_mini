import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { AppSidebar } from '@/components/app-sidebar'
import { ToastProvider } from '@/components/ToastProvider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { SidebarWidthProvider } from '@/contexts/sidebar-width-context'

const invokeMock = vi.fn()

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: Parameters<typeof invokeMock>) => invokeMock(...args),
}))

// Mock the hooks used by AppSidebar
vi.mock('@/hooks/use-recent-projects', () => ({
  useRecentProjects: () => ({
    projects: [
      { name: 'my-project', path: '/tmp/my-project', is_git_repo: true, git_branch: 'main', git_status: 'dirty' },
      { name: 'other', path: '/tmp/other', is_git_repo: false },
    ],
    loading: false,
    error: null,
    refreshProjects: vi.fn(),
  }),
}))

vi.mock('@/contexts/settings-context', () => ({
  useSettings: () => ({
    settings: {
      code_settings: {
        theme: 'github',
        font_size: 14,
        auto_collapse_sidebar: false,
        show_file_explorer: true,
        show_project_git_refs_in_sidebar: true,
      },
    },
  }),
}))

function renderSidebar(props: Partial<React.ComponentProps<typeof AppSidebar>> = {}) {
  return render(
    <ToastProvider>
      <SidebarWidthProvider>
        <SidebarProvider>
          <AppSidebar
            currentProject={{ name: 'my-project', path: '/tmp/my-project', is_git_repo: true, git_branch: 'main', git_status: 'dirty' } as any}
            {...props}
          />
        </SidebarProvider>
      </SidebarWidthProvider>
    </ToastProvider>
  )
}

describe('AppSidebar active project', () => {
  beforeEach(() => {
    invokeMock.mockReset()
    invokeMock.mockResolvedValue([])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('applies sidebar-active styling to the current project', async () => {
    renderSidebar()
    await screen.findByRole('button', { name: /project actions for my-project/i })
    const link = screen.getByTitle(/\/tmp\/my-project/)
    expect(link).toHaveAttribute('data-active', 'true')
    expect(link.className).toContain('data-[active=true]:bg-sidebar-accent')
    expect(link.className).not.toContain('border-l-2')
    expect(screen.queryByText('main')).toBeNull()
  })

  it('does not show spinner when no projects executing', async () => {
    renderSidebar({ executingProjectPaths: new Set() })
    await screen.findByRole('button', { name: /project actions for my-project/i })
    expect(screen.queryByLabelText('Agent running')).toBeNull()
  })

  it('shows spinner on the executing project', async () => {
    renderSidebar({ executingProjectPaths: new Set(['/tmp/my-project']) })
    await screen.findByRole('button', { name: /project actions for my-project/i })
    expect(screen.getByLabelText('Agent running')).toBeTruthy()
  })

  it('shows spinner on a non-selected project that is executing', async () => {
    // currentProject is /tmp/my-project but /tmp/other is executing
    renderSidebar({ executingProjectPaths: new Set(['/tmp/other']) })
    await screen.findByRole('button', { name: /project actions for my-project/i })
    // Spinner should appear on the "other" project row
    expect(screen.getByLabelText('Agent running')).toBeTruthy()
  })

  it('uses Radix ScrollArea as the sidebar scroll container without a login footer', async () => {
    const { container } = renderSidebar()
    await screen.findByRole('button', { name: /project actions for my-project/i })

    const content = container.querySelector('[data-sidebar="content"]')
    const footer = container.querySelector('[data-sidebar="footer"]')

    expect(content).toHaveClass('min-h-0')
    expect(content).toHaveClass('overflow-hidden') // Radix ScrollArea uses overflow-hidden on root
    expect(footer).not.toBeNull()
    expect(screen.getByRole('button', { name: /open settings/i })).toBeInTheDocument()
    expect(screen.queryByText(/test@test.com/i)).toBeNull()
    expect(screen.queryByText(/sign out/i)).toBeNull()
  })

  it('opens settings from the footer without rendering user identity', async () => {
    const onOpenSettings = vi.fn()

    renderSidebar({ onOpenSettings })
    await screen.findByRole('button', { name: /project actions for my-project/i })

    fireEvent.click(screen.getByRole('button', { name: /open settings/i }))

    expect(onOpenSettings).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/test@test.com/i)).toBeNull()
  })

  it('prevents footer settings clicks from bubbling into surrounding sidebar content', async () => {
    const onOpenSettings = vi.fn()
    const onParentClick = vi.fn()

    render(
      <div onClick={onParentClick}>
        <ToastProvider>
          <SidebarWidthProvider>
            <SidebarProvider>
              <AppSidebar
                currentProject={{ name: 'my-project', path: '/tmp/my-project', is_git_repo: true, git_branch: 'main', git_status: 'dirty' } as any}
                onOpenSettings={onOpenSettings}
              />
            </SidebarProvider>
          </SidebarWidthProvider>
        </ToastProvider>
      </div>
    )

    const button = await screen.findByRole('button', { name: /open settings/i })
    const clickEvent = new MouseEvent('click', { bubbles: true, cancelable: true })

    button.dispatchEvent(clickEvent)

    expect(clickEvent.defaultPrevented).toBe(true)
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
    expect(onParentClick).not.toHaveBeenCalled()
  })

  it('uses themed scrollbar CSS variables and scrollbar-color', () => {
    const stylesheet = readFileSync('src/index.css', 'utf8')

    expect(stylesheet).toContain('--scrollbar-thumb')
    expect(stylesheet).toContain('--scrollbar-thumb-active')
    expect(stylesheet).toContain('--scrollbar-track')
    expect(stylesheet).toContain('.theme-scrollbar')
    expect(stylesheet).toContain('scrollbar-color: hsl(var(--scrollbar-thumb)) hsl(var(--scrollbar-track))')
    expect(stylesheet).toContain('scrollbar-width: thin')
  })

  it('uses Radix ScrollArea in sidebar instead of native scrollbar pseudo-elements', () => {
    const scrollAreaComponent = readFileSync('src/components/ui/scroll-area.tsx', 'utf8')
    expect(scrollAreaComponent).toContain('--scrollbar-thumb')
    expect(scrollAreaComponent).toContain('--scrollbar-thumb-active')

    const sidebarComponent = readFileSync('src/components/ui/sidebar.tsx', 'utf8')
    expect(sidebarComponent).toContain('ScrollArea')
  })
})
