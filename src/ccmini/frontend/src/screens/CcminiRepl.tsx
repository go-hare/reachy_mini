import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import chalk from 'chalk'
import { Ansi, Box, Text, useInput, useStdin, useStdout } from '../ink.js'
import { CCMINI_REPL_HELP } from '../ccmini/ccminiCommands.js'
import {
  AssistantRedactedThinkingMessage,
  AssistantThinkingMessage,
} from '../ccmini-thinking/index.js'
import { useTextInput } from '../hooks/useTextInput.js'
import { useDeclaredCursor } from '../ink/hooks/use-declared-cursor.js'
import {
  type CcminiConnectConfig,
  type CcminiPendingToolCall,
  type CcminiPendingToolRequest,
  type CcminiRemoteContent,
  type CcminiToolResultInput,
} from '../ccmini/bridgeTypes.js'
import { CcminiSessionManager } from '../ccmini/CcminiSessionManager.js'
import { applyCcminiBridgeEvent } from '../ccmini/ccminiMessageAdapter.js'
import { saveConfiguredTheme } from '../ccmini/loadCcminiConfig.js'
import {
  type DonorCommandCatalogEntry,
  findDonorCommand,
  getDonorCommandCatalog,
  getDonorCommandSuggestions,
} from '../ccmini/donorCommandCatalog.js'
import { getResolvedThemeSetting, getThemeTokens } from '../ccmini/themePalette.js'
import { THEME_OPTIONS, type ThemeSetting } from '../ccmini/themeTypes.js'
import type { Message as MessageType } from '../types/message.js'
import {
  createCcminiSystemMessage,
  createCcminiUserMessage,
  extractCcminiTextContent,
} from '../ccmini/messageUtils.js'

type Props = {
  ccminiConnectConfig: CcminiConnectConfig
  initialMessages?: MessageType[]
  initialThemeSetting?: ThemeSetting
  onExit: () => void | Promise<void>
}

type CcminiConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'disconnected'

type SavedToolResult = {
  content: string
  isError: boolean
}

type RecentImeCandidate = {
  text: string
  at: number
}

const WELCOME_WIDTH = 58
const DEFAULT_INPUT_PLACEHOLDER = 'Try "fix lint errors"'
const WELCOME_DIVIDER = '…'.repeat(WELCOME_WIDTH)
const DONOR_POINTER = '❯'
const COMMAND_PANEL_VISIBLE_COUNT = 8
const FRONTEND_LOCAL_COMMAND_NAMES = new Set([
  'commands',
  'exit',
  'help',
  'quit',
  'theme',
])
const BACKEND_PASSTHROUGH_COMMAND_NAMES = new Set([
  'agents',
  'brief',
  'buddy',
  'clear',
  'compact',
  'config',
  'context',
  'cost',
  'doctor',
  'feedback',
  'files',
  'help',
  'hooks',
  'keybindings',
  'login',
  'logout',
  'memory',
  'mcp',
  'model',
  'output-style',
  'permissions',
  'plan',
  'plugin',
  'rename',
  'review',
  'rewind',
  'session',
  'skills',
  'stats',
  'status',
  'statusline',
  'tasks',
  'terminal-setup',
  'theme',
  'usage',
  'version',
  'voice',
])

function isFrontendLocalCommandName(name: string): boolean {
  return FRONTEND_LOCAL_COMMAND_NAMES.has(name)
}

function isBackendPassthroughCommandName(name: string): boolean {
  return BACKEND_PASSTHROUGH_COMMAND_NAMES.has(name)
}

function getCommandAutocompleteValue(
  entry: DonorCommandCatalogEntry,
): string {
  return `/${entry.name}${entry.argumentHint ? ' ' : ''}`
}

function getCommandStatusLabel(
  entry: DonorCommandCatalogEntry,
): string {
  if (isFrontendLocalCommandName(entry.name)) {
    return 'native'
  }
  if (isBackendPassthroughCommandName(entry.name)) {
    return 'backend'
  }
  return 'source'
}

function describeDonorCommand(
  entry: DonorCommandCatalogEntry,
): string[] {
  const lines = [
    `/${entry.name} - ${entry.description}`,
    `Source: ${entry.sourcePath}`,
  ]

  if (entry.aliases.length > 0) {
    lines.push(`Aliases: ${entry.aliases.map(alias => `/${alias}`).join(', ')}`)
  }

  if (entry.argumentHint) {
    lines.push(`Arguments: ${entry.argumentHint}`)
  }

  lines.push(
    isFrontendLocalCommandName(entry.name)
      ? 'Status: wired in the current ccmini frontend.'
      : isBackendPassthroughCommandName(entry.name)
        ? 'Status: forwarded to the current ccmini backend builtin/prompt command runtime.'
        : 'Status: extracted from donor source. Current ccmini frontend exposes metadata, not full donor runtime execution.',
  )

  return lines
}

function getVisibleCommandWindowStart(
  total: number,
  visibleCount: number,
  selectedIndex: number,
): number {
  if (total <= visibleCount) {
    return 0
  }

  const half = Math.floor(visibleCount / 2)
  const maxStart = total - visibleCount
  return Math.max(0, Math.min(selectedIndex - half, maxStart))
}

function applyForeground(text: string, color: string): string {
  if (color.startsWith('rgb(')) {
    const match = color.match(/rgb\(\s?(\d+),\s?(\d+),\s?(\d+)\s?\)/)
    if (match) {
      return chalk.rgb(
        Number.parseInt(match[1]!, 10),
        Number.parseInt(match[2]!, 10),
        Number.parseInt(match[3]!, 10),
      )(text)
    }
  }

  if (color.startsWith('ansi:')) {
    const name = color.slice('ansi:'.length)
    const fn = (chalk as unknown as Record<string, (value: string) => string>)[name]
    if (typeof fn === 'function') {
      return fn(text)
    }
  }

  return text
}

function applyBackground(text: string, color: string): string {
  if (color.startsWith('rgb(')) {
    const match = color.match(/rgb\(\s?(\d+),\s?(\d+),\s?(\d+)\s?\)/)
    if (match) {
      return chalk.bgRgb(
        Number.parseInt(match[1]!, 10),
        Number.parseInt(match[2]!, 10),
        Number.parseInt(match[3]!, 10),
      )(text)
    }
  }

  if (color.startsWith('ansi:')) {
    const name = color.slice('ansi:'.length)
    const bgName = `bg${name[0]!.toUpperCase()}${name.slice(1)}`
    const fn = (chalk as unknown as Record<string, (value: string) => string>)[bgName]
    if (typeof fn === 'function') {
      return fn(text)
    }
  }

  return text
}

function trimMessageLines(lines: string[], maxLines = 4): string[] {
  const compact = lines
    .flatMap(line => line.split('\n'))
    .map(line => line.trimEnd())
    .filter(line => line.length > 0)

  if (compact.length <= maxLines) {
    return compact
  }

  return [...compact.slice(0, maxLines - 1), '...']
}

function isBackspaceInput(input: string, key: { backspace?: boolean; ctrl?: boolean }): boolean {
  return key.backspace === true || input === '\x7f' || input === '\b' || (key.ctrl === true && input === 'h')
}

function isDeleteInput(input: string, key: { delete?: boolean }): boolean {
  return key.delete === true || input === '\x1b[3~'
}

function extractPrintableImeText(input: string): string {
  const printable = input
    .replace(/\r|\n/g, '')
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
    .trim()

  if (!printable) {
    return ''
  }

  return /[^\x00-\x7F]/.test(printable) ? printable : ''
}

