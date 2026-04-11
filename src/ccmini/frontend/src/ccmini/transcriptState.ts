import type { Message as MessageType } from '../types/message.js'
import { extractCcminiTextContent } from './messageUtils.js'
import {
  stringifyUnknown,
  summarizeToolResultText,
  summarizeToolUse,
  unwrapPersistedOutput,
} from './toolRenderUtils.js'

export type AssistantToolUseBlock = {
  type?: string
  id?: string
  name?: string
  input?: Record<string, unknown>
}

export type UserToolResultBlock = {
  type?: string
  tool_use_id?: string
  content?: unknown
  is_error?: boolean
}

export type ProgressPayload = {
  type?: string
  kind?: string
  toolName?: string
  content?: unknown
}

export type ToolUseLookupEntry = {
  name: string
  input?: Record<string, unknown>
}

export function getAssistantToolUseBlock(
  message: MessageType,
): AssistantToolUseBlock | null {
  if (message.type !== 'assistant') {
    return null
  }

  const content = message.message?.content
  if (!Array.isArray(content) || content.length === 0) {
    return null
  }

  const first = content[0] as AssistantToolUseBlock | undefined
  return first?.type === 'tool_use' ? first : null
}

export function getUserToolResultBlock(
  message: MessageType,
): UserToolResultBlock | null {
  if (message.type !== 'user') {
    return null
  }

  const content = message.message.content
  if (!Array.isArray(content) || content.length === 0) {
    return null
  }

  const first = content[0] as UserToolResultBlock | undefined
  return first?.type === 'tool_result' ? first : null
}

export function getProgressPayload(
  message: MessageType,
): ProgressPayload | null {
  if (message.type !== 'progress') {
    return null
  }

  const data = (message as MessageType & { data?: ProgressPayload }).data
  return data ?? null
}

export function buildToolUseLookup(
  messages: MessageType[],
): Map<string, ToolUseLookupEntry> {
  const lookup = new Map<string, ToolUseLookupEntry>()

  for (const message of messages) {
    const toolUse = getAssistantToolUseBlock(message)
    if (toolUse?.id && toolUse.name) {
      lookup.set(toolUse.id, {
        name: toolUse.name,
        input: toolUse.input,
      })
    }
  }

  return lookup
}

export function getMessageLines(message: MessageType): string[] {
  switch (message.type) {
    case 'user': {
      const content = message.message.content
      if (typeof content === 'string') {
        return [content]
      }
      const toolResult = getUserToolResultBlock(message)
      if (toolResult) {
        const rawText = unwrapPersistedOutput(
          stringifyUnknown(message.toolUseResult ?? toolResult.content),
        )
        const rawLines = rawText
          .replace(/\r\n/g, '\n')
          .split('\n')
          .map(line => line.replace(/\r/g, ''))

        let start = 0
        while (start < rawLines.length && !rawLines[start]?.trim()) {
          start += 1
        }

        let end = rawLines.length
        while (end > start && !rawLines[end - 1]?.trim()) {
          end -= 1
        }

        const lines = rawLines.slice(start, end)
        return lines.length > 0
          ? lines
          : [summarizeToolResultText(message.toolUseResult ?? toolResult.content)]
      }
      const text = extractCcminiTextContent(
        content as Array<{ type: string; text: string }>,
        '\n',
      )
      return [text || stringifyUnknown(content)]
    }
    case 'assistant': {
      const content = message.message?.content
      if (!Array.isArray(content)) {
        return [stringifyUnknown(content)]
      }
      const toolUse = getAssistantToolUseBlock(message)
      if (toolUse) {
        const summary = summarizeToolUse(
          toolUse.name ?? 'unknown',
          toolUse.input,
        )
        return [summary.detail ?? summary.title]
      }
      const text = extractCcminiTextContent(
        content as Array<{ type: string; text: string }>,
        '\n',
      )
      return [text || stringifyUnknown(content)]
    }
    case 'progress': {
      const data = getProgressPayload(message)
      return [String(data?.content ?? data?.kind ?? 'working...')]
    }
    case 'thinking': {
      const thinkingMessage = message as MessageType & {
        thinking?: unknown
        isRedacted?: boolean
      }
      return [
        thinkingMessage.isRedacted
          ? 'Thinking…'
          : String(thinkingMessage.thinking ?? '').trim() || 'Thinking…',
      ]
    }
    case 'system': {
      const content = (message as { content?: unknown; message?: unknown }).content
      return [String(content ?? (message as { message?: unknown }).message ?? '')]
    }
    default:
      return [stringifyUnknown(message)]
  }
}
