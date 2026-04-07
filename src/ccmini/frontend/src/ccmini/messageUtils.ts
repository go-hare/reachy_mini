import { randomUUID, type UUID } from 'crypto'
import type {
  AssistantMessage,
  MessageOrigin,
  ProgressMessage,
  SystemMessage,
  SystemMessageLevel,
  ThinkingMessage,
  UserMessage,
} from '../types/message.js'

export function createCcminiUserMessage({
  content,
  uuid,
  toolUseResult,
  toolUseMetadata,
  origin,
}: {
  content: UserMessage['message']['content']
  uuid?: UUID | string
  toolUseResult?: unknown
  toolUseMetadata?: Record<string, unknown>
  origin?: MessageOrigin
}): UserMessage {
  return {
    type: 'user',
    message: {
      role: 'user',
      content,
    },
    uuid: (uuid as UUID | undefined) ?? randomUUID(),
    timestamp: new Date().toISOString(),
    toolUseResult,
    toolUseMetadata,
    origin,
  }
}

export function createCcminiAssistantMessage({
  content,
  isVirtual,
}: {
  content: unknown
  isVirtual?: true
}): AssistantMessage {
  return {
    type: 'assistant',
    message: {
      role: 'assistant',
      content,
    },
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
    isVirtual,
  }
}

export function createCcminiSystemMessage(
  content: string,
  level: SystemMessageLevel = 'info',
): SystemMessage {
  return {
    type: 'system',
    subtype: 'informational',
    content,
    level,
    isMeta: false,
    timestamp: new Date().toISOString(),
    uuid: randomUUID(),
  }
}

export function createCcminiThinkingMessage({
  thinking,
  isRedacted = false,
  isVirtual,
}: {
  thinking?: string
  isRedacted?: boolean
  isVirtual?: true
}): ThinkingMessage {
  return {
    type: 'thinking',
    thinking,
    isRedacted,
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
    isVirtual,
  }
}

export function createCcminiProgressMessage<P>({
  toolUseID,
  parentToolUseID,
  data,
}: {
  toolUseID: string
  parentToolUseID: string
  data: P
}): ProgressMessage {
  return {
    type: 'progress',
    data,
    toolUseID,
    parentToolUseID,
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
  }
}

export function extractCcminiTextContent(
  blocks: readonly { readonly type: string; readonly text?: string }[],
  separator = '',
): string {
  return blocks
    .filter(
      (block): block is { type: 'text'; text: string } =>
        block.type === 'text' && typeof block.text === 'string',
    )
    .map(block => block.text)
    .join(separator)
}