function countDelCharacters(input: string): number {
  return (input.match(/\x7f/g) || []).length
}

function deleteBeforeCursor(
  value: string,
  cursorOffset: number,
  count = 1,
): { value: string; cursorOffset: number } {
  let nextValue = value
  let nextOffset = cursorOffset

  for (let index = 0; index < count; index += 1) {
    if (nextOffset === 0) {
      break
    }
    nextValue =
      nextValue.slice(0, nextOffset - 1) + nextValue.slice(nextOffset)
    nextOffset -= 1
  }

  return {
    value: nextValue,
    cursorOffset: nextOffset,
  }
}

function deleteAtCursor(
  value: string,
  cursorOffset: number,
  count = 1,
): { value: string; cursorOffset: number } {
  let nextValue = value

  for (let index = 0; index < count; index += 1) {
    nextValue =
      nextValue.slice(0, cursorOffset) + nextValue.slice(cursorOffset + 1)
  }

  return {
    value: nextValue,
    cursorOffset,
  }
}

function isAppleTerminalSession(): boolean {
  const termProgram = process.env.TERM_PROGRAM?.toLowerCase()
  const bundleId = process.env.__CFBundleIdentifier?.toLowerCase()

  return (
    termProgram === 'apple_terminal' ||
    bundleId === 'com.apple.terminal'
  )
}

function WelcomeHero({
  themeSetting,
}: {
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const resolvedTheme = getResolvedThemeSetting(themeSetting)
  const isLightTheme = [
    'light',
    'light-daltonized',
    'light-ansi',
  ].includes(resolvedTheme)
  const isAppleTerminal = isAppleTerminalSession()
  const clawd = (text: string) => applyForeground(text, theme.clawd_body)
  const clawdOnBackground = (text: string) =>
    applyBackground(applyForeground(text, theme.clawd_body), theme.clawd_background)

  if (isAppleTerminal && isLightTheme) {
    return (
      <Box flexDirection="column" width={WELCOME_WIDTH}>
        <Text>{''}</Text>
        <Text>{''}</Text>
        <Text>{''}</Text>
        <Text>{'            ░░░░░░'}</Text>
        <Text>{'    ░░░   ░░░░░░░░░░'}</Text>
        <Text>{'   ░░░░░░░░░░░░░░░░░░░'}</Text>
        <Text>{''}</Text>
        <Text>
          <Text dimColor>{'                           ░░░░'}</Text>
          {'                     ██'}
        </Text>
        <Text>
          <Text dimColor>{'                         ░░░░░░░░░░'}</Text>
          {'               ██▒▒██'}
        </Text>
        <Text>{'                                            ▒▒      ██   ▒'}</Text>
        <Text>{'                                          ▒▒░░▒▒      ▒ ▒▒'}</Text>
        <Text>
          {'      '}
          {clawd('▗ ▗     ▖ ▖')}
          {'                           ▒▒         ▒▒'}
        </Text>
        <Text>{'                                           ░          ▒'}</Text>
        <Text>{'…………………         ……………………………………………………………………░…………………………▒…………'}</Text>
      </Box>
    )
  }

  if (isAppleTerminal) {
    return (
      <Box flexDirection="column" width={WELCOME_WIDTH}>
        <Text>{''}</Text>
        <Text>{'     *                                       █████▓▓░'}</Text>
        <Text>{'                                 *         ███▓░     ░░'}</Text>
        <Text>{'            ░░░░░░                        ███▓░'}</Text>
        <Text>{'    ░░░   ░░░░░░░░░░                      ███▓░'}</Text>
        <Text>
          {'   ░░░░░░░░░░░░░░░░░░░    '}
          <Text bold>*</Text>
          {'                ██▓░░      ▓'}
        </Text>
        <Text>{'                                             ░▓▓███▓▓░'}</Text>
        <Text dimColor>{' *                                 ░░░░'}</Text>
        <Text dimColor>{'                                 ░░░░░░░░'}</Text>
        <Text dimColor>{'                               ░░░░░░░░░░░░░░░░'}</Text>
        <Text>
          {'                                                      '}
          <Text dimColor>*</Text>
        </Text>
        <Text>
          {'      '}
          {clawd('▗ ▗     ▖ ▖')}
          {'                       '}
          <Text bold>*</Text>
        </Text>
        <Text>{'                      *'}</Text>
        <Text>{'…………………         ………………………………………………………………………………………………………………'}</Text>
      </Box>
    )
  }

  if (isLightTheme) {
    return (
      <Box flexDirection="column" width={WELCOME_WIDTH}>
        <Text>{''}</Text>
        <Text>{''}</Text>
        <Text>{''}</Text>
        <Text>{'            ░░░░░░'}</Text>
        <Text>{'    ░░░   ░░░░░░░░░░'}</Text>
        <Text>{'   ░░░░░░░░░░░░░░░░░░░'}</Text>
        <Text>{''}</Text>
        <Text>
          <Text dimColor>{'                           ░░░░'}</Text>
          {'                     ██'}
        </Text>
        <Text>
          <Text dimColor>{'                         ░░░░░░░░░░'}</Text>
          {'               ██▒▒██'}
        </Text>
        <Text>{'                                            ▒▒      ██   ▒'}</Text>
        <Text>
          {'       '}
          {clawd('█████████')}
          {'                          ▒▒░░▒▒      ▒ ▒▒'}
        </Text>
        <Text>
          {'      '}
          {clawdOnBackground('██▄█████▄██')}
          {'                           ▒▒         ▒▒'}
        </Text>
        <Text>
          {'       '}
          {clawd('█████████')}
          {'                           ░          ▒'}
        </Text>
        <Text>
          {'…………………'}
          {clawd('█ █   █ █')}
          {'……………………………………………………………………░…………………………▒…………'}
        </Text>
      </Box>
    )
  }

  return (
    <Box flexDirection="column" width={WELCOME_WIDTH}>
      <Text>{''}</Text>
      <Text>
        {'     *                                       '}
        {clawd('█████▓▓░')}
      </Text>
      <Text>
        {'                                 *         '}
        {clawd('███▓░')}
        {'     ░░'}
      </Text>
      <Text>
        {'            ░░░░░░                        '}
        {clawd('███▓░')}
      </Text>
      <Text>
        {'    ░░░   ░░░░░░░░░░                      '}
        {clawd('███▓░')}
      </Text>
      <Text>
        {'   ░░░░░░░░░░░░░░░░░░░    '}
        <Text bold>*</Text>
        {'                '}
        {clawd('██▓░░')}
        {'      ▓'}
      </Text>
      <Text>{'                                             ░▓▓███▓▓░'}</Text>
      <Text dimColor>{' *                                 ░░░░'}</Text>
      <Text dimColor>{'                                 ░░░░░░░░'}</Text>
      <Text dimColor>{'                               ░░░░░░░░░░░░░░░░'}</Text>
      <Text>
        {'       '}
        {clawd('█████████')}
        {'                                        '}
        <Text dimColor>*</Text>
      </Text>
      <Text>
        {'      '}
        {clawd('██▄█████▄██')}
        {'                        '}
        <Text bold>*</Text>
      </Text>
      <Text>
        {'       '}
        {clawd('█████████')}
        {'      '}
        <Text bold>*</Text>
      </Text>
      <Text>
        {'…………………'}
        {clawd('█ █   █ █')}
        {'………………………………………………………………………………………………………………'}
      </Text>
    </Box>
  )
}

function MessageResponseFlow({
  children,
}: {
  children: React.ReactNode
}): React.ReactNode {
  return (
    <Box flexDirection="row" width="100%">
      <Box minWidth={4}>
        <Text dimColor>{'  ⎿  '}</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1} flexShrink={1}>
        {children}
      </Box>
    </Box>
  )
}

