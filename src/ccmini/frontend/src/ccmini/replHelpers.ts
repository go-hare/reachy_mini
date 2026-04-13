import type { CcminiPendingToolCall, CcminiSpeculationState } from './bridgeTypes.js'
import type { Message as MessageType } from '../types/message.js'
import { stringWidth } from '../ink/stringWidth.js'
import { createCcminiSystemMessage } from './messageUtils.js'
import { stringifyUnknown } from './toolRenderUtils.js'

const SPINNER_VERBS = [
  'Orchestrating',
  'Thinking',
  'Grooving',
  'Tinkering',
  'Processing',
  'Sketching',
] as const

const SPINNER_TIPS = [
  'Start with small features or bug fixes, tell Claude to propose a plan, and verify its suggested edits',
  'Hit Enter to queue up additional messages while Claude is working.',
  "Use /btw to ask a quick side question without interrupting Claude's current work.",
  'Ask Claude to create a todo list when working on complex tasks to track progress and remain on track.',
] as const

export function extractPrintableImeText(input: string): string {
  const printable = input
    .replace(/\r|\n/g, '')
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
    .trim()

  if (!printable) {
    return ''
  }

  return /[^\x00-\x7F]/.test(printable) ? printable : ''
}

export function isAppleTerminalSession(): boolean {
  const termProgram = process.env.TERM_PROGRAM?.toLowerCase()
  const bundleId = process.env.__CFBundleIdentifier?.toLowerCase()

  return (
    termProgram === 'apple_terminal' ||
    bundleId === 'com.apple.terminal'
  )
}

function truncateTextToWidth(value: string, width: number): string {
  if (width <= 0) {
    return ''
  }
  if (stringWidth(value) <= width) {
    return value
  }

  const ellipsis = '…'
  if (width <= stringWidth(ellipsis)) {
    return ellipsis
  }

  let result = ''
  for (const char of value) {
    const next = `${result}${char}`
    if (stringWidth(next) + stringWidth(ellipsis) > width) {
      break
    }
    result = next
  }

  return `${result}${ellipsis}`
}

export function padLineToWidth(left: string, right: string, width: number): string {
  const safeWidth = Math.max(24, width)
  const rightWidth = stringWidth(right)
  const minimumGap = 1

  if (rightWidth + minimumGap >= safeWidth) {
    return truncateTextToWidth(right, safeWidth)
  }

  const leftBudget = safeWidth - rightWidth - minimumGap
  const fittedLeft = truncateTextToWidth(left, leftBudget)
  const gap = Math.max(minimumGap, safeWidth - stringWidth(fittedLeft) - rightWidth)
  return `${fittedLeft}${' '.repeat(gap)}${right}`
}

export function getMacroVersion(): string {
  const macro = globalThis as typeof globalThis & {
    MACRO?: { VERSION?: string }
  }
  return macro.MACRO?.VERSION ?? '0.0.0'
}

export function appendSystemMessageOnce(
  prev: MessageType[],
  content: string,
  level: 'info' | 'warning' | 'error' = 'info',
): MessageType[] {
  if (
    prev.some(
      message =>
        message.type === 'system' && String(message.content ?? '') === content,
    )
  ) {
    return prev
  }
  return [...prev, createCcminiSystemMessage(content, level)]
}

export function summarizeToolCall(call: CcminiPendingToolCall): string[] {
  const lines = [call.description]
  if (call.toolInput && Object.keys(call.toolInput).length > 0) {
    lines.push(stringifyUnknown(call.toolInput))
  }
  return lines
}

export function normalizePendingToolCalls(
  calls: Array<Record<string, unknown>>,
): CcminiPendingToolCall[] {
  const next: CcminiPendingToolCall[] = []
  for (const call of calls) {
    const toolUseId = String(call.tool_use_id ?? '')
    if (!toolUseId) {
      continue
    }
    const toolName = String(call.tool_name ?? 'unknown')
    next.push({
      toolName,
      toolUseId,
      description: `Provide a client-side result for ${toolName}`,
      toolInput:
        typeof call.tool_input === 'object' && call.tool_input !== null
          ? (call.tool_input as Record<string, unknown>)
          : undefined,
    })
  }
  return next
}

export function describeSpeculationStatus(
  speculation: CcminiSpeculationState,
  inputValue: string,
): string {
  if (speculation.status === 'ready') {
    if (
      inputValue.trim() === speculation.suggestion.trim() &&
      speculation.suggestion
    ) {
      return 'Enter will use the prefetched reply.'
    }
    return 'Prefetched reply is ready.'
  }
  if (speculation.status === 'running') {
    return 'Prefetching the likely next reply...'
  }
  if (speculation.status === 'blocked') {
    const toolName = speculation.boundary.toolName || 'a tool'
    return `Prefetch paused at ${toolName}.`
  }
  if (speculation.status === 'error' && speculation.error) {
    return 'Prefetch failed; the normal query path is still available.'
  }
  return ''
}

export function pickSpinnerVerb(): string {
  return SPINNER_VERBS[Math.floor(Math.random() * SPINNER_VERBS.length)]!
}

export function pickSpinnerTip(messages: MessageType[]): string {
  const userTurnCount = messages.filter(message => message.type === 'user').length

  if (userTurnCount <= 1) {
    return SPINNER_TIPS[0]
  }

  return SPINNER_TIPS[userTurnCount % SPINNER_TIPS.length]!
}
