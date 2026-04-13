import type {
  CcminiControlRequest,
  CcminiPendingToolRequest,
  CcminiPromptSuggestionState,
  CcminiSpeculationState,
} from './bridgeTypes.js'
import { normalizePendingToolCalls } from './replHelpers.js'

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {}
}

export function parsePromptSuggestionState(
  payload: Record<string, unknown> | undefined,
): CcminiPromptSuggestionState {
  return {
    text: String(payload?.text ?? ''),
    shownAt: Number(payload?.shown_at ?? 0),
    acceptedAt: Number(payload?.accepted_at ?? 0),
  }
}

export function parseSpeculationState(
  payload: Record<string, unknown> | undefined,
): CcminiSpeculationState {
  const boundary = asRecord(payload?.boundary)
  return {
    status: String(payload?.status ?? 'idle'),
    suggestion: String(payload?.suggestion ?? ''),
    reply: String(payload?.reply ?? ''),
    startedAt: Number(payload?.started_at ?? 0),
    completedAt: Number(payload?.completed_at ?? 0),
    error: String(payload?.error ?? ''),
    boundary: {
      type: String(boundary.type ?? ''),
      toolName: String(boundary.tool_name ?? ''),
      detail: String(boundary.detail ?? ''),
      filePath: String(boundary.file_path ?? ''),
      completedAt: Number(boundary.completed_at ?? 0),
    },
  }
}

export function getPendingToolRequestFromPayload(
  payload: Record<string, unknown> | undefined,
): CcminiPendingToolRequest | null {
  const rawCalls = Array.isArray(payload?.calls)
    ? (payload?.calls as Array<Record<string, unknown>>)
    : []
  const calls = normalizePendingToolCalls(rawCalls)
  if (calls.length === 0) {
    return null
  }

  return {
    runId: String(payload?.run_id ?? ''),
    calls,
  }
}

export function getControlRequestFromPayload(
  payload: Record<string, unknown> | undefined,
): CcminiControlRequest | null {
  const requestId = String(payload?.request_id ?? '').trim()
  const requestType = String(payload?.request_type ?? '').trim()
  const toolName = String(payload?.tool_name ?? '').trim()

  if (!requestId || !requestType) {
    return null
  }

  return {
    requestId,
    requestType,
    toolName,
    toolInput:
      typeof payload?.tool_input === 'object' && payload.tool_input !== null
        ? (payload.tool_input as Record<string, unknown>)
        : undefined,
    permissionMode:
      typeof payload?.permission_mode === 'string'
        ? payload.permission_mode
        : undefined,
    operationType:
      typeof payload?.operation_type === 'string'
        ? payload.operation_type
        : undefined,
    filePath:
      typeof payload?.file_path === 'string' ? payload.file_path : undefined,
    directoryPath:
      typeof payload?.directory_path === 'string'
        ? payload.directory_path
        : undefined,
    workingDirectory:
      typeof payload?.working_directory === 'string'
        ? payload.working_directory
        : undefined,
    referenceDirectories: Array.isArray(payload?.reference_directories)
      ? payload.reference_directories
          .filter((value): value is string => typeof value === 'string')
      : undefined,
  }
}

export function removePendingToolCallById(
  pendingToolRequest: CcminiPendingToolRequest | null,
  toolUseId: string,
): CcminiPendingToolRequest | null {
  if (!pendingToolRequest || !toolUseId) {
    return pendingToolRequest
  }

  const remainingCalls = pendingToolRequest.calls.filter(
    call => call.toolUseId !== toolUseId,
  )
  if (remainingCalls.length === pendingToolRequest.calls.length) {
    return pendingToolRequest
  }

  return remainingCalls.length > 0
    ? {
        ...pendingToolRequest,
        calls: remainingCalls,
      }
    : null
}

export function shouldClearPendingToolRequest(eventType: string): boolean {
  return (
    eventType === 'completion' ||
    eventType === 'error' ||
    eventType === 'executor_error'
  )
}

export function shouldStopLoadingForEvent(eventType: string): boolean {
  return (
    eventType === 'control_request' ||
    eventType === 'pending_tool_call' ||
    shouldClearPendingToolRequest(eventType)
  )
}
