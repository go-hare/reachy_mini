import type { Message as MessageType } from '../types/message.js'
import {
  createCcminiAssistantMessage,
  createCcminiProgressMessage,
  createCcminiSystemMessage,
  createCcminiThinkingMessage,
  createCcminiUserMessage,
} from './messageUtils.js'
import type { CcminiBridgeEventRecord } from './bridgeTypes.js'

function getFirstToolUseId(message: MessageType): string | null {
  if (message.type !== 'assistant') {
    return null
  }
  const content = message.message?.content
  if (!Array.isArray(content) || content.length === 0) {
    return null
  }
  const first = content[0] as { type?: string; id?: string }
  if (first?.type !== 'tool_use' || typeof first.id !== 'string') {
    return null
  }
  return first.id
}

function hasAssistantToolUse(messages: MessageType[], toolUseId: string): boolean {
  return messages.some(message => getFirstToolUseId(message) === toolUseId)
}

function appendAssistantToolUse(
  messages: MessageType[],
  {
    toolUseId,
    toolName,
    toolInput,
  }: {
    toolUseId: string
    toolName: string
    toolInput: Record<string, unknown>
  },
): MessageType[] {
  if (hasAssistantToolUse(messages, toolUseId)) {
    return messages
  }
  return [
    ...messages,
    createCcminiAssistantMessage({
      content: [
        {
          type: 'tool_use',
          id: toolUseId,
          name: toolName,
          input: toolInput,
        } as never,
      ],
    }),
  ]
}

function appendUserToolResult(
  messages: MessageType[],
  {
    toolUseId,
    result,
    isError,
    metadata,
  }: {
    toolUseId: string
    result: string
    isError: boolean
    metadata: Record<string, unknown>
  },
): MessageType[] {
  return [
    ...messages,
    createCcminiUserMessage({
      content: [
        {
          type: 'tool_result',
          tool_use_id: toolUseId,
          content: result,
          is_error: isError,
        } as never,
      ],
      toolUseResult: metadata.output ?? result,
      toolUseMetadata: metadata,
    }),
  ]
}

function updateStreamingAssistant(
  messages: MessageType[],
  text: string,
): MessageType[] {
  const next = [...messages]
  for (let index = next.length - 1; index >= 0; index -= 1) {
    const candidate = next[index]
    if (candidate?.type === 'assistant' && candidate.isVirtual) {
      const previousContent =
        typeof candidate.message?.content === 'string'
          ? candidate.message.content
          : ''
      next[index] = {
        ...candidate,
        message: {
          ...(candidate.message ?? {}),
          role: 'assistant',
          content: `${previousContent}${text}`,
        },
        isVirtual: true,
      }
      return next
    }
  }
  next.push(
    createCcminiAssistantMessage({
      content: text,
      isVirtual: true,
    }),
  )
  return next
}

function finalizeAssistant(
  messages: MessageType[],
  text: string,
): MessageType[] {
  const next = [...messages]
  for (let index = next.length - 1; index >= 0; index -= 1) {
    const candidate = next[index]
    if (candidate?.type === 'assistant' && candidate.isVirtual) {
      next[index] = {
        ...candidate,
        message: {
          ...(candidate.message ?? {}),
          role: 'assistant',
          content: text,
        },
        isVirtual: false,
      }
      return next
    }
  }
  next.push(createCcminiAssistantMessage({ content: text }))
  return next
}

function appendThinkingMessage(
  messages: MessageType[],
  {
    text,
    isRedacted,
  }: {
    text: string
    isRedacted: boolean
  },
): MessageType[] {
  const next = [...messages]
  const last = next.at(-1) as
    | (MessageType & {
        type: 'thinking'
        thinking?: string
        isRedacted?: boolean
        isVirtual?: boolean
      })
    | undefined

  if (last?.type === 'thinking' && last.isVirtual) {
    next[next.length - 1] = {
      ...last,
      thinking: `${String(last.thinking ?? '')}${text}`,
      isRedacted,
    }
    return next
  }

  next.push(
    createCcminiThinkingMessage({
      thinking: text,
      isRedacted,
      isVirtual: true,
    }),
  )
  return next
}

function startThinkingMessage(
  messages: MessageType[],
  isRedacted: boolean,
): MessageType[] {
  const last = messages.at(-1) as
    | (MessageType & {
        type: 'thinking'
        isVirtual?: boolean
        isRedacted?: boolean
      })
    | undefined
  if (last?.type === 'thinking' && last.isVirtual) {
    return messages
  }
  return [
    ...messages,
    createCcminiThinkingMessage({
      thinking: '',
      isRedacted,
      isVirtual: true,
    }),
  ]
}

