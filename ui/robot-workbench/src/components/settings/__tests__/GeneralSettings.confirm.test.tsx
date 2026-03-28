import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { GeneralSettings } from '../GeneralSettings'
import { ToastProvider } from '@/components/ToastProvider'
import { DEFAULT_MUJOCO_WEB_VIEWER_URL } from '@/lib/reachy-daemon'

function renderWithProviders(ui: React.ReactNode) {
  return render(<ToastProvider>{ui}</ToastProvider>)
}

function getRecentProjectsClearButton() {
  const buttons = screen.getAllByRole('button', { name: /^clear$/i })
  return buttons[buttons.length - 1]!
}

const baseProps = {
  tempDefaultProjectsFolder: '/tmp',
  tempShowConsoleOutput: true,
  systemPrompt: '',
  saving: false,
  onFolderChange: vi.fn(),
  onSelectFolder: vi.fn(async () => {}),
  onConsoleOutputChange: vi.fn(),
  onSystemPromptChange: vi.fn(),
  onClearRecentProjects: vi.fn(async () => {}),
}

if (typeof document !== 'undefined') describe('GeneralSettings clear recent projects confirmation', () => {
  it('opens confirmation dialog and cancels without clearing', async () => {
    const onClearRecentProjects = vi.fn(async () => {})
    renderWithProviders(
      <GeneralSettings {...baseProps} onClearRecentProjects={onClearRecentProjects} />
    )

    fireEvent.click(getRecentProjectsClearButton())
    expect(await screen.findByText(/permanently remove all recent projects/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    await waitFor(() => {
      expect(screen.queryByText(/permanently remove all recent projects/i)).not.toBeInTheDocument()
    })
    expect(onClearRecentProjects).not.toHaveBeenCalled()
  })

  it('confirms and shows success toast after clearing', async () => {
    const onClearRecentProjects = vi.fn(async () => {})
    renderWithProviders(
      <GeneralSettings {...baseProps} onClearRecentProjects={onClearRecentProjects} />
    )

    fireEvent.click(getRecentProjectsClearButton())
    expect(await screen.findByText(/permanently remove all recent projects/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /yes, clear them/i }))

    await waitFor(() => {
      expect(onClearRecentProjects).toHaveBeenCalled()
    })

    // Toast visible
    await waitFor(() => {
      expect(screen.getByText(/recent projects cleared/i)).toBeInTheDocument()
    })
  })

  it('applies the local MuJoCo viewer preset and clears it', async () => {
    const onMujocoViewerUrlChange = vi.fn()
    const onClearRecentProjects = vi.fn(async () => {})
    const view = renderWithProviders(
      <GeneralSettings
        {...baseProps}
        onClearRecentProjects={onClearRecentProjects}
        tempMujocoViewerUrl=""
        onMujocoViewerUrlChange={onMujocoViewerUrlChange}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /use local preset/i }))
    expect(onMujocoViewerUrlChange).toHaveBeenCalledWith(DEFAULT_MUJOCO_WEB_VIEWER_URL)

    view.rerender(
      <ToastProvider>
        <GeneralSettings
          {...baseProps}
          onClearRecentProjects={onClearRecentProjects}
          tempMujocoViewerUrl={DEFAULT_MUJOCO_WEB_VIEWER_URL}
          onMujocoViewerUrlChange={onMujocoViewerUrlChange}
        />
      </ToastProvider>
    )

    fireEvent.click(screen.getAllByRole('button', { name: /^clear$/i })[0])
    expect(onMujocoViewerUrlChange).toHaveBeenLastCalledWith('')
  })
})
