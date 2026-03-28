import React from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Send, PenLine } from 'lucide-react'

export interface AutocompleteOption {
  id: string
  label: string
  description: string
  icon?: React.ComponentType<{ className?: string }> | (() => React.ReactElement)
  category?: string
  filePath?: string
}

export interface ChatInputProps {
  inputRef: React.RefObject<HTMLInputElement | null>
  autocompleteRef: React.RefObject<HTMLDivElement | null>

  inputValue: string
  typedPlaceholder: string
  onInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  onInputSelect: (e: React.SyntheticEvent<HTMLInputElement>) => void
  onKeyDown: (e: React.KeyboardEvent) => void
  onFocus: () => void
  onBlur: () => void
  onClear: () => void
  onSend: () => void

  // Autocomplete
  showAutocomplete: boolean
  autocompleteOptions: AutocompleteOption[]
  selectedOptionIndex: number
  onSelectOption: (option: AutocompleteOption) => void

  planModeEnabled: boolean
  projectName?: string
  selectedAgent?: string
  getAgentModel: (agentName: string) => string | null

  // File mentions toggle affects autocomplete header text
  fileMentionsEnabled: boolean
  chatSendShortcut?: 'enter' | 'mod+enter'
  defaultAgentLabel?: string

  // Session controls
  onNewSession?: () => void
  showNewSession?: boolean
}

function ChatInputInner(props: ChatInputProps) {
  const {
    inputRef,
    autocompleteRef,
    inputValue,
    typedPlaceholder,
    onInputChange,
    onInputSelect,
    onKeyDown,
    onFocus,
    onBlur,
    onClear,
    onSend,
    showAutocomplete,
    autocompleteOptions,
    selectedOptionIndex,
    onSelectOption,
    planModeEnabled,
    projectName,
    fileMentionsEnabled,
    onNewSession,
    showNewSession,
  } = props

  const defaultPlaceholder = "Send a message"

  return (
    <div className="max-w-4xl mx-auto group">
      {showAutocomplete && autocompleteOptions.length > 0 && (
        <div
          ref={autocompleteRef}
          className="mb-3 bg-background border border-border rounded-lg shadow-xl max-h-48 overflow-y-auto"
        >
          <div className="p-2">
            <div className="text-xs font-medium text-muted-foreground mb-2 px-2">
              {fileMentionsEnabled ? `Files in ${projectName || 'Project'} & Capabilities` : 'Agent Capabilities'}
            </div>
            {autocompleteOptions.map((option, index) => {
              const IconComponent = option.icon
              return (
                <button
                  key={option.id}
                  onClick={() => onSelectOption(option)}
                  className={`w-full text-left p-3 rounded-md transition-colors flex items-start gap-3 ${
                    index === selectedOptionIndex
                      ? 'bg-accent text-accent-foreground'
                      : 'hover:bg-accent/50'
                  }`}
                >
                  {IconComponent && (
                    typeof IconComponent === 'function' && IconComponent.length === 0 ? (
                      <IconComponent />
                    ) : (
                      <IconComponent className="h-4 w-4 mt-0.5 flex-shrink-0" />
                    )
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm">{option.label}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{option.description}</div>
                    {option.category && (
                      <div className="text-xs text-muted-foreground/70 mt-1">{option.category}</div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        {showNewSession && onNewSession && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-10 w-10 opacity-60 hover:opacity-100 group-hover:opacity-100 transition-opacity"
                  aria-label="New chat"
                  title="New chat"
                  onClick={onNewSession}
                >
                  <PenLine className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <span>New chat</span>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
        <div className="relative flex-1">
          <Input
            ref={inputRef}
            value={inputValue}
            onChange={onInputChange}
            onSelect={onInputSelect}
            onKeyDown={onKeyDown}
            onFocus={onFocus}
            onBlur={onBlur}
            placeholder={typedPlaceholder || (
              planModeEnabled
                ? "Describe your plan"
                : defaultPlaceholder
            )}
            className="pr-12 py-2.5 text-base"
            autoComplete="off"
            disabled={false}
          />
          {inputValue && (
            <button
              onClick={onClear}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear input"
            >
              ×
            </button>
          )}
        </div>
        <Button onClick={onSend} disabled={!inputValue.trim()} size="icon" className="h-10 w-10">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

export const ChatInput = React.memo(ChatInputInner)
