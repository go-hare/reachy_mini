import type React from 'react'
import { useCallback, useEffect, useRef } from 'react'
import type {
  CcminiConnectConfig,
  CcminiControlResponse,
  CcminiControlRequest,
  CcminiPendingToolRequest,
  CcminiPromptSuggestionState,
  CcminiRemoteContent,
  CcminiSendResult,
  CcminiSpeculationState,
  CcminiToolResultInput,
} from '../ccmini/bridgeTypes.js'
import { CcminiSessionManager } from '../ccmini/CcminiSessionManager.js'
import { applyCcminiBridgeEvent } from '../ccmini/ccminiMessageAdapter.js'
import { createCcminiSystemMessage } from '../ccmini/messageUtils.js'
import { appendSystemMessageOnce } from '../ccmini/replHelpers.js'
import { requestCcminiTasksStoreRefresh } from '../ccmini/tasksStore.js'
import {
  getControlRequestFromPayload,
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
  setPendingControlRequest: React.Dispatch<
    React.SetStateAction<CcminiControlRequest | null>
  >
  setPromptSuggestion: React.Dispatch<
    React.SetStateAction<CcminiPromptSuggestionState>
  >
  setSpeculation: React.Dispatch<React.SetStateAction<CcminiSpeculationState>>
  setMessages: React.Dispatch<React.SetStateAction<MessageType[]>>
  setTransportStatus: React.Dispatch<
    React.SetStateAction<'connecting' | 'connected' | 'disconnected'>
  >
}

export function useCcminiSessionBridge({
  ccminiConnectConfig,
  emptyPromptSuggestionState,
  idleSpeculationState,
  wasLoadingRef,
  setIsLoading,
  setPendingCcminiToolRequest,
  setPendingControlRequest,
  setPromptSuggestion,
  setSpeculation,
  setMessages,
  setTransportStatus,
}: UseCcminiSessionBridgeOptions): {
  sendMessage: (
    content: CcminiRemoteContent,
    opts?: { uuid?: string },
  ) => Promise<CcminiSendResult>
  submitToolResults: (
    runId: string,
    results: CcminiToolResultInput[],
  ) => Promise<boolean>
  submitControlResponse: (
    requestId: string,
    response: CcminiControlResponse,
  ) => Promise<boolean>
} {
  const managerRef = useRef<CcminiSessionManager | null>(null)

  const resetTransientState = useCallback((): void => {
    setPendingCcminiToolRequest(null)
    setPendingControlRequest(null)
    setPromptSuggestion(emptyPromptSuggestionState)
    setSpeculation(idleSpeculationState)
  }, [
    emptyPromptSuggestionState,
    idleSpeculationState,
    setPendingCcminiToolRequest,
    setPendingControlRequest,
    setPromptSuggestion,
    setSpeculation,
  ])

  const sendMessage = useCallback(
    async (
      content: CcminiRemoteContent,
      opts?: { uuid?: string },
    ): Promise<CcminiSendResult> => {
      const manager = managerRef.current
      if (!manager) {
        return {
          ok: false,
          status: 'error',
          message: 'ccmini transport is not ready yet.',
        }
      }
      setIsLoading(true)
      requestCcminiTasksStoreRefresh(ccminiConnectConfig, 0)
      try {
        return await manager.sendMessage(content, opts)
      } catch (error) {
        setIsLoading(false)
        return {
          ok: false,
          status: 'error',
          message: error instanceof Error ? error.message : String(error),
        }
      }
    },
    [ccminiConnectConfig, setIsLoading],
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
      requestCcminiTasksStoreRefresh(ccminiConnectConfig, 0)
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
    [ccminiConnectConfig, setIsLoading, setMessages, setPendingCcminiToolRequest],
  )

  const submitControlResponse = useCallback(
    async (
      requestId: string,
      response: CcminiControlResponse,
    ): Promise<boolean> => {
      const manager = managerRef.current
      if (!manager) {
        return false
      }
      try {
        const ok = await manager.submitControlResponse(requestId, response)
        if (ok) {
          setPendingControlRequest(prev =>
            prev?.requestId === requestId ? null : prev,
          )
        }
        return ok
      } catch (error) {
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
    [setMessages, setPendingControlRequest],
  )

  useEffect(() => {
    let disposed = false

    resetTransientState()
    setTransportStatus('connecting')

    const manager = new CcminiSessionManager(ccminiConnectConfig, {
      onConnected: () => {
        if (disposed) {
          return
        }
        setTransportStatus('connected')
        setPendingCcminiToolRequest(null)
        requestCcminiTasksStoreRefresh(ccminiConnectConfig, 0)
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
        setTransportStatus('disconnected')
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
        setTransportStatus('disconnected')
        setIsLoading(false)
        setMessages(prev => appendSystemMessageOnce(prev, error.message, 'error'))
      },
      onEvent: event => {
        requestCcminiTasksStoreRefresh(ccminiConnectConfig)
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
        } else if (eventType === 'control_request') {
          setPendingControlRequest(
            getControlRequestFromPayload(event.payload),
          )
        } else if (eventType === 'control_request_resolved') {
          const resolvedId = String(event.payload?.request_id ?? '')
          setPendingControlRequest(prev =>
            prev?.requestId === resolvedId ? null : prev,
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
      setTransportStatus('disconnected')
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
      setTransportStatus('disconnected')
      manager.disconnect()
      managerRef.current = null
    }
  }, [
    ccminiConnectConfig,
    resetTransientState,
    setIsLoading,
    setMessages,
    setPendingCcminiToolRequest,
    setPendingControlRequest,
    setPromptSuggestion,
    setSpeculation,
    setTransportStatus,
    wasLoadingRef,
  ])

  return {
    sendMessage,
    submitControlResponse,
    submitToolResults,
  }
}
