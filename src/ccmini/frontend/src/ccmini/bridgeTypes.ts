import { randomUUID } from 'crypto'

export type CcminiTransportMode = 'ws' | 'polling'

export type CcminiRemoteContent =
  | string
  | Array<{
      type: string
      text?: string
      [key: string]: unknown
    }>

export type CcminiConnectConfig = {
  baseUrl: string
  authToken: string
  sessionId: string
  mode?: CcminiTransportMode
  pollIntervalMs?: number
}

export type CcminiBridgeMessageType =
  | 'query'
  | 'events'
  | 'response'
  | 'submit_tool_results'
  | 'error'
  | 'heartbeat'

export type CcminiBridgeMessage = {
  type: CcminiBridgeMessageType
  payload: Record<string, unknown>
  session_id?: string
  timestamp?: number
  request_id?: string
  sequence_num?: number
}

export type CcminiBridgeEventRecord = {
  sequence_num: number
  type: string
  payload: Record<string, unknown>
  timestamp?: number
  request_id?: string
}

export type CcminiToolResultInput = {
  tool_use_id: string
  content: string
  is_error?: boolean
}

export type CcminiPendingToolCall = {
  toolName: string
  toolUseId: string
  description: string
  toolInput?: Record<string, unknown>
}

export type CcminiPendingToolRequest = {
  runId: string
  calls: CcminiPendingToolCall[]
}

export type CcminiPromptSuggestionState = {
  text: string
  shownAt: number
  acceptedAt: number
}

export type CcminiSpeculationBoundary = {
  type: string
  toolName: string
  detail: string
  filePath: string
  completedAt: number
}

export type CcminiSpeculationState = {
  status: string
  suggestion: string
  reply: string
  startedAt: number
  completedAt: number
  error: string
  boundary: CcminiSpeculationBoundary
}

export type CcminiTaskBoardTaskStatus = 'pending' | 'in_progress' | 'completed'

export type CcminiTaskBoardTask = {
  id: string
  subject: string
  description: string
  activeForm?: string
  owner?: string
  ownerIsActive?: boolean
  status: CcminiTaskBoardTaskStatus
  blocks?: string[]
  blockedBy?: string[]
  metadata?: Record<string, unknown>
}

export function createCcminiRequestId(): string {
  return randomUUID().replace(/-/g, '').slice(0, 12)
}

export function encodeCcminiBridgeMessage(
  message: CcminiBridgeMessage,
): string {
  return JSON.stringify(message)
}

export function decodeCcminiBridgeMessage(raw: string): CcminiBridgeMessage {
  const parsed = JSON.parse(raw) as CcminiBridgeMessage
  return {
    type: parsed.type,
    payload: parsed.payload ?? {},
    session_id: parsed.session_id,
    timestamp: parsed.timestamp,
    request_id: parsed.request_id,
    sequence_num: parsed.sequence_num,
  }
}
