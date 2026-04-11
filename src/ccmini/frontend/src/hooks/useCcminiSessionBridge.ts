import type React from 'react'
import { useCallback, useEffect, useRef } from 'react'
import type {
  CcminiConnectConfig,
  CcminiPendingToolRequest,
  CcminiPromptSuggestionState,
  CcminiRemoteContent,
  CcminiSpeculationState,
  CcminiToolResultInput,
} from '../ccmini/bridgeTypes.js'
import { CcminiSessionManager } from '../ccmini/CcminiSessionManager.js'
import { applyCcminiBridgeEvent } from '../ccmini/ccminiMessageAdapter.js'
import { createCcminiSystemMessage } from '../ccmini/messageUtils.js'
import { appendSystemMessageOnce } from '../ccmini/replHelpers.js'
import {
  getPendingToolRequestFromPayload,
  parsePromptSuggestionState,
  parseSpeculationState,
  removePendingToolCallById,
  shouldClearPendingToolRequest,
  shouldStopLoadingForEvent,
} from '../ccmini/sessionEventState.js'
import type { Message as MessageType } from '../types/message.js'

type UseCcminiSessionBridgeOptions = {
  ccminiConnectConfig: CcminiConnectConfig
  emptyPromptSuggestionState: CcminiPromptSuggestionState
  idleSpeculationState: CcminiSpeculationState
  wasLoadingRef: React.MutableRefObject<boolean>
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>
  setPendingCcminiToolRequest: React.Dispatch<
    React.SetStateAction<CcminiPendingToolRequest | null>
  >
  setPromptSuggestion: React.Dispatch<
    React.SetStateAction<CcminiPromptSuggestionState>
  >
  setSpeculation: React.Dispatch<React.SetStateAction<CcminiSpeculationState>>
  setMessages: React.Dispatch<React.SetStateAction<MessageType[]>>
}

export function useCcminiSessionBridge({
  ccminiConnectConfig,
  emptyPromptSuggestionState,
  idleSpeculationState,
  wasLoadingRef,
  setIsLoading,
  setPendingCcminiToolRequest,
  setPromptSuggestion,
  setSpeculation,
  setMessages,
}: UseCcminiSessionBridgeOptions): {
  sendMessage: (
    content: CcminiRemoteContent,
    opts?: { uuid?: string },
  ) => Promise<boolean>
  submitToolResults: (
    runId: string,
    results: CcminiToolResultInput[],
  ) => Promise<boolean>
} {
  const managerRef = useRef<CcminiSessionManager | null>(null)

  const resetTransientState = useCallback((): void => {
    setPendingCcminiToolRequest(null)
    setPromptSuggestion(emptyPromptSuggestionState)
    setSpeculation(idleSpeculationState)
  }, [
    emptyPromptSuggestionState,
    idleSpeculationState,
    setPendingCcminiToolRequest,
    setPromptSuggestion,
    setSpeculation,
  ])

  const sendMessage = useCallback(
    async (
      content: CcminiRemoteContent,
      opts?: { uuid?: string },
    ): Promise<boolean> => {
      const manager = managerRef.current
      if (!manager) {
        return false
      }
      setIsLoading(true)
      try {
        return await manager.sendMessage(content, opts)
      } catch {
        setIsLoading(false)
        return false
      }
    },
    [setIsLoading],
  )

  const submitToolResults = useCallback(
    async (
      runId: string,
      results: CcminiToolResultInput[],
    ): Promise<boolean> => {
      const manager = managerRef.current
      if (!manager) {
        return false
      }
      setIsLoading(true)
      try {
        const ok = await manager.submitToolResults(runId, results)
        if (ok) {
          setPendingCcminiToolRequest(prev =>
            prev?.runId === runId ? null : prev,
          )
        } else {
          setIsLoading(false)
        }
        return ok
      } catch (error) {
        setIsLoading(false)
        setMessages(prev => [
          ...prev,
          createCcminiSystemMessage(
            error instanceof Error ? error.message : String(error),
            'error',
          ),
        ])
        return false
      }
    },
    [setIsLoading, setMessages, setPendingCcminiToolRequest],
  )

  useEffect(() => {
    let disposed = false

    resetTransientState()

    const manager = new CcminiSessionManager(ccminiConnectConfig, {
      onConnected: () => {
        if (disposed) {
          return
        }
        setPendingCcminiToolRequest(null)
        setMessages(prev =>
          appendSystemMessageOnce(
            prev,
            `ccmini transport connected: ${ccminiConnectConfig.baseUrl}`,
            'info',
          ),
        )
      },
      onDisconnected: () => {
        if (disposed) {
          return
        }
        const lostDuringActiveTurn = wasLoadingRef.current
        resetTransientState()
        setIsLoading(false)
        if (lostDuringActiveTurn) {
          setMessages(prev =>
            appendSystemMessageOnce(
              prev,
              'ccmini transport disconnected before completion; the final assistant reply may be missing.',
              'warning',
            ),
          )
        }
      },
      onError: error => {
        if (disposed) {
          return
        }
        resetTransientState()
        setIsLoading(false)
        setMessages(prev => appendSystemMessageOnce(prev, error.message, 'error'))
      },
      onEvent: event => {
        if (disposed || event.type !== 'stream_event') {
          if (!disposed) {
            setMessages(prev => applyCcminiBridgeEvent(event, prev))
          }
          return
        }

        const eventType = String(event.payload?.event_type ?? '')
        if (eventType === 'request_start') {
          setIsLoading(true)
        } else if (eventType === 'prompt_suggestion') {
          setPromptSuggestion(parsePromptSuggestionState(event.payload))
        } else if (eventType === 'speculation') {
          setSpeculation(parseSpeculationState(event.payload))
        }

        if (eventType === 'pending_tool_call') {
          setPendingCcminiToolRequest(
            getPendingToolRequestFromPayload(event.payload),
          )
        } else if (eventType === 'tool_result') {
          const toolUseId = String(event.payload?.tool_use_id ?? '')
          setPendingCcminiToolRequest(prev =>
            removePendingToolCallById(prev, toolUseId),
          )
        } else if (shouldClearPendingToolRequest(eventType)) {
          setPendingCcminiToolRequest(null)
        }

        if (shouldStopLoadingForEvent(eventType)) {
          setIsLoading(false)
        }

        setMessages(prev => applyCcminiBridgeEvent(event, prev))
      },
    })

    managerRef.current = manager
    void manager.connect().catch(error => {
      if (disposed) {
        return
      }
      resetTransientState()
      setIsLoading(false)
      setMessages(prev =>
        appendSystemMessageOnce(
          prev,
          error instanceof Error ? error.message : String(error),
          'error',
        ),
      )
    })

    return () => {
      disposed = true
      resetTransientState()
      manager.disconnect()
      managerRef.current = null
    }
  }, [
    ccminiConnectConfig,
    resetTransientState,
    setIsLoading,
    setMessages,
    setPendingCcminiToolRequest,
    setPromptSuggestion,
    setSpeculation,
    wasLoadingRef,
  ])

  return {
    sendMessage,
    submitToolResults,
  }
}
