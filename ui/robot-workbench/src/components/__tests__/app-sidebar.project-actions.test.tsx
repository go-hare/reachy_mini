import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AppSidebar } from '@/components/app-sidebar'
import { ToastProvider } from '@/components/ToastProvider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { SidebarWidthProvider } from '@/contexts/sidebar-width-context'

const invokeMock = vi.fn()

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: Parameters<typeof invokeMock>) => invokeMock(...args),
}))

vi.mock('@/hooks/use-recent-projects', () => ({
  useRecentProjects: () => ({
    projects: [
      {
        name: 'my-project',
        path: '/tmp/my-project',
        last_accessed: 1,
        is_git_repo: true,
        git_branch: 'main',
        git_status: 'dirty',
      },
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
            currentProject={null}
            onProjectSelect={vi.fn()}
            {...props}
          />
        </SidebarProvider>
      </SidebarWidthProvider>
    </ToastProvider>
  )
}

describe('AppSidebar project navigation', () => {
  beforeEach(() => {
    invokeMock.mockReset()
    invokeMock.mockImplementation(async (cmd: string) => {
      switch (cmd) {
        case 'get_available_project_applications':
          return [
            { id: 'cursor', label: 'Cursor', installed: true },
            { id: 'zed', label: 'Zed', installed: false },
          ]
        case 'delete_project':
          return null
        case 'create_project_git_branch':
          return null
        case 'create_workspace_worktree':
          return '/tmp/my-project/.commander/feature-ws'
        default:
          return null
      }
    })
  })

  it('renders a persistent project action trigger in the sidebar', async () => {
    renderSidebar()
    const actionsButton = await screen.findByRole('button', { name: /project actions for my-project/i })

    expect(actionsButton).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /expand my-project/i })).not.toBeInTheDocument()
  })

  it('opens the sidebar project menu and handles branch, worktree, and delete actions', async () => {
    const handleBranchCreated = vi.fn()
    const handleWorktreeCreated = vi.fn()
    renderSidebar({
      onProjectBranchCreated: handleBranchCreated,
      onProjectWorktreeCreated: handleWorktreeCreated,
    } as any)

    fireEvent.click(await screen.findByRole('button', { name: /project actions for my-project/i }))

    expect(await screen.findByRole('menuitem', { name: /new branch/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /new worktree/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /delete project/i })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /open directory|show in finder/i })).toBeNull()

    fireEvent.click(screen.getByRole('menuitem', { name: /new branch/i }))
    fireEvent.change(await screen.findByLabelText(/branch name/i), { target: { value: 'feature/sidebar-header' } })
    fireEvent.click(screen.getByRole('button', { name: /^create branch$/i }))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('create_project_git_branch', {
        projectPath: '/tmp/my-project',
        branch: 'feature/sidebar-header',
      })
      expect(handleBranchCreated).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/tmp/my-project' }),
        'feature/sidebar-header'
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /project actions for my-project/i }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /new worktree/i }))
    fireEvent.change(await screen.findByLabelText(/worktree name/i), { target: { value: 'feature-ws' } })
    fireEvent.click(screen.getByRole('button', { name: /^create worktree$/i }))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('create_workspace_worktree', {
        projectPath: '/tmp/my-project',
        name: 'feature-ws',
      })
      expect(handleWorktreeCreated).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/tmp/my-project' }),
        '/tmp/my-project/.commander/feature-ws'
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /project actions for my-project/i }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /delete project/i }))

    expect(await screen.findByText(/type the project name/i)).toBeInTheDocument()
    expect(screen.getByText(/this permanently removes the project directory from disk/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /copy project name/i })).toBeInTheDocument()
  })

  it('opens the project row without rendering sidebar branch or worktree groups', async () => {
    const handleProjectSelect = vi.fn()
    renderSidebar({
      onProjectSelect: handleProjectSelect,
    } as any)

    fireEvent.click(screen.getByRole('link', { name: /my-project/i }))

    await waitFor(() => {
      expect(handleProjectSelect).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/tmp/my-project' })
      )
    })

    expect(screen.queryByText('Branches')).toBeNull()
    expect(screen.queryByText('Worktrees')).toBeNull()
    expect(invokeMock).not.toHaveBeenCalledWith('get_git_branches', expect.anything())
    expect(invokeMock).not.toHaveBeenCalledWith('get_project_git_worktrees', expect.anything())
  })
})
