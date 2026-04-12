import type React from 'react'
import { useCallback } from 'react'
import type {
  CcminiPromptSuggestionState,
  CcminiRemoteContent,
  CcminiSendResult,
  CcminiSpeculationState,
} from '../ccmini/bridgeTypes.js'
import { findDonorCommand } from '../ccmini/donorCommandCatalog.js'
import { describeDonorCommand } from '../ccmini/donorCommandPresentation.js'
import {
  createCcminiSystemMessage,
  createCcminiUserMessage,
} from '../ccmini/messageUtils.js'
import { isAppleTerminalSession } from '../ccmini/replHelpers.js'
import {
  resolveLocalCommandIntent,
  shouldRestoreImeQuestionInput,
  type RecentImeCandidate,
} from '../ccmini/replInputState.js'
import type { Message as MessageType } from '../types/message.js'

type UseCcminiSubmitHandlersOptions = {
  applyMainInputState: (nextValue: string, nextOffset: number) => void
  openThemePicker: () => void
  onExit: () => void | Promise<void>
  sendMessage: (
    content: CcminiRemoteContent,
    opts?: { uuid?: string },
  ) => Promise<CcminiSendResult>
  queueMessage: (
    content: CcminiRemoteContent,
    opts?: { uuid?: string },
  ) => void
  isTurnBusy: boolean
  recentImeCandidateRef: React.MutableRefObject<RecentImeCandidate>
  setShowPromptHelp: React.Dispatch<React.SetStateAction<boolean>>
  setShowCommandCatalog: React.Dispatch<React.SetStateAction<boolean>>
  setMessages: React.Dispatch<React.SetStateAction<MessageType[]>>
  setPromptSuggestion: React.Dispatch<
    React.SetStateAction<CcminiPromptSuggestionState>
  >
  emptyPromptSuggestionState: CcminiPromptSuggestionState
  setSpeculation: React.Dispatch<React.SetStateAction<CcminiSpeculationState>>
  idleSpeculationState: CcminiSpeculationState
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>
}

export function useCcminiSubmitHandlers({
  applyMainInputState,
  openThemePicker,
  onExit,
  sendMessage,
  queueMessage,
  isTurnBusy,
  recentImeCandidateRef,
  setShowPromptHelp,
  setShowCommandCatalog,
  setMessages,
  setPromptSuggestion,
  emptyPromptSuggestionState,
  setSpeculation,
  idleSpeculationState,
  setIsLoading,
}: UseCcminiSubmitHandlersOptions): {
  submitInputValue: (value: string) => Promise<void>
  submitLocalCommand: (value: string) => Promise<boolean>
} {
  const submitLocalCommand = useCallback(
    async (value: string): Promise<boolean> => {
      const localCommandIntent = resolveLocalCommandIntent(value)
      switch (localCommandIntent.type) {
        case 'open-command-catalog':
          setShowPromptHelp(false)
          setShowCommandCatalog(true)
          applyMainInputState('/', 1)
          return true

        case 'open-help':
          setShowPromptHelp(true)
          setShowCommandCatalog(false)
          applyMainInputState('', 0)
          return true

        case 'show-command-help': {
          const donorCommand = findDonorCommand(localCommandIntent.lookup)
          if (!donorCommand) {
            return false
          }

          setShowPromptHelp(false)
          setShowCommandCatalog(false)
          applyMainInputState('', 0)
          setMessages(prev => [
            ...prev,
            createCcminiSystemMessage(
              describeDonorCommand(donorCommand).join('\n'),
              'info',
            ),
          ])
          return true
        }

        case 'open-theme-picker':
          setShowCommandCatalog(false)
          openThemePicker()
          applyMainInputState('', 0)
          return true

        case 'exit':
          await onExit()
          return true

        case 'backend-passthrough':
        case 'unhandled':
          return false
      }
    },
    [
      applyMainInputState,
      onExit,
      openThemePicker,
      setMessages,
      setShowCommandCatalog,
      setShowPromptHelp,
    ],
  )

  const submitInputValue = useCallback(
    async (value: string): Promise<void> => {
      let normalized = value.trim()
      const recentImeCandidate = recentImeCandidateRef.current
      if (
        shouldRestoreImeQuestionInput({
          normalizedValue: normalized,
          recentImeCandidate,
          now: Date.now(),
          maxAgeMs: 1000,
          isAppleTerminal: isAppleTerminalSession(),
        })
      ) {
        normalized = recentImeCandidate.text
      }

      if (!normalized) {
        return
      }

      if (await submitLocalCommand(normalized)) {
        return
      }

      setShowPromptHelp(false)
      setShowCommandCatalog(false)

      const userMessage = createCcminiUserMessage({
        content: normalized,
      })
      setMessages(prev => [...prev, userMessage])
      setPromptSuggestion(emptyPromptSuggestionState)
      setSpeculation(idleSpeculationState)
      applyMainInputState('', 0)
      recentImeCandidateRef.current = {
        text: '',
        at: 0,
      }

      const sendOpts = {
        uuid: userMessage.uuid,
      }

      if (isTurnBusy) {
        queueMessage(normalized, sendOpts)
        return
      }

      const result = await sendMessage(normalized, sendOpts)
      if (result.ok) {
        return
      }

      if (result.status === 'busy') {
        queueMessage(normalized, sendOpts)
        return
      }

      setMessages(prev => [
        ...prev,
        createCcminiSystemMessage(
          result.message ?? 'Failed to send message to ccmini bridge.',
          'error',
        ),
      ])
      setIsLoading(false)
    },
    [
      applyMainInputState,
      emptyPromptSuggestionState,
      idleSpeculationState,
      isTurnBusy,
      queueMessage,
      recentImeCandidateRef,
      sendMessage,
      setIsLoading,
      setMessages,
      setPromptSuggestion,
      setShowCommandCatalog,
      setShowPromptHelp,
      setSpeculation,
      submitLocalCommand,
    ],
  )

  return {
    submitInputValue,
    submitLocalCommand,
  }
}
