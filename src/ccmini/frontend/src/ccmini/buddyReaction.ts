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

function matchesNegativeKeyword(text: string): boolean {
  return includesAny(text.toLowerCase(), [
    '不行',
    '失败',
    '报错',
    '错误',
    'bug',
    'error',
    'wrong',
    'broken',
    'stuck',
    'again',
  ])
}

function matchesKeepGoingKeyword(text: string): boolean {
  return includesAny(text.toLowerCase(), [
    '继续',
    '接着',
    'go on',
    'keep going',
    'keep',
    'carry on',
    '继续做',
  ])
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

  if (includesAny(combined, ['buddy', '/buddy'])) {
    return [
      'I am paying attention.',
      'Buddy is listening.',
      'Tiny penguin acknowledged.',
    ]
  }

  if (matchesNegativeKeyword(userText)) {
    return [
      'You have got this.',
      'Okay, deep breath.',
      'I am still cheering.',
    ]
  }

  if (matchesKeepGoingKeyword(userText)) {
    return ['Forward march.', 'Still with you.', 'Keep cooking.']
  }

  if (
    includesAny(combined, [
      '完成',
      '修好',
      '好了',
      'fixed',
      'done',
      'resolved',
      'working',
      'success',
    ])
  ) {
    return [
      'That feels like progress.',
      'Neat little win.',
      'Happy penguin noises.',
    ]
  }

  return [
    'Just vibing nearby.',
    'Watching the terminal glow.',
    'Tiny flippers crossed.',
  ]
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