function endThinkingMessage(messages: MessageType[]): MessageType[] {
  const next = [...messages]
  const last = next.at(-1) as
    | (MessageType & {
        type: 'thinking'
        isVirtual?: boolean
        thinking?: string
      })
    | undefined
  if (last?.type === 'thinking' && last.isVirtual) {
    if (!String(last.thinking ?? '').trim()) {
      next.pop()
      return next
    }
    next[next.length - 1] = {
      ...last,
      isVirtual: false,
    }
  }
  return next
}

export function applyCcminiBridgeEvent(
  event: CcminiBridgeEventRecord,
  prev: MessageType[],
): MessageType[] {
  if (event.type !== 'stream_event') {
    return prev
  }

  const payload = event.payload ?? {}
  const eventType =
    typeof payload.event_type === 'string' ? payload.event_type : ''

  switch (eventType) {
    case 'request_start':
      return prev
    case 'thinking': {
      const phase = String(payload.phase ?? 'delta')
      const isRedacted = Boolean(payload.is_redacted)
      if (phase === 'start') {
        return startThinkingMessage(prev, isRedacted)
      }
      if (phase === 'end') {
        return endThinkingMessage(prev)
      }
      return appendThinkingMessage(prev, {
        text: String(payload.text ?? ''),
        isRedacted,
      })
    }
    case 'text':
      return updateStreamingAssistant(prev, String(payload.text ?? ''))
    case 'completion':
      return finalizeAssistant(prev, String(payload.text ?? ''))
    case 'error':
    case 'executor_error':
      return [
        ...prev,
        createCcminiSystemMessage(
          String(payload.error ?? 'Unknown error'),
          'error',
        ),
      ]
    case 'tool_call':
      return appendAssistantToolUse(prev, {
        toolUseId: String(payload.tool_use_id ?? ''),
        toolName: String(payload.tool_name ?? 'unknown'),
        toolInput:
          typeof payload.tool_input === 'object' && payload.tool_input !== null
            ? (payload.tool_input as Record<string, unknown>)
            : {},
      })
    case 'tool_progress':
      return [
        ...prev,
        createCcminiProgressMessage({
          toolUseID: String(payload.tool_use_id ?? ''),
          parentToolUseID: String(payload.tool_use_id ?? ''),
          data: {
            type: 'repl_tool_progress',
            kind: 'tool_progress',
            toolName: String(payload.tool_name ?? 'unknown'),
            content: String(
              payload.content ??
                `Tool progress: ${String(payload.tool_name ?? 'unknown')}`,
            ),
            ...(typeof payload.metadata === 'object' && payload.metadata !== null
              ? (payload.metadata as Record<string, unknown>)
              : {}),
          },
        }),
      ]
    case 'tool_result':
      return appendUserToolResult(prev, {
        toolUseId: String(payload.tool_use_id ?? ''),
        result: String(payload.result ?? ''),
        isError: Boolean(payload.is_error),
        metadata:
          typeof payload.metadata === 'object' && payload.metadata !== null
            ? (payload.metadata as Record<string, unknown>)
            : {},
      })
    case 'pending_tool_call': {
      let next = prev
      const calls = Array.isArray(payload.calls)
        ? (payload.calls as Array<Record<string, unknown>>)
        : []
      for (const call of calls) {
        next = appendAssistantToolUse(next, {
          toolUseId: String(call.tool_use_id ?? ''),
          toolName: String(call.tool_name ?? 'unknown'),
          toolInput:
            typeof call.tool_input === 'object' && call.tool_input !== null
              ? (call.tool_input as Record<string, unknown>)
              : {},
        })
      }
      return [
        ...next,
        createCcminiSystemMessage(
          `Waiting for external tool results (${calls.length})`,
          'info',
        ),
      ]
    }
    case 'tool_use_summary':
      return [
        ...prev,
        createCcminiSystemMessage(String(payload.summary ?? ''), 'info'),
      ]
    case 'usage':
      return [
        ...prev,
        createCcminiSystemMessage(
          `Usage: in ${String(payload.input_tokens ?? 0)}, out ${String(payload.output_tokens ?? 0)}`,
          'info',
        ),
      ]
    default:
      return prev
  }
}
