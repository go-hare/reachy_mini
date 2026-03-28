import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatInput } from '@/components/chat/ChatInput'

describe('ChatInput', () => {
  const baseProps = {
    inputRef: { current: null } as any,
    autocompleteRef: { current: null } as any,
    inputValue: '',
    typedPlaceholder: '',
    onInputChange: vi.fn(),
    onInputSelect: vi.fn(),
    onKeyDown: vi.fn(),
    onFocus: vi.fn(),
    onBlur: vi.fn(),
    onClear: vi.fn(),
    onSend: vi.fn(),
    showAutocomplete: false,
    autocompleteOptions: [],
    selectedOptionIndex: 0,
    onSelectOption: vi.fn(),
    planModeEnabled: false,
    onPlanModeChange: vi.fn(),
    workspaceEnabled: true,
    onWorkspaceEnabledChange: vi.fn(),
    projectName: 'demo',
    selectedAgent: undefined,
    getAgentModel: () => null,
    fileMentionsEnabled: true,
  }

  it('shows default placeholder in normal mode', () => {
    render(<ChatInput {...baseProps} />)
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Send a message')
  })

  it('shows plan placeholder in plan mode', () => {
    render(<ChatInput {...baseProps} planModeEnabled={true} />)
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Describe your plan')
  })

  it('does not render the project helper footer', () => {
    render(<ChatInput {...baseProps} />)
    expect(screen.queryByText(/Working in:\s*demo/i)).toBeNull()
  })

  it('does not render helper guidance or shortcut pills', () => {
    const { container } = render(<ChatInput {...baseProps} />)
    expect(screen.queryByText(/Cmd\+Enter to send/i)).toBeNull()
    expect(screen.queryByText('/agent prompt')).toBeNull()

    const helperSection = container.querySelector('[data-testid="chat-input-helper"]')
    expect(helperSection).toBeNull()
  })

  it('keeps the normal placeholder stable regardless of default agent label', () => {
    const { rerender } = render(<ChatInput {...(baseProps as any)} defaultAgentLabel="Codex" />)
    const input = screen.getByRole('textbox')
    expect(input).toHaveAttribute('placeholder', 'Send a message')
    rerender(<ChatInput {...(baseProps as any)} defaultAgentLabel="Ollama" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Send a message')
  })
})
