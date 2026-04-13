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
  websocketUrl?: string
  authToken: string
  sessionId: string
  mode?: CcminiTransportMode
  pollIntervalMs?: number
}

export type CcminiBridgeMessageType =
  | 'query'
  | 'events'
  | 'response'
  | 'control_response'
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

export type CcminiControlDecision = 'allow' | 'deny' | 'ask'

export type CcminiControlResponse = {
  decision: CcminiControlDecision
  scope?: 'once' | 'directory'
  scopePath?: string
}

export type CcminiControlRequest = {
  requestId: string
  requestType: string
  toolName: string
  toolInput?: Record<string, unknown>
  permissionMode?: string
  operationType?: string
  filePath?: string
  directoryPath?: string
  workingDirectory?: string
  referenceDirectories?: string[]
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

export type CcminiSendStatus = 'accepted' | 'busy' | 'error'

export type CcminiSendResult = {
  ok: boolean
  status: CcminiSendStatus
  message?: string
}

export type CcminiBackgroundTaskStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'killed'

export type CcminiBackgroundTaskType =
  | 'local_agent'
  | 'remote_agent'
  | 'in_process_teammate'
  | 'local_bash'
  | 'local_workflow'
  | 'monitor_mcp'
  | 'dream'
  | string

export type CcminiBackgroundTask = {
  id: string
  type: CcminiBackgroundTaskType
  status: CcminiBackgroundTaskStatus
  description: string
  outputFile?: string
  transcriptFile?: string
  startTime?: number
  endTime?: number | null
  updatedAt?: number
  resumeCount?: number
  canResume?: boolean
  promptPreview?: string
  model?: string
  profile?: string
  workerName?: string
  teamName?: string
  backendType?: string
  isolation?: string
  agentType?: string
  subagentType?: string
  result?: string
  error?: string
  metadata?: Record<string, unknown>
}

export type CcminiTeamMember = {
  agentId: string
  name: string
  agentType?: string
  model?: string
  cwd?: string
  status?: string
  currentTask?: string
  messagesProcessed?: number
  totalTurns?: number
  error?: string
  isIdle?: boolean
  isActive?: boolean
  backendType?: string
  transcriptFile?: string
  color?: string
  planModeRequired?: boolean
  lastUpdateMs?: number
}

export type CcminiTeamState = {
  name: string
  description?: string
  leadAgentId?: string
  leadSessionId?: string
  activeCount?: number
  teammateCount?: number
  members: CcminiTeamMember[]
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
