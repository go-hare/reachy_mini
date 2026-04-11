import type { Message } from '../types/message.js'
import { extractCcminiTextContent } from './messageUtils.js'

type BuddyReactionPayload = {
  fingerprint: string
  reaction: string | null
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function isHumanUserMessage(message: Message): boolean {
  if (message.type !== 'user' || message.isMeta || message.isCompactSummary) {
    return false
  }

  if (message.toolUseResult !== undefined) {
    return false
  }

  if ((message as Record<string, unknown>).isSynthetic === true) {
    return false
  }

  if ((message as Record<string, unknown>).isReplay === true) {
    return false
  }

  const content = message.message?.content
  if (
    Array.isArray(content) &&
    content.some(block => block?.type === 'tool_result')
  ) {
    return false
  }

  const originKind = message.origin?.kind
  if (typeof originKind === 'string' && originKind !== 'human') {
    return false
  }

  return true
}

function readUserText(message: Message): string {
  if (message.type !== 'user') {
    return ''
  }

  const content = message.message?.content
  if (typeof content === 'string') {
    return normalizeText(content)
  }

  if (Array.isArray(content)) {
    return normalizeText(
      extractCcminiTextContent(
        content as Array<{ type: string; text?: string }>,
        '\n',
      ),
    )
  }

  return ''
}

function readAssistantText(message: Message): string {
  if (message.type !== 'assistant') {
    return ''
  }

  if (message.isMeta || message.isCompactSummary) {
    return ''
  }

  const content = message.message?.content
  if (typeof content === 'string') {
    return normalizeText(content)
  }

  if (Array.isArray(content)) {
    return normalizeText(
      extractCcminiTextContent(
        content as Array<{ type: string; text?: string }>,
        '\n',
      ),
    )
  }

  return ''
}

function getLatestUserText(messages: readonly Message[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (!message || !isHumanUserMessage(message)) {
      continue
    }

    const text = readUserText(message)
    if (text) {
      return text
    }
  }

  return ''
}

function getLatestAssistantText(messages: readonly Message[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (!message) {
      continue
    }

    const text = readAssistantText(message)
    if (text) {
      return text
    }
  }

  return ''
}

function includesAny(text: string, keywords: readonly string[]): boolean {
  return keywords.some(keyword => text.includes(keyword))
}

function pickReaction(
  options: readonly string[],
  fingerprint: string,
): string | null {
  if (options.length === 0) {
    return null
  }

  let seed = 0
  for (const char of fingerprint) {
    seed = (seed * 33 + char.charCodeAt(0)) >>> 0
  }

  return options[seed % options.length] ?? null
}

function classifyReaction(
  userText: string,
  assistantText: string,
): readonly string[] {
  const combined = `${userText}\n${assistantText}`.toLowerCase()

  if (includesAny(combined, ['buddy', '宠物', '兔子'])) {
    return ['我在呢。', '收到，我继续盯着。', '小兔已在线。']
  }

  if (includesAny(combined, ['报错', '错误', 'error', '失败', '不行', 'bug'])) {
    return ['别急，我陪你看。', '这次我们把它修顺。', '我还盯着这块。']
  }

  if (includesAny(combined, ['布局', '前端', '消息区', '终端', 'ui', 'layout'])) {
    return ['这块布局我也在看。', '消息流还可以再顺。', '我盯着终端这片呢。']
  }

  if (
    includesAny(combined, ['完成', '修好', '好了', 'fixed', 'done', 'resolved'])
  ) {
    return ['这下顺眼多了。', '好耶，又推进了一步。', '这一改终于对味了。']
  }

  return ['我在旁边看着。', '继续推进，我陪着。', '这轮输出我还盯着。']
}

export function deriveBuddyReaction(
  messages: readonly Message[],
): BuddyReactionPayload {
  const userText = getLatestUserText(messages)
  const assistantText = getLatestAssistantText(messages)

  if (!assistantText) {
    return { fingerprint: '', reaction: null }
  }

  const fingerprint = `${userText}\n${assistantText}`.trim()
  if (!fingerprint) {
    return { fingerprint: '', reaction: null }
  }

  return {
    fingerprint,
    reaction: pickReaction(
      classifyReaction(userText, assistantText),
      fingerprint,
    ),
  }
}