function AssistantFlow({
  lines,
  width,
}: {
  lines: string[]
  width: number
}): React.ReactNode {
  const content = trimMessageLines(lines, 8).join('\n')
  return (
    <Box flexDirection="row" width="100%">
      <Box minWidth={2}>
        <Text>{'●'}</Text>
      </Box>
      <Box flexDirection="column" width={width}>
        <Text wrap="wrap">{content}</Text>
      </Box>
    </Box>
  )
}

function SystemFlow({
  content,
  addMargin,
  dot,
  color,
  dimColor,
  width,
}: {
  content: string
  addMargin: boolean
  dot: boolean
  color?: 'yellow' | 'red'
  dimColor: boolean
  width: number
}): React.ReactNode {
  return (
    <Box flexDirection="row" marginTop={addMargin ? 1 : 0} width="100%">
      {dot ? (
        <Box minWidth={2}>
          <Text color={color} dimColor={dimColor}>
            {'●'}
          </Text>
        </Box>
      ) : null}
      <Box flexDirection="column" width={width}>
        <Text color={color} dimColor={dimColor} wrap="wrap">
          {content.trim()}
        </Text>
      </Box>
    </Box>
  )
}

function UserPromptFlow({
  content,
  addMargin,
  themeSetting,
}: {
  content: string
  addMargin: boolean
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const lines = content.split('\n')
  return (
    <Box flexDirection="column" marginTop={addMargin ? 1 : 0} paddingRight={1}>
      {lines.map((line, index) => (
        <Text key={index}>
          {applyBackground(
            `${index === 0 ? `${DONOR_POINTER} ` : '  '}${line || ' '}`,
            theme.userMessageBackground,
          )}
        </Text>
      ))}
    </Box>
  )
}

function getMacroVersion(): string {
  const macro = globalThis as typeof globalThis & {
    MACRO?: { VERSION?: string }
  }
  return macro.MACRO?.VERSION ?? '0.0.0'
}

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function appendSystemMessageOnce(
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

function summarizeToolCall(call: CcminiPendingToolCall): string[] {
  const lines = [call.description]
  if (call.toolInput && Object.keys(call.toolInput).length > 0) {
    lines.push(stringifyUnknown(call.toolInput))
  }
  return lines
}

function normalizePendingToolCalls(
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

function summarizeInbox(
  inbox: Record<string, Array<Record<string, unknown>>>,
): string[] {
  const lines: string[] = []

  const pushRows = inbox.push_notifications
  if (pushRows?.length) {
    const last = pushRows[pushRows.length - 1]!
    const title = String(last.title ?? '').trim()
    const body = String(last.body ?? '').trim().slice(0, 72)
    lines.push(`push: ${title}${body ? ` - ${body}` : ''}`)
  }

  const fileRows = inbox.file_deliveries
  if (fileRows?.length) {
    const last = fileRows[fileRows.length - 1]!
    const path = String(last.source_path ?? '')
    const base = path.split(/[/\\]/).pop() ?? path
    lines.push(`file: ${base}`)
  }

  const prRows = inbox.subscribe_pr
  if (prRows?.length) {
    const last = prRows[prRows.length - 1]!
    lines.push(`pr: ${String(last.repository ?? '')}`)
  }

  return lines.slice(-4)
}

function parseDraftResult(value: string): SavedToolResult {
  const trimmed = value.trim()
  const isError = trimmed.startsWith('error:')
  return {
    content: isError ? trimmed.slice('error:'.length).trim() : trimmed,
    isError,
  }
}

function findNextIncompleteIndex(
  results: Array<SavedToolResult | null>,
  startIndex: number,
): number {
  for (let offset = 1; offset <= results.length; offset += 1) {
    const nextIndex = (startIndex + offset) % results.length
    if (!results[nextIndex]) {
      return nextIndex
    }
  }
  return startIndex
}

function getMessageLines(message: MessageType): string[] {
  switch (message.type) {
    case 'user': {
      const content = message.message.content
      if (typeof content === 'string') {
        return [content]
      }
      const first = content[0] as
        | { type?: string; tool_use_id?: string; content?: string; is_error?: boolean }
        | undefined
      if (first?.type === 'tool_result') {
        return [
          `${first.is_error ? 'error result' : 'tool result'} for ${first.tool_use_id ?? 'unknown'}`,
          String(first.content ?? ''),
        ]
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
      const first = content[0] as
        | { type?: string; name?: string; input?: Record<string, unknown> }
        | undefined
      if (first?.type === 'tool_use') {
        return [
          `tool: ${first.name ?? 'unknown'}`,
          stringifyUnknown(first.input ?? {}),
        ]
      }
      const text = extractCcminiTextContent(
        content as Array<{ type: string; text: string }>,
        '\n',
      )
      return [text || stringifyUnknown(content)]
    }
    case 'progress': {
      const data = (message as { data?: { content?: unknown; kind?: unknown } }).data
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

function renderInputLine(inputValue: string, cursorOffset: number): React.ReactNode {
  if (inputValue.length === 0) {
    return (
      <Text>
        <Text inverse>{DEFAULT_INPUT_PLACEHOLDER[0] ?? ' '}</Text>
        <Text dimColor>{DEFAULT_INPUT_PLACEHOLDER.slice(1)}</Text>
      </Text>
    )
  }

  const safeOffset = Math.max(0, Math.min(cursorOffset, inputValue.length))
  const before = inputValue.slice(0, safeOffset)
  const current = inputValue[safeOffset] ?? ' '
  const after = inputValue.slice(safeOffset + (safeOffset < inputValue.length ? 1 : 0))

  return (
    <Text>
      {before}
      <Text inverse>{current}</Text>
      {after}
    </Text>
  )
}

function MainInputLine({
  inputValue,
  renderedValue,
  cursorLine,
  cursorColumn,
}: {
  inputValue: string
  renderedValue: string
  cursorLine: number
  cursorColumn: number
}): React.ReactNode {
  const cursorRef = useDeclaredCursor({
    line: cursorLine,
    column: cursorColumn,
    active: true,
  })

  return (
    <Box ref={cursorRef}>
      {inputValue.length === 0 ? (
        renderInputLine(inputValue, 0)
      ) : (
        <Text wrap="truncate-end">
          <Ansi>{renderedValue}</Ansi>
        </Text>
      )}
    </Box>
  )
}

function PromptFooter(): React.ReactNode {
  return (
    <Box paddingLeft={2} marginTop={1}>
      <Text dimColor>? for shortcuts</Text>
    </Box>
  )
}

function PromptHelpMenu(): React.ReactNode {
  return (
    <Box flexDirection="row" gap={4} paddingX={2} marginTop={1}>
      <Box flexDirection="column" width={24}>
        <Text dimColor>/ for commands</Text>
        <Text dimColor>@ for file paths</Text>
        <Text dimColor>& for background</Text>
        <Text dimColor>/btw for side question</Text>
        <Text dimColor>/theme for text style</Text>
      </Box>
      <Box flexDirection="column" width={35}>
        <Text dimColor>double tap esc to clear input</Text>
        <Text dimColor>shift + tab to auto-accept edits</Text>
        <Text dimColor>ctrl + o for verbose output</Text>
        <Text dimColor>ctrl + t to toggle tasks</Text>
        <Text dimColor>shift + ⏎ for newline</Text>
      </Box>
    </Box>
  )
}

function CommandCatalogPanel({
  entries,
  selectedIndex,
  query,
}: {
  entries: DonorCommandCatalogEntry[]
  selectedIndex: number
  query: string
}): React.ReactNode {
  const windowStart = getVisibleCommandWindowStart(
    entries.length,
    COMMAND_PANEL_VISIBLE_COUNT,
    selectedIndex,
  )
  const visibleEntries = entries.slice(
    windowStart,
    windowStart + COMMAND_PANEL_VISIBLE_COUNT,
  )
  const selectedEntry = entries[selectedIndex]

  return (
    <Box flexDirection="column" gap={1} marginTop={1}>
      <Text>Commands</Text>
      <Text dimColor>
        {entries.length === 0
          ? `No extracted donor commands match "/${query}"`
          : `Showing ${entries.length} extracted donor commands for "/${query}"`}
      </Text>
      {visibleEntries.length > 0 ? (
        <Box flexDirection="column">
          {visibleEntries.map((entry, index) => {
            const actualIndex = windowStart + index
            const line = `${DONOR_POINTER} /${entry.name}${entry.argumentHint ? ` ${entry.argumentHint}` : ''}`
            return (
              <Text key={entry.sourcePath} dimColor={actualIndex !== selectedIndex}>
                {actualIndex === selectedIndex
                  ? applyForeground(line, 'ansi:cyan')
                  : `  /${entry.name}${entry.argumentHint ? ` ${entry.argumentHint}` : ''}`}
                <Text dimColor> [{getCommandStatusLabel(entry)}]</Text>
              </Text>
            )
          })}
        </Box>
      ) : null}
      {selectedEntry ? (
        <Box flexDirection="column">
          {describeDonorCommand(selectedEntry).map((line, index) => (
            <Text key={`${selectedEntry.sourcePath}-${index}`} dimColor={index > 0}>
              {line}
            </Text>
          ))}
        </Box>
      ) : null}
      <Text dimColor italic>
        Up/Down choose  Tab inserts  Enter runs current input  Esc cancels
      </Text>
    </Box>
  )
}

function ThinkingFlow({
  thinking,
  isRedacted,
  verbose,
}: {
  thinking: string
  isRedacted: boolean
  verbose: boolean
}): React.ReactNode {
  return (
    <Box flexDirection="column" width="100%">
      {isRedacted ? (
        <AssistantRedactedThinkingMessage />
      ) : (
        <AssistantThinkingMessage
          param={{
            type: 'thinking',
            thinking,
          }}
          isTranscriptMode={false}
          verbose={verbose}
        />
      )}
    </Box>
  )
}

function ThemePickerPanel({
  selectedIndex,
  previewThemeSetting,
  syntaxHighlightingDisabled,
}: {
  selectedIndex: number
  previewThemeSetting: ThemeSetting
  syntaxHighlightingDisabled: boolean
}): React.ReactNode {
  const theme = getThemeTokens(previewThemeSetting)

  return (
    <Box flexDirection="column" gap={1} marginTop={1}>
      <Text>{applyForeground('Theme', theme.permission)}</Text>
      <Box flexDirection="column">
        <Text bold>Choose the text style that looks best with your terminal</Text>
      </Box>
      <Box flexDirection="column">
        {THEME_OPTIONS.map((option, index) => (
          <Text key={option.value}>
            {index === selectedIndex
              ? applyBackground(
                  applyForeground(
                    `${DONOR_POINTER} ${option.label}`,
                    theme.inverseText,
                  ),
                  theme.permission,
                )
              : `${' '} ${option.label}`}
          </Text>
        ))}
      </Box>
      <Box flexDirection="column" width="100%">
        <Text dimColor>{'╌'.repeat(36)}</Text>
        <Text>{' function greet() {'}</Text>
        <Text color={syntaxHighlightingDisabled ? undefined : 'red'}>
          {'-  console.log("Hello, World!");'}
        </Text>
        <Text color={syntaxHighlightingDisabled ? undefined : 'green'}>
          {'+  console.log("Hello, Claude!");'}
        </Text>
        <Text>{' }'}</Text>
        <Text dimColor>{'╌'.repeat(36)}</Text>
        <Text dimColor>
          {syntaxHighlightingDisabled
            ? 'Syntax highlighting disabled (ctrl+t to enable)'
            : 'Syntax highlighting enabled (ctrl+t to disable)'}
        </Text>
      </Box>
      <Box marginTop={1}>
        <Text dimColor italic>
          Enter to select  Esc to cancel
        </Text>
      </Box>
    </Box>
  )
}

function CcminiInboxPanel({
  baseUrl,
  authToken,
}: {
  baseUrl: string
  authToken: string
}): React.ReactNode {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const root = baseUrl.replace(/\/$/, '')

    const poll = async () => {
      try {
        const response = await fetch(
          `${root}/api/kairos/inbox?limit=12&stream=all`,
          {
            headers: { Authorization: `Bearer ${authToken}` },
          },
        )
        if (!response.ok) {
          if (!cancelled) {
            setError(`HTTP ${response.status}`)
          }
          return
        }
        const payload = (await response.json()) as {
          inbox?: Record<string, Array<Record<string, unknown>>>
        }
        if (cancelled) return
        setError(null)
        setLines(summarizeInbox(payload.inbox ?? {}))
      } catch (fetchError) {
        if (!cancelled) {
          setError(
            fetchError instanceof Error
              ? fetchError.message
              : 'inbox fetch failed',
          )
        }
      }
    }

    void poll()
    const intervalId = setInterval(poll, 8000)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [authToken, baseUrl])

  if (error && lines.length === 0) {
    return (
      <MessageResponseFlow>
        <Text dimColor>inbox: {error}</Text>
      </MessageResponseFlow>
    )
  }

  if (lines.length === 0 && !error) {
    return null
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      {error ? (
        <SystemFlow
          content={error}
          addMargin={false}
          dot
          color="red"
          dimColor={false}
          width={72}
        />
      ) : null}
      {lines.map((line, index) => (
        <MessageResponseFlow key={index}>
          <Text dimColor>{line}</Text>
        </MessageResponseFlow>
      ))}
    </Box>
  )
}

function CcminiPendingToolRequestPanel({
  runId,
  toolName,
  description,
  callCount,
}: {
  runId: string
  toolName: string
  description: string
  callCount: number
}): React.ReactNode {
  return (
    <Box flexDirection="column" marginTop={1}>
      <SystemFlow
        content="Waiting for ccmini host continuation"
        addMargin={false}
        dot
        color="yellow"
        dimColor={false}
        width={72}
      />
      <MessageResponseFlow>
        <Text>Tool: {toolName}</Text>
      </MessageResponseFlow>
      <MessageResponseFlow>
        <Text>Action: {description}</Text>
      </MessageResponseFlow>
      <MessageResponseFlow>
        <Text dimColor>Run: {runId}</Text>
      </MessageResponseFlow>
      <MessageResponseFlow>
        <Text dimColor>The remote executor is waiting for client-side tool results.</Text>
      </MessageResponseFlow>
      {callCount > 1 ? (
        <MessageResponseFlow>
          <Text dimColor>{callCount} tool results are waiting to be submitted.</Text>
        </MessageResponseFlow>
      ) : null}
    </Box>
  )
}

function CcminiToolResultEditor({
  runId,
  calls,
  onSubmit,
  onAbort,
}: {
  runId: string
  calls: CcminiPendingToolCall[]
  onSubmit: (results: Array<{
    tool_use_id: string
    content: string
    is_error?: boolean
  }>) => void | Promise<void>
  onAbort: () => void
}): React.ReactNode {
  const { stdin } = useStdin()
  const [activeIndex, setActiveIndex] = useState(0)
  const [drafts, setDrafts] = useState(() => calls.map(() => ''))
  const [savedResults, setSavedResults] = useState<Array<SavedToolResult | null>>(
    () => calls.map(() => null),
  )
  const [cursorOffset, setCursorOffset] = useState(0)
  const skipNextDeleteRef = useRef(0)
  const draftsRef = useRef(drafts)
  const cursorOffsetRef = useRef(cursorOffset)
  const activeIndexRef = useRef(activeIndex)

  const activeCall = calls[activeIndex]
  const completedCount = useMemo(
    () => savedResults.filter(result => result !== null).length,
    [savedResults],
  )

  const saveCurrentResult = useCallback(() => {
    const parsed = parseDraftResult(drafts[activeIndex] ?? '')
    const nextResults = [...savedResults]
    nextResults[activeIndex] = parsed
    setSavedResults(nextResults)

    if (nextResults.every(result => result !== null)) {
      void onSubmit(
        nextResults.map((result, index) => ({
          tool_use_id: calls[index]!.toolUseId,
          content: result!.content,
          is_error: result!.isError || undefined,
        })),
      )
      return
    }

    const nextIndex = findNextIncompleteIndex(nextResults, activeIndex)
    setActiveIndex(nextIndex)
    setCursorOffset((drafts[nextIndex] ?? '').length)
  }, [activeIndex, calls, drafts, onSubmit, savedResults])

  useEffect(() => {
    draftsRef.current = drafts
    cursorOffsetRef.current = cursorOffset
    activeIndexRef.current = activeIndex
  }, [activeIndex, cursorOffset, drafts])

  useEffect(() => {
    const handleRawDelete = (chunk: string | Buffer): void => {
      const input = typeof chunk === 'string' ? chunk : chunk.toString('utf8')
      const delCount = countDelCharacters(input)
      if (delCount === 0) {
        return
      }

      const currentIndex = activeIndexRef.current
      const currentDraft = draftsRef.current[currentIndex] ?? ''
      const next = deleteBeforeCursor(
        currentDraft,
        cursorOffsetRef.current,
        delCount,
      )
      skipNextDeleteRef.current += delCount
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[currentIndex] = next.value
          draftsRef.current = updated
          return updated
        })
        setSavedResults(prev => {
          if (!prev[currentIndex]) return prev
          const updated = [...prev]
          updated[currentIndex] = null
          return updated
        })
      }
      cursorOffsetRef.current = next.cursorOffset
      setCursorOffset(next.cursorOffset)
    }

    stdin.on('data', handleRawDelete)
    return () => {
      stdin.off('data', handleRawDelete)
    }
  }, [stdin])

  useInput((input, key) => {
    if (key.delete && skipNextDeleteRef.current > 0) {
      skipNextDeleteRef.current -= 1
      return
    }

    const rawDelCount =
      !key.backspace && !key.delete ? countDelCharacters(input) : 0

    if (key.escape || (key.ctrl && input === 'c')) {
      onAbort()
      return
    }

    if (calls.length > 1) {
      if (key.upArrow) {
        setActiveIndex(prev => (prev === 0 ? calls.length - 1 : prev - 1))
        setCursorOffset(0)
        return
      }
      if (key.downArrow || key.tab) {
        setActiveIndex(prev => (prev + 1) % calls.length)
        setCursorOffset(0)
        return
      }
    }

    if (key.leftArrow) {
      setCursorOffset(prev => Math.max(0, prev - 1))
      return
    }
    if (key.rightArrow) {
      setCursorOffset(prev =>
        Math.min((drafts[activeIndex] ?? '').length, prev + 1),
      )
      return
    }
    if (key.home) {
      setCursorOffset(0)
      return
    }
    if (key.end) {
      setCursorOffset((drafts[activeIndex] ?? '').length)
      return
    }
    if (rawDelCount > 0) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteBeforeCursor(currentDraft, cursorOffset, rawDelCount)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) return prev
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      setCursorOffset(next.cursorOffset)
      return
    }
    if (isBackspaceInput(input, key)) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteBeforeCursor(currentDraft, cursorOffset)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) return prev
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      setCursorOffset(next.cursorOffset)
      return
    }
    if (isDeleteInput(input, key)) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteAtCursor(currentDraft, cursorOffset)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) return prev
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      return
    }
    if (key.return) {
      if (key.shift || key.meta) {
        setDrafts(prev => {
          const current = prev[activeIndex] ?? ''
          const next = [...prev]
          next[activeIndex] =
            current.slice(0, cursorOffset) + '\n' + current.slice(cursorOffset)
          return next
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) return prev
          const next = [...prev]
          next[activeIndex] = null
          return next
        })
        setCursorOffset(prev => prev + 1)
        return
      }
      saveCurrentResult()
      return
    }

    if (!input || key.ctrl || key.meta) {
      return
    }

    setDrafts(prev => {
      const current = prev[activeIndex] ?? ''
      const next = [...prev]
      next[activeIndex] =
        current.slice(0, cursorOffset) + input + current.slice(cursorOffset)
      return next
    })
    setSavedResults(prev => {
      if (!prev[activeIndex]) return prev
      const next = [...prev]
      next[activeIndex] = null
      return next
    })
    setCursorOffset(prev => prev + input.length)
  })

  if (!activeCall) {
    return null
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      <SystemFlow
        content={
          calls.length === 1
            ? `Submit result for ${activeCall.toolName}`
            : 'Submit ccmini tool results'
        }
        addMargin={false}
        dot={false}
        dimColor={false}
        width={72}
      />
      <MessageResponseFlow>
        <Text dimColor>{completedCount}/{calls.length} ready</Text>
      </MessageResponseFlow>
      <MessageResponseFlow>
        <Text dimColor>Run {runId} is waiting for client-side tool results.</Text>
      </MessageResponseFlow>

      {calls.length > 1 ? (
        <Box flexDirection="column" marginTop={1}>
          {calls.map((call, index) => {
            const status = savedResults[index]
              ? 'ready'
              : drafts[index]
                ? 'draft'
                : 'pending'
            return (
              <MessageResponseFlow key={call.toolUseId}>
                <Text bold={index === activeIndex}>
                  {index === activeIndex ? DONOR_POINTER : ' '} {call.toolName} [{status}]
                </Text>
              </MessageResponseFlow>
            )
          })}
        </Box>
      ) : null}

      <Box flexDirection="column" marginTop={1}>
        <MessageResponseFlow>
          <Text>Tool: {activeCall.toolName}</Text>
        </MessageResponseFlow>
        <MessageResponseFlow>
          <Text>Tool use: {activeCall.toolUseId}</Text>
        </MessageResponseFlow>
        {summarizeToolCall(activeCall).map((line, index) => (
          <MessageResponseFlow key={`${activeCall.toolUseId}-${index}`}>
            <Text dimColor={index > 0}>{line}</Text>
          </MessageResponseFlow>
        ))}
      </Box>

      <MessageResponseFlow>
        <Text dimColor>
          Enter saves this result. Shift+Enter inserts a newline. Prefix with{' '}
          <Text bold>error:</Text>{' '}to submit an error result. Esc cancels.
        </Text>
      </MessageResponseFlow>

      <Box flexDirection="row" marginTop={1}>
        <Text>
          {DONOR_POINTER}
          {' '}
        </Text>
        {renderInputLine(drafts[activeIndex] ?? '', cursorOffset)}
      </Box>
    </Box>
  )
}

