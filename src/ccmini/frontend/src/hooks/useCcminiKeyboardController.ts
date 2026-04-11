import type React from 'react'
import { useInput } from '../ink.js'
import type { DonorCommandCatalogEntry } from '../ccmini/donorCommandCatalog.js'
import {
  extractPrintableImeText,
  isAppleTerminalSession,
} from '../ccmini/replHelpers.js'
import {
  isCommandCatalogActive,
  isThemePickerActive,
  shouldSubmitRecentImeCandidate,
  type RecentImeCandidate,
} from '../ccmini/replInputState.js'
import { THEME_OPTIONS, type ThemeSetting } from '../ccmini/themeTypes.js'

type UseInputHandler = Parameters<typeof useInput>[0]
type TextInputHandler = (input: string, key: Parameters<UseInputHandler>[1]) => void

type UseCcminiKeyboardControllerOptions = {
  inputValueRef: React.MutableRefObject<string>
  recentImeCandidateRef: React.MutableRefObject<RecentImeCandidate>
  pendingToolRequestActive: boolean
  showThemePicker: boolean
  showCommandCatalog: boolean
  showPromptHelp: boolean
  donorCommandSuggestionsLength: number
  selectedDonorCommand: DonorCommandCatalogEntry | null
  promptSuggestionText: string
  themePickerIndex: number
  setThemePickerIndex: React.Dispatch<React.SetStateAction<number>>
  setPreviewThemeSetting: React.Dispatch<
    React.SetStateAction<ThemeSetting | null>
  >
  setSyntaxHighlightingDisabled: React.Dispatch<
    React.SetStateAction<boolean>
  >
  commitThemeSetting: (setting: ThemeSetting) => void
  closeThemePicker: () => void
  setCommandCatalogIndex: React.Dispatch<React.SetStateAction<number>>
  autocompleteSelectedCommand: (
    entry: DonorCommandCatalogEntry | null,
  ) => void
  applyMainInputState: (nextValue: string, nextOffset: number) => void
  onExit: () => void | Promise<void>
  setShowFullThinking: React.Dispatch<React.SetStateAction<boolean>>
  setShowPromptHelp: React.Dispatch<React.SetStateAction<boolean>>
  closeCommandCatalog: () => void
  submitInputValue: (value: string) => Promise<void>
  onTextInputInput: TextInputHandler
}

export function useCcminiKeyboardController({
  inputValueRef,
  recentImeCandidateRef,
  pendingToolRequestActive,
  showThemePicker,
  showCommandCatalog,
  showPromptHelp,
  donorCommandSuggestionsLength,
  selectedDonorCommand,
  promptSuggestionText,
  themePickerIndex,
  setThemePickerIndex,
  setPreviewThemeSetting,
  setSyntaxHighlightingDisabled,
  commitThemeSetting,
  closeThemePicker,
  setCommandCatalogIndex,
  autocompleteSelectedCommand,
  applyMainInputState,
  onExit,
  setShowFullThinking,
  setShowPromptHelp,
  closeCommandCatalog,
  submitInputValue,
  onTextInputInput,
}: UseCcminiKeyboardControllerOptions): void {
  const handleInput: UseInputHandler = (input, key) => {
    const currentInputValue = inputValueRef.current
    const themePickerActive = isThemePickerActive(
      showThemePicker,
      currentInputValue,
    )
    const commandCatalogActive = isCommandCatalogActive({
      inputValue: currentInputValue,
      showThemePicker,
      showCommandCatalog,
    })

    if (themePickerActive) {
      if (key.ctrl && input === 'c') {
        void onExit()
        return
      }

      if (key.escape) {
        closeThemePicker()
        if (currentInputValue.trim() === '/theme') {
          applyMainInputState('', 0)
        }
        return
      }

      if (key.ctrl && input === 't') {
        setSyntaxHighlightingDisabled(prev => !prev)
        return
      }

      if (key.upArrow) {
        setThemePickerIndex(prev => {
          const next = prev === 0 ? THEME_OPTIONS.length - 1 : prev - 1
          setPreviewThemeSetting(THEME_OPTIONS[next]!.value)
          return next
        })
        return
      }

      if (key.downArrow || key.tab) {
        setThemePickerIndex(prev => {
          const next = (prev + 1) % THEME_OPTIONS.length
          setPreviewThemeSetting(THEME_OPTIONS[next]!.value)
          return next
        })
        return
      }

      if (key.return) {
        commitThemeSetting(THEME_OPTIONS[themePickerIndex]!.value)
        if (currentInputValue.trim() === '/theme') {
          applyMainInputState('', 0)
        }
      }
      return
    }

    if (commandCatalogActive && donorCommandSuggestionsLength > 0) {
      if (key.upArrow) {
        setCommandCatalogIndex(prev =>
          prev === 0 ? donorCommandSuggestionsLength - 1 : prev - 1,
        )
        return
      }

      if (key.downArrow) {
        setCommandCatalogIndex(prev =>
          (prev + 1) % donorCommandSuggestionsLength,
        )
        return
      }

      if (key.tab) {
        autocompleteSelectedCommand(selectedDonorCommand)
        return
      }
    }

    if (
      key.tab &&
      !commandCatalogActive &&
      !showThemePicker &&
      !showPromptHelp &&
      !currentInputValue.trim() &&
      promptSuggestionText
    ) {
      applyMainInputState(promptSuggestionText, promptSuggestionText.length)
      return
    }

    if (pendingToolRequestActive) {
      return
    }

    if (key.ctrl && input === 'c') {
      void onExit()
      return
    }

    if (key.ctrl && input === 'o') {
      setShowFullThinking(prev => !prev)
      return
    }

    if (key.return) {
      if (
        commandCatalogActive &&
        currentInputValue.trim() === '/' &&
        selectedDonorCommand
      ) {
        autocompleteSelectedCommand(selectedDonorCommand)
        return
      }

      const recentImeCandidate = recentImeCandidateRef.current
      if (
        shouldSubmitRecentImeCandidate({
          inputValue: currentInputValue,
          recentImeCandidate,
          now: Date.now(),
          maxAgeMs: 1500,
          isAppleTerminal: isAppleTerminalSession(),
        })
      ) {
        void submitInputValue(recentImeCandidate.text)
        return
      }
    }

    if (key.escape) {
      if (showPromptHelp) {
        setShowPromptHelp(false)
        return
      }

      if (commandCatalogActive) {
        closeCommandCatalog()
        if (currentInputValue.trim() === '/') {
          applyMainInputState('', 0)
        }
        return
      }

      if (currentInputValue.length > 0) {
        applyMainInputState('', 0)
      }
      return
    }

    if (input) {
      const imeText = extractPrintableImeText(input)
      if (imeText) {
        recentImeCandidateRef.current = {
          text: imeText,
          at: Date.now(),
        }
      }
    }

    onTextInputInput(input, key)
  }

  useInput(handleInput, { isActive: !pendingToolRequestActive })
}