export function CcminiRepl({
  ccminiConnectConfig,
  initialMessages = [],
  initialThemeSetting = 'dark',
  onExit,
}: Props): React.ReactNode {
  const [messages, setMessages] = useState<MessageType[]>(initialMessages)
  const [inputValue, setInputValue] = useState('')
  const [cursorOffset, setCursorOffset] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const [showPromptHelp, setShowPromptHelp] = useState(false)
  const [showCommandCatalog, setShowCommandCatalog] = useState(false)
  const [showThemePicker, setShowThemePicker] = useState(false)
  const [themeSetting, setThemeSetting] = useState<ThemeSetting>(initialThemeSetting)
  const [previewThemeSetting, setPreviewThemeSetting] =
    useState<ThemeSetting | null>(null)
  const [themePickerIndex, setThemePickerIndex] = useState(
    Math.max(
      0,
      THEME_OPTIONS.findIndex(option => option.value === initialThemeSetting),
    ),
  )
  const [syntaxHighlightingDisabled, setSyntaxHighlightingDisabled] =
    useState(false)
  const [showFullThinking, setShowFullThinking] = useState(false)
  const [commandCatalogIndex, setCommandCatalogIndex] = useState(0)
  const [connectionStatus, setConnectionStatus] =
    useState<CcminiConnectionStatus>('connecting')
  const [pendingCcminiToolRequest, setPendingCcminiToolRequest] =
    useState<CcminiPendingToolRequest | null>(null)
  const managerRef = useRef<CcminiSessionManager | null>(null)
  const recentImeCandidateRef = useRef<RecentImeCandidate>({
    text: '',
    at: 0,
  })
  const inputValueRef = useRef(inputValue)
  const cursorOffsetRef = useRef(cursorOffset)
  const { stdout } = useStdout()
  const rows = stdout.rows ?? 24

  const pendingCcminiCalls = pendingCcminiToolRequest?.calls ?? []
  const firstPendingCcminiToolCall = pendingCcminiCalls[0]
  const transcriptHeight = Math.max(10, rows - 18)
  const visibleMessageCount = Math.max(4, Math.floor(transcriptHeight / 4))
  const activeThemeSetting = previewThemeSetting ?? themeSetting
  const donorCommandCatalog = useMemo(() => getDonorCommandCatalog(), [])
  const trimmedInputValue = inputValue.trim()
  const donorCommandQuery = useMemo(() => {
    if (trimmedInputValue.startsWith('/')) {
      const value = trimmedInputValue.slice(1)
      return value.includes(' ') ? null : value
    }
    return showCommandCatalog ? '' : null
  }, [showCommandCatalog, trimmedInputValue])
  const donorCommandSuggestions = useMemo(() => {
    if (donorCommandQuery === null) {
      return []
    }
    return getDonorCommandSuggestions(donorCommandQuery)
  }, [donorCommandCatalog, donorCommandQuery])
  const selectedDonorCommand =
    donorCommandSuggestions[
      Math.min(commandCatalogIndex, Math.max(0, donorCommandSuggestions.length - 1))
    ] ?? null

  const sendMessage = useCallback(
    async (
      content: CcminiRemoteContent,
      opts?: { uuid?: string },
    ): Promise<boolean> => {
      const manager = managerRef.current
      if (!manager) {
        return false
      }
      setIsLoading(true)
      try {
        return await manager.sendMessage(content, opts)
      } catch {
        setIsLoading(false)
        return false
      }
    },
    [],
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
    [],
  )

  const applyMainInputState = useCallback(
    (nextValue: string, nextOffset: number): void => {
      inputValueRef.current = nextValue
      cursorOffsetRef.current = nextOffset
      setInputValue(nextValue)
      setCursorOffset(nextOffset)
    },
    [],
  )

  const setMainInputValue = useCallback((nextValue: string): void => {
    inputValueRef.current = nextValue
    setInputValue(nextValue)
  }, [])

  const setMainCursorOffset = useCallback((nextOffset: number): void => {
    cursorOffsetRef.current = nextOffset
    setCursorOffset(nextOffset)
  }, [])

  useEffect(() => {
    inputValueRef.current = inputValue
    cursorOffsetRef.current = cursorOffset
  }, [cursorOffset, inputValue])

  useEffect(() => {
    if (donorCommandSuggestions.length === 0) {
      setCommandCatalogIndex(0)
      return
    }

    setCommandCatalogIndex(prev =>
      Math.min(prev, donorCommandSuggestions.length - 1),
    )
  }, [donorCommandSuggestions])

  const openThemePicker = useCallback(() => {
    const currentIndex = Math.max(
      0,
      THEME_OPTIONS.findIndex(option => option.value === themeSetting),
    )
    setThemePickerIndex(currentIndex)
    setPreviewThemeSetting(themeSetting)
    setShowThemePicker(true)
  }, [themeSetting])

  const closeThemePicker = useCallback(() => {
    setPreviewThemeSetting(null)
    setShowThemePicker(false)
  }, [])

  const commitThemeSetting = useCallback(
    (setting: ThemeSetting) => {
      setThemeSetting(setting)
      setPreviewThemeSetting(null)
      setShowThemePicker(false)
      try {
        saveConfiguredTheme(setting)
      } catch (error) {
        setMessages(prev => [
          ...prev,
          createCcminiSystemMessage(
            error instanceof Error
              ? error.message
              : 'Failed to save theme setting.',
            'error',
          ),
        ])
      }
    },
    [],
  )

  const closeCommandCatalog = useCallback(() => {
    setShowCommandCatalog(false)
  }, [])

  const autocompleteSelectedCommand = useCallback(
    (entry: DonorCommandCatalogEntry | null): void => {
      if (!entry) {
        return
      }
      const nextValue = getCommandAutocompleteValue(entry)
      applyMainInputState(nextValue, nextValue.length)
      setShowCommandCatalog(false)
      setShowPromptHelp(false)
    },
    [applyMainInputState],
  )

  useEffect(() => {
    setConnectionStatus('connecting')
    setPendingCcminiToolRequest(null)

    const manager = new CcminiSessionManager(ccminiConnectConfig, {
      onConnected: () => {
        setConnectionStatus('connected')
        setPendingCcminiToolRequest(null)
        setMessages(prev =>
          appendSystemMessageOnce(
            prev,
            `ccmini transport connected: ${ccminiConnectConfig.baseUrl}`,
            'info',
          ),
        )
      },
      onDisconnected: () => {
        setConnectionStatus('disconnected')
        setPendingCcminiToolRequest(null)
        setIsLoading(false)
      },
      onError: error => {
        setConnectionStatus('disconnected')
        setPendingCcminiToolRequest(null)
        setIsLoading(false)
        setMessages(prev => appendSystemMessageOnce(prev, error.message, 'error'))
      },
      onEvent: event => {
        if (event.type === 'stream_event') {
          const eventType = event.payload?.event_type
          if (eventType === 'pending_tool_call') {
            const rawCalls = Array.isArray(event.payload?.calls)
              ? (event.payload.calls as Array<Record<string, unknown>>)
              : []
            const calls = normalizePendingToolCalls(rawCalls)
            setPendingCcminiToolRequest(
              calls.length > 0
                ? {
                    runId: String(event.payload?.run_id ?? ''),
                    calls,
                  }
                : null,
            )
            setIsLoading(false)
          } else if (eventType === 'tool_result') {
            const toolUseId = String(event.payload?.tool_use_id ?? '')
            if (toolUseId) {
              setPendingCcminiToolRequest(prev => {
                if (!prev) {
                  return prev
                }
                const remainingCalls = prev.calls.filter(
                  call => call.toolUseId !== toolUseId,
                )
                if (remainingCalls.length === prev.calls.length) {
                  return prev
                }
                return remainingCalls.length > 0
                  ? {
                      ...prev,
                      calls: remainingCalls,
                    }
                  : null
              })
            }
          } else if (
            eventType === 'completion' ||
            eventType === 'error' ||
            eventType === 'executor_error'
          ) {
            setPendingCcminiToolRequest(null)
          }
        }

        setMessages(prev => applyCcminiBridgeEvent(event, prev))
        if (
          event.type === 'stream_event' &&
          event.payload?.event_type === 'completion'
        ) {
          setIsLoading(false)
        }
      },
    })

    managerRef.current = manager
    void manager.connect().catch(error => {
      setConnectionStatus('disconnected')
      setPendingCcminiToolRequest(null)
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
      setConnectionStatus('disconnected')
      setPendingCcminiToolRequest(null)
      manager.disconnect()
      managerRef.current = null
    }
  }, [ccminiConnectConfig])

  const submitLocalCommand = useCallback(
    async (value: string): Promise<boolean> => {
      const normalized = value.trim()
      if (normalized === '/' || normalized === '/commands') {
        setShowPromptHelp(false)
        setShowCommandCatalog(true)
        applyMainInputState('/', 1)
        return true
      }

      if (normalized === '/help') {
        setShowPromptHelp(true)
        setShowCommandCatalog(false)
        applyMainInputState('', 0)
        return true
      }

      if (normalized.startsWith('/help ')) {
        const requestedCommand = normalized.slice('/help '.length).trim()
        const donorCommand = findDonorCommand(requestedCommand)

        if (!donorCommand) {
          return false
        }

        setShowPromptHelp(false)
        setShowCommandCatalog(false)
        applyMainInputState('', 0)
        setMessages(prev => [
          ...prev,
          createCcminiSystemMessage(
            describeDonorCommand(donorCommand).join('\n'),
            'info',
          ),
        ])
        return true
      }

      if (normalized === '/theme') {
        setShowCommandCatalog(false)
        openThemePicker()
        applyMainInputState('', 0)
        return true
      }

      if (normalized === '/exit' || normalized === '/quit') {
        await onExit()
        return true
      }

      if (normalized.startsWith('/')) {
        const slashCommandName = normalized
          .slice(1)
          .split(/\s+/, 1)[0]
          ?.toLowerCase() ?? ''
        const donorCommand = findDonorCommand(normalized)

        if (isBackendPassthroughCommandName(slashCommandName)) {
          return false
        }

        if (!donorCommand) {
          return false
        }

        setShowPromptHelp(false)
        setShowCommandCatalog(false)
        applyMainInputState('', 0)
        setMessages(prev => [
          ...prev,
          createCcminiSystemMessage(
            describeDonorCommand(donorCommand).join('\n'),
            'info',
          ),
        ])
        return true
      }

      return false
    },
    [applyMainInputState, onExit, openThemePicker],
  )

  const submitInputValue = useCallback(async (value: string): Promise<void> => {
    let normalized = value.trim()
    const recentImeCandidate = recentImeCandidateRef.current
    if (
      normalized === '?' &&
      isAppleTerminalSession() &&
      recentImeCandidate.text &&
      Date.now() - recentImeCandidate.at < 1000
    ) {
      normalized = recentImeCandidate.text
    }
    if (!normalized) {
      return
    }

    if (await submitLocalCommand(normalized)) {
      return
    }

    setShowPromptHelp(false)
    setShowCommandCatalog(false)

    const userMessage = createCcminiUserMessage({
      content: normalized,
    })
    setMessages(prev => [...prev, userMessage])
    applyMainInputState('', 0)
    recentImeCandidateRef.current = {
      text: '',
      at: 0,
    }

    const ok = await sendMessage(normalized, {
      uuid: userMessage.uuid,
    })
    if (!ok) {
      setMessages(prev => [
        ...prev,
        createCcminiSystemMessage(
          'Failed to send message to ccmini bridge.',
          'error',
        ),
      ])
      setIsLoading(false)
    }
  }, [applyMainInputState, sendMessage, submitLocalCommand])

  const textInputState = useTextInput({
    value: inputValue,
    onChange: setMainInputValue,
    onSubmit: value => {
      void submitInputValue(value)
    },
    onExit: () => {
      void onExit()
    },
    onHistoryUp: () => {},
    onHistoryDown: () => {},
    onHistoryReset: () => {},
    onClearInput: () => applyMainInputState('', 0),
    focus: true,
    multiline: false,
    cursorChar: ' ',
    invert: value => chalk.inverse(value),
    themeText: value => value,
    columns: Math.max(8, (stdout.columns ?? 100) - 4),
    disableEscapeDoublePress:
      showCommandCatalog || showThemePicker || showPromptHelp,
    externalOffset: cursorOffset,
    onOffsetChange: setMainCursorOffset,
  })

  useInput(
    (input, key) => {
      const themePickerActive =
        showThemePicker || inputValueRef.current.trim() === '/theme'
      const commandCatalogActive =
        !themePickerActive &&
        inputValueRef.current.trim() !== '/help' &&
        (
          showCommandCatalog ||
          (
            inputValueRef.current.trim().startsWith('/') &&
            !inputValueRef.current.trim().slice(1).includes(' ')
          )
        )

      if (themePickerActive) {
        if (key.ctrl && input === 'c') {
          void onExit()
          return
        }

        if (key.escape) {
          closeThemePicker()
          if (inputValueRef.current.trim() === '/theme') {
            applyMainInputState('', 0)
          }
          return
        }

        if (key.ctrl && input === 't') {
          setSyntaxHighlightingDisabled(prev => !prev)
          return
        }

        if (key.upArrow) {
          setThemePickerIndex(prev => {
            const next = prev === 0 ? THEME_OPTIONS.length - 1 : prev - 1
            setPreviewThemeSetting(THEME_OPTIONS[next]!.value)
            return next
          })
          return
        }

        if (key.downArrow || key.tab) {
          setThemePickerIndex(prev => {
            const next = (prev + 1) % THEME_OPTIONS.length
            setPreviewThemeSetting(THEME_OPTIONS[next]!.value)
            return next
          })
          return
        }

        if (key.return) {
          commitThemeSetting(THEME_OPTIONS[themePickerIndex]!.value)
          if (inputValueRef.current.trim() === '/theme') {
            applyMainInputState('', 0)
          }
          return
        }

        return
      }

      if (commandCatalogActive && donorCommandSuggestions.length > 0) {
        if (key.upArrow) {
          setCommandCatalogIndex(prev =>
            prev === 0 ? donorCommandSuggestions.length - 1 : prev - 1,
          )
          return
        }

        if (key.downArrow) {
          setCommandCatalogIndex(prev =>
            (prev + 1) % donorCommandSuggestions.length,
          )
          return
        }

        if (key.tab) {
          autocompleteSelectedCommand(selectedDonorCommand)
          return
        }
      }

      if (pendingCcminiToolRequest) {
        return
      }

      if (key.ctrl && input === 'c') {
        void onExit()
        return
      }

      if (key.ctrl && input === 'o') {
        setShowFullThinking(prev => !prev)
        return
      }

      if (key.return) {
        if (
          commandCatalogActive &&
          inputValueRef.current.trim() === '/' &&
          selectedDonorCommand
        ) {
          autocompleteSelectedCommand(selectedDonorCommand)
          return
        }

        const recentImeCandidate = recentImeCandidateRef.current
        if (
          isAppleTerminalSession() &&
          !inputValueRef.current.trim() &&
          recentImeCandidate.text &&
          Date.now() - recentImeCandidate.at < 1500
        ) {
          void submitInputValue(recentImeCandidate.text)
          return
        }
      }

      if (key.escape) {
        if (showPromptHelp) {
          setShowPromptHelp(false)
          return
        }
        if (commandCatalogActive) {
          closeCommandCatalog()
          if (inputValueRef.current.trim() === '/') {
            applyMainInputState('', 0)
          }
          return
        }
        if (inputValueRef.current.length > 0) {
          applyMainInputState('', 0)
        }
        return
      }

      if (input) {
        const imeText = extractPrintableImeText(input)
        if (imeText) {
          recentImeCandidateRef.current = {
            text: imeText,
            at: Date.now(),
          }
        }
      }

      textInputState.onInput(input, key)
    },
    { isActive: !pendingCcminiToolRequest },
  )

  const renderedMessages = useMemo(
    () =>
      messages.map((message, index) => ({
        key: message.uuid ?? `${message.type}-${index}`,
        message,
        lines: getMessageLines(message),
      })),
    [messages],
  )
  const visibleMessages = useMemo(
    () =>
      renderedMessages
        .filter(message => {
          const firstLine = message.lines[0] ?? ''
          return !(
            message.message.type === 'system' &&
            firstLine.startsWith('ccmini transport connected:')
          )
        })
        .slice(-visibleMessageCount),
    [visibleMessageCount, renderedMessages],
  )

  const showWelcome = visibleMessages.length === 0
  const columns = stdout.columns ?? 100
  const messageWidth = Math.max(20, columns - 10)
  const inputWidth = Math.max(8, columns - 4)
  const showVisiblePromptHelp =
    showPromptHelp || trimmedInputValue === '/help'
  const showVisibleThemePicker =
    showThemePicker || trimmedInputValue === '/theme'
  const showVisibleCommandCatalog =
    !showVisibleThemePicker &&
    trimmedInputValue !== '/help' &&
    (
      showCommandCatalog ||
      donorCommandQuery !== null
    )
  const themeTokens = getThemeTokens(activeThemeSetting)

  return (
    <Box flexDirection="column">
      <Box flexDirection="column" width={WELCOME_WIDTH}>
        <Text>
          Welcome to {applyForeground('ccmini frontend', themeTokens.claude)}{' '}
          <Text dimColor>ccmini-{getMacroVersion()}</Text>
        </Text>
        <Text>{WELCOME_DIVIDER}</Text>
      </Box>

      {showWelcome && connectionStatus === 'connecting' ? (
        <Box width={WELCOME_WIDTH} marginTop={1} marginBottom={1}>
          <Text color="yellow">· Checking connectivity...</Text>
        </Box>
      ) : null}

      {showWelcome ? <WelcomeHero themeSetting={activeThemeSetting} /> : null}

      {showWelcome ? (
        <React.Fragment />
      ) : (
        <Box flexDirection="column" marginTop={1}>
          {visibleMessages.map(message => (
            <Box key={message.key} flexDirection="column" marginTop={1}>
              {message.message.type === 'user' ? (
                <UserPromptFlow
                  content={trimMessageLines(message.lines, 8).join('\n')}
                  addMargin={false}
                  themeSetting={activeThemeSetting}
                />
              ) : message.message.type === 'thinking' ? (
                <ThinkingFlow
                  thinking={String(
                    (
                      message.message as MessageType & {
                        thinking?: string
                      }
                    ).thinking ?? '',
                  )}
                  isRedacted={Boolean(
                    (
                      message.message as MessageType & {
                        isRedacted?: boolean
                      }
                    ).isRedacted,
                  )}
                  verbose={showFullThinking}
                />
              ) : message.message.type === 'assistant' ? (
                <AssistantFlow
                  lines={message.lines}
                  width={messageWidth}
                />
              ) : message.message.type === 'system' ? (
                message.message.level === 'info' ? (
                  <MessageResponseFlow>
                    <Text dimColor wrap="wrap">
                      {trimMessageLines(message.lines, 8).join('\n')}
                    </Text>
                  </MessageResponseFlow>
                ) : (
                  <SystemFlow
                    content={trimMessageLines(message.lines, 8).join('\n')}
                    addMargin={false}
                    dot
                    color={message.message.level === 'error' ? 'red' : 'yellow'}
                    dimColor={false}
                    width={messageWidth}
                  />
                )
              ) : (
                <MessageResponseFlow>
                  <Text dimColor wrap="wrap">
                    {trimMessageLines(message.lines, 8).join('\n')}
                  </Text>
                </MessageResponseFlow>
              )}
            </Box>
          ))}
        </Box>
      )}

      <Box marginTop={1}>
        <CcminiInboxPanel
          baseUrl={ccminiConnectConfig.baseUrl}
          authToken={ccminiConnectConfig.authToken}
        />
      </Box>

      {pendingCcminiToolRequest && firstPendingCcminiToolCall ? (
        <CcminiPendingToolRequestPanel
          runId={pendingCcminiToolRequest.runId}
          toolName={firstPendingCcminiToolCall.toolName}
          description={firstPendingCcminiToolCall.description}
          callCount={pendingCcminiCalls.length}
        />
      ) : null}

      {showVisibleThemePicker ? (
        <ThemePickerPanel
          selectedIndex={themePickerIndex}
          previewThemeSetting={activeThemeSetting}
          syntaxHighlightingDisabled={syntaxHighlightingDisabled}
        />
      ) : null}

      {showVisibleCommandCatalog ? (
        <CommandCatalogPanel
          entries={donorCommandSuggestions}
          selectedIndex={commandCatalogIndex}
          query={donorCommandQuery ?? ''}
        />
      ) : null}

      <Box marginTop={1} flexDirection="row">
        <Text dimColor={isLoading}>{applyForeground(`${DONOR_POINTER} `, themeTokens.subtle)}</Text>
        <MainInputLine
          inputValue={inputValue}
          renderedValue={textInputState.renderedValue}
          cursorLine={textInputState.cursorLine}
          cursorColumn={textInputState.cursorColumn}
        />
      </Box>
      {showVisibleThemePicker
        ? null
        : showVisibleCommandCatalog
          ? null
          : showVisiblePromptHelp
            ? <PromptHelpMenu />
            : <PromptFooter />}

      {pendingCcminiToolRequest ? (
        <CcminiToolResultEditor
          key={pendingCcminiToolRequest.runId}
          runId={pendingCcminiToolRequest.runId}
          calls={pendingCcminiCalls}
          onSubmit={async results => {
            const ok = await submitToolResults(
              pendingCcminiToolRequest.runId,
              results,
            )
            if (ok) {
              setPendingCcminiToolRequest(null)
            }
          }}
          onAbort={() => {
            setPendingCcminiToolRequest(null)
            setIsLoading(false)
          }}
        />
      ) : null}
    </Box>
  )
}
