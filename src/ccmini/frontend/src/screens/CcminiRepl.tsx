import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import chalk from 'chalk'
import { Ansi, Box, Text, useInput, useStdin, useStdout, useTerminalFocus } from '../ink.js'
import { stringWidth } from '../ink/stringWidth.js'
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
  type CcminiPromptSuggestionState,
  type CcminiRemoteContent,
  type CcminiSpeculationState,
  type CcminiTaskBoardTask,
  type CcminiToolResultInput,
} from '../ccmini/bridgeTypes.js'
import { CcminiSessionManager } from '../ccmini/CcminiSessionManager.js'
import { BuddyCompanion } from '../ccmini/BuddyCompanion.js'
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

type AskUserQuestionOption = {
  id: string
  label: string
  description?: string
}

type AskUserQuestion = {
  id: string
  header?: string
  prompt: string
  options: AskUserQuestionOption[]
  allowMultiple: boolean
}

type AskUserQuestionAnswer = {
  selectedOptionIds: string[]
  selectedLabels: string[]
  freeformText: string
}

type RecentImeCandidate = {
  text: string
  at: number
}

const WELCOME_WIDTH = 58
const DEFAULT_INPUT_PLACEHOLDER = 'Describe a task or type / for commands'
const WELCOME_DIVIDER = '…'.repeat(WELCOME_WIDTH)
const DONOR_POINTER = '❯'
const ASK_USER_QUESTION_ICONS = {
  tick: '✓',
  bullet: '•',
  arrowRight: '→',
  warning: '!',
} as const
const COMMAND_PANEL_VISIBLE_COUNT = 8
const TASK_PANEL_RECENT_COMPLETED_TTL_MS = 30_000
const EMPTY_PROMPT_SUGGESTION_STATE: CcminiPromptSuggestionState = {
  text: '',
  shownAt: 0,
  acceptedAt: 0,
}
const IDLE_SPECULATION_STATE: CcminiSpeculationState = {
  status: 'idle',
  suggestion: '',
  reply: '',
  startedAt: 0,
  completedAt: 0,
  error: '',
  boundary: {
    type: '',
    toolName: '',
    detail: '',
    filePath: '',
    completedAt: 0,
  },
}
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
  'Use /btw to ask a quick side question without interrupting Claude\'s current work.',
  'Ask Claude to create a todo list when working on complex tasks to track progress and remain on track.',
] as const
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

function getFrameBorderColor(
  themeSetting: ThemeSetting,
): 'ansi:red' | 'ansi:redBright' {
  return getResolvedThemeSetting(themeSetting).startsWith('light')
    ? 'ansi:red'
    : 'ansi:redBright'
}

function PanelFrame({
  title,
  subtitle,
  themeSetting,
  children,
  titleColor,
}: {
  title: string
  subtitle?: string
  themeSetting: ThemeSetting
  children: React.ReactNode
  titleColor?: string
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={getFrameBorderColor(themeSetting)}
      borderText={{
        content: `${applyForeground(` ${title} `, titleColor ?? theme.claude)}${
          subtitle ? applyForeground(` ${subtitle} `, theme.subtle) : ''
        }`,
        position: 'top',
        align: 'start',
        offset: 1,
      }}
      paddingX={1}
      paddingY={0}
      width="100%"
    >
      {children}
    </Box>
  )
}

function ClawdMascot({
  themeSetting,
}: {
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box flexDirection="column" alignItems="center">
      <Text>
        <Text color={theme.clawd_body}>{' ▐'}</Text>
        <Text color={theme.clawd_body} backgroundColor={theme.clawd_background}>
          {'▛███▜'}
        </Text>
        <Text color={theme.clawd_body}>{'▌'}</Text>
      </Text>
      <Text>
        <Text color={theme.clawd_body}>{'▝▜'}</Text>
        <Text color={theme.clawd_body} backgroundColor={theme.clawd_background}>
          {'█████'}
        </Text>
        <Text color={theme.clawd_body}>{'▛▘'}</Text>
      </Text>
      <Text color={theme.clawd_body}>{'  ▘▘ ▝▝  '}</Text>
    </Box>
  )
}

function FeedDivider({
  width,
  themeSetting,
}: {
  width: number
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  return <Text>{applyForeground('─'.repeat(Math.max(12, width)), theme.claude)}</Text>
}

function WelcomeFeedSection({
  title,
  lines,
  width,
  themeSetting,
}: {
  title: string
  lines: string[]
  width: number
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const visibleLines = lines.length > 0 ? lines : ['No recent activity']

  return (
    <Box flexDirection="column" width={width}>
      <Text bold>{applyForeground(title, theme.claude)}</Text>
      {visibleLines.map((line, index) => (
        <Text key={index} wrap="wrap">
          {line}
        </Text>
      ))}
    </Box>
  )
}

function WelcomeDashboard({
  themeSetting,
  columns,
  connectionStatus,
  baseUrl,
  donorCommandCount,
  recentActivityLines,
}: {
  themeSetting: ThemeSetting
  columns: number
  connectionStatus: CcminiConnectionStatus
  baseUrl: string
  donorCommandCount: number
  recentActivityLines: string[]
}): React.ReactNode {
  const compact = columns < 96
  const leftWidth = compact ? undefined : 28
  const rightWidth = compact ? Math.max(36, columns - 8) : Math.max(42, columns - 42)
  const statusLine = getConnectionStatusHeadline(connectionStatus)
  const modelLine = `ccmini bridge · ${statusLine.toLowerCase()}`
  const tips = [
    'Run /help to inspect available interaction shortcuts.',
    'Ask for a coding task or paste a concrete error to begin.',
    connectionStatus === 'connected'
      ? 'The bridge is ready for prompts, slash commands, and tool continuations.'
      : 'The bridge is still warming up, so sending work may need to wait.',
  ]
  const activityLines =
    recentActivityLines.length > 0 ? recentActivityLines : ['No recent activity']

  return (
    <PanelFrame
      title="ccmini frontend"
      subtitle={`v${getMacroVersion()}`}
      themeSetting={themeSetting}
    >
      <Box
        flexDirection={compact ? 'column' : 'row'}
        paddingX={1}
        paddingY={1}
        gap={1}
        alignItems={compact ? 'flex-start' : 'stretch'}
      >
        <Box
          flexDirection="column"
          width={leftWidth}
          minHeight={compact ? undefined : 11}
          alignItems={compact ? 'flex-start' : 'center'}
          justifyContent="flex-start"
        >
          <Text bold>Welcome back!</Text>
          <Box marginTop={1} marginBottom={1}>
            <ClawdMascot themeSetting={themeSetting} />
          </Box>
          <Text dimColor>{modelLine}</Text>
          <Text dimColor>{formatConnectionTarget(baseUrl)}</Text>
          <Text dimColor>{`${donorCommandCount} donor commands indexed`}</Text>
        </Box>

        {compact ? null : (
          <Box
            alignSelf="stretch"
            borderStyle="single"
            borderColor={getFrameBorderColor(themeSetting)}
            borderTop={false}
            borderBottom={false}
            borderRight={false}
            width={1}
          />
        )}

        <Box
          flexDirection="column"
          width={rightWidth}
          flexGrow={1}
          flexShrink={1}
          justifyContent="flex-start"
        >
          <WelcomeFeedSection
            title="Tips for getting started"
            lines={tips}
            width={rightWidth}
            themeSetting={themeSetting}
          />
          <FeedDivider width={rightWidth} themeSetting={themeSetting} />
          <WelcomeFeedSection
            title="Recent activity"
            lines={activityLines}
            width={rightWidth}
            themeSetting={themeSetting}
          />
        </Box>
      </Box>
    </PanelFrame>
  )
}

function CompactStatusBar({
  themeSetting,
  connectionStatus,
  baseUrl,
  donorCommandCount,
}: {
  themeSetting: ThemeSetting
  connectionStatus: CcminiConnectionStatus
  baseUrl: string
  donorCommandCount: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box
      borderTop
      borderBottom={false}
      borderLeft={false}
      borderRight={false}
      borderStyle="single"
      borderTopColor={getFrameBorderColor(themeSetting)}
      paddingLeft={1}
      marginBottom={1}
      width="100%"
    >
      <Text wrap="wrap">
        {applyForeground('ccmini frontend', theme.claude)}
        <Text dimColor>
          {` · ${getConnectionStatusHeadline(connectionStatus)} · ${formatConnectionTarget(baseUrl)} · ${donorCommandCount} donor commands`}
        </Text>
      </Text>
    </Box>
  )
}

function toPendingToolInputRecord(
  value: unknown,
): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

function createEmptyAskUserQuestionAnswer(): AskUserQuestionAnswer {
  return {
    selectedOptionIds: [],
    selectedLabels: [],
    freeformText: '',
  }
}

function getAskUserQuestionKey(question: AskUserQuestion): string {
  return question.id || question.prompt
}

function getAskUserQuestionHeader(
  question: AskUserQuestion,
  index: number,
): string {
  const rawHeader =
    typeof question.header === 'string' && question.header.trim()
      ? question.header.trim()
      : `Q${index + 1}`
  return truncateInlineText(rawHeader, 18)
}

function isAskUserQuestionPendingTool(
  call: CcminiPendingToolCall | null | undefined,
): boolean {
  return String(call?.toolName ?? '').trim().toLowerCase() === 'askuserquestion'
}

function parseAskUserQuestions(
  toolInput: Record<string, unknown> | undefined,
): AskUserQuestion[] {
  const rawQuestions = Array.isArray(toolInput?.questions)
    ? toolInput.questions
    : []

  const questions: AskUserQuestion[] = []

  for (const rawQuestion of rawQuestions) {
    const record = toPendingToolInputRecord(rawQuestion)
    if (!record) {
      continue
    }

    const prompt =
      typeof record.prompt === 'string' && record.prompt.trim()
        ? record.prompt.trim()
        : typeof record.question === 'string' && record.question.trim()
          ? record.question.trim()
          : ''

    const questionId =
      typeof record.id === 'string' && record.id.trim()
        ? record.id.trim()
        : prompt

    const rawOptions = Array.isArray(record.options) ? record.options : []
    const options: AskUserQuestionOption[] = []

    for (const rawOption of rawOptions) {
      const optionRecord = toPendingToolInputRecord(rawOption)
      if (!optionRecord) {
        continue
      }

      const label =
        typeof optionRecord.label === 'string' ? optionRecord.label.trim() : ''
      if (!label) {
        continue
      }

      const optionId =
        typeof optionRecord.id === 'string' && optionRecord.id.trim()
          ? optionRecord.id.trim()
          : label

      options.push({
        id: optionId,
        label,
        description:
          typeof optionRecord.description === 'string' &&
          optionRecord.description.trim()
            ? optionRecord.description.trim()
            : undefined,
      })
    }

    if (!prompt || options.length < 2) {
      continue
    }

    questions.push({
      id: questionId,
      header:
        typeof record.header === 'string' && record.header.trim()
          ? record.header.trim()
          : undefined,
      prompt,
      options,
      allowMultiple: Boolean(record.allow_multiple ?? record.allowMultiple),
    })
  }

  return questions
}

function hasAskUserQuestionAnswer(answer: AskUserQuestionAnswer): boolean {
  return (
    answer.selectedLabels.length > 0 || answer.freeformText.trim().length > 0
  )
}

function toggleAskUserQuestionOption(
  answer: AskUserQuestionAnswer,
  option: AskUserQuestionOption,
): AskUserQuestionAnswer {
  const existingIndex = answer.selectedOptionIds.indexOf(option.id)
  if (existingIndex >= 0) {
    return {
      ...answer,
      selectedOptionIds: answer.selectedOptionIds.filter(id => id !== option.id),
      selectedLabels: answer.selectedLabels.filter(label => label !== option.label),
    }
  }

  return {
    ...answer,
    selectedOptionIds: [...answer.selectedOptionIds, option.id],
    selectedLabels: [...answer.selectedLabels, option.label],
  }
}

function buildAskUserQuestionToolResult(
  toolInput: Record<string, unknown> | undefined,
  questions: AskUserQuestion[],
  answers: Record<string, AskUserQuestionAnswer>,
): string {
  const donorStyleAnswers: Record<string, string> = {}
  const annotations: Record<string, { notes?: string }> = {}

  for (const question of questions) {
    const answer =
      answers[getAskUserQuestionKey(question)] ??
      createEmptyAskUserQuestionAnswer()
    const selectedLabelsText = answer.selectedLabels.join(', ').trim()
    const notes = answer.freeformText.trim()

    if (!selectedLabelsText && !notes) {
      continue
    }

    donorStyleAnswers[question.prompt] = selectedLabelsText || notes

    if (selectedLabelsText && notes) {
      annotations[question.prompt] = { notes }
    }
  }

  return JSON.stringify(
    {
      ...(toolInput ?? {}),
      answers: donorStyleAnswers,
      ...(Object.keys(annotations).length > 0 ? { annotations } : {}),
    },
    null,
    2,
  )
}

function summarizeAskUserQuestionAnswer(
  answer: AskUserQuestionAnswer,
): string {
  const labelText = answer.selectedLabels.join(', ')
  const freeformText = answer.freeformText.trim()
  if (labelText && freeformText) {
    return `${labelText} | ${freeformText}`
  }
  return labelText || freeformText || 'No answer provided'
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
    <Box flexDirection="row" width="100%" alignItems="flex-start">
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
  width,
}: {
  content: string
  addMargin: boolean
  themeSetting: ThemeSetting
  width: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const lines = content.split('\n')
  return (
    <Box
      flexDirection="column"
      marginTop={addMargin ? 1 : 0}
      paddingRight={1}
      width="100%"
    >
      {lines.map((line, index) => (
        <Text key={index} wrap="truncate-end">
          {applyBackground(
            `${index === 0 ? `${DONOR_POINTER} ` : '  '}${line || ' '}${' '.repeat(
              Math.max(
                0,
                width -
                  stringWidth(
                    `${index === 0 ? `${DONOR_POINTER} ` : '  '}${line || ' '}`,
                  ),
              ),
            )}`,
            theme.userMessageBackground,
          )}
        </Text>
      ))}
    </Box>
  )
}

function ToolUseFlow({
  toolName,
  toolInput,
  width,
}: {
  toolName: string
  toolInput?: Record<string, unknown>
  width: number
}): React.ReactNode {
  const title = formatToolUseTitle(toolName, toolInput)
  const accentColor = getToolAccentColor(toolName)
  const bodyLines = getToolUseBodyLines(toolName, toolInput)

  return (
    <Box flexDirection="row" width="100%" alignItems="flex-start">
      <Box minWidth={2}>
        <Text color={accentColor}>{'●'}</Text>
      </Box>
      <Box flexDirection="column" width={width}>
        <Text color={accentColor} wrap="wrap">
          {title}
        </Text>
        {bodyLines.length > 0 ? (
          bodyLines.map((line, index) => (
            <Text
              key={`${toolName}-${index}`}
              color={line.color}
              dimColor={line.dimColor}
              wrap="wrap"
            >
              {line.text}
            </Text>
          ))
        ) : null}
      </Box>
    </Box>
  )
}

function ToolResultFlow({
  rawResult,
  toolName,
  toolInput,
  isError,
}: {
  rawResult: unknown
  toolName?: string
  toolInput?: Record<string, unknown>
  isError: boolean
}): React.ReactNode {
  const presentation = buildToolResultPresentation({
    rawResult,
    toolName,
    toolInput,
    isError,
  })

  return (
    <MessageResponseFlow>
      <Box flexDirection="column">
        {presentation.header ? (
          <Text
            color={presentation.header.color}
            dimColor={presentation.header.dimColor}
            wrap="wrap"
          >
            {presentation.header.text}
          </Text>
        ) : null}
        {presentation.bodyLines.map((line, index) => (
          <Text
            key={`${toolName ?? 'tool'}-result-${index}`}
            color={line.color}
            dimColor={line.dimColor}
            wrap="wrap"
          >
            {line.text}
          </Text>
        ))}
      </Box>
    </MessageResponseFlow>
  )
}

function ToolProgressFlow({
  content,
  toolName,
}: {
  content: string
  toolName?: string
}): React.ReactNode {
  return (
    <MessageResponseFlow>
      <Box flexDirection="column">
        {getPreviewLines(content, 4, true).map((line, index) => (
          <Text key={`${toolName ?? 'tool'}-progress-${index}`} dimColor wrap="wrap">
            {line}
          </Text>
        ))}
      </Box>
    </MessageResponseFlow>
  )
}

function CollapsedReadSearchFlow({
  entry,
  width,
}: {
  entry: CollapsedReadSearchEntry
  width: number
}): React.ReactNode {
  const summary = summarizeCollapsedReadSearchEntry(entry)

  return (
    <Box flexDirection="column" width="100%">
      <Box flexDirection="row" width="100%" alignItems="flex-start">
        <Box minWidth={2}>
          <Text dimColor={!entry.isActive}>{'●'}</Text>
        </Box>
        <Box flexDirection="column" width={width}>
          <Text dimColor={!entry.isActive} wrap="wrap">
            {summary}
          </Text>
        </Box>
      </Box>
      {entry.isActive && entry.hint ? (
        <MessageResponseFlow>
          <Text dimColor wrap="wrap">
            {entry.hint}
          </Text>
        </MessageResponseFlow>
      ) : null}
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

function extractTaggedContent(
  text: string,
  tag: string,
): string | null {
  const openTag = `<${tag}>`
  const closeTag = `</${tag}>`
  const startIndex = text.indexOf(openTag)
  const endIndex = text.indexOf(closeTag)
  if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
    return null
  }
  return text
    .slice(startIndex + openTag.length, endIndex)
    .trim()
}

function unwrapPersistedOutput(text: string): string {
  return extractTaggedContent(text, 'persisted-output') ?? text
}

function clipPreviewLine(
  line: string,
  maxLength = 140,
): string {
  if (stringWidth(line) <= maxLength) {
    return line
  }
  return `${line.slice(0, Math.max(0, maxLength - 3))}...`
}

function normalizePreviewLines(
  value: string,
  keepEmpty = false,
): string[] {
  const rawLines = value
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

  const trimmed = rawLines.slice(start, end).map(line => clipPreviewLine(line))
  return keepEmpty
    ? trimmed
    : trimmed.filter(line => line.trim().length > 0)
}

function truncatePreviewLines(
  lines: string[],
  maxLines: number,
): string[] {
  if (lines.length <= maxLines) {
    return lines
  }
  const remaining = lines.length - maxLines
  return [
    ...lines.slice(0, maxLines),
    `... +${remaining} more line${remaining === 1 ? '' : 's'}`,
  ]
}

function getPreviewLines(
  value: string,
  maxLines = 8,
  keepEmpty = false,
): string[] {
  return truncatePreviewLines(
    normalizePreviewLines(value, keepEmpty),
    maxLines,
  )
}

function getNumberedPreviewLines(
  value: string,
  maxLines = 8,
): string[] {
  const rawLines = normalizePreviewLines(value, true)
  if (rawLines.length === 0) {
    return []
  }
  const visibleLines = rawLines.slice(0, maxLines)
  const width = String(visibleLines.length).length
  const numbered = visibleLines.map((line, index) =>
    `${String(index + 1).padStart(width, ' ')} ${line || ' '}`,
  )
  if (rawLines.length > maxLines) {
    numbered.push(`... +${rawLines.length - maxLines} more lines`)
  }
  return numbered
}

function truncateInlineText(
  value: string,
  maxLength = 76,
): string {
  if (stringWidth(value) <= maxLength) {
    return value
  }
  return `${value.slice(0, Math.max(0, maxLength - 3))}...`
}

function getDisplayPath(pathValue: unknown): string | null {
  if (typeof pathValue !== 'string' || !pathValue.trim()) {
    return null
  }

  const trimmed = pathValue.trim()
  const cwd = process.cwd()
  const normalizedPath = trimmed.replace(/\//g, '\\')
  const normalizedCwd = cwd.replace(/\//g, '\\')

  if (
    normalizedPath.toLowerCase().startsWith(
      `${normalizedCwd.toLowerCase()}\\`,
    )
  ) {
    return normalizedPath.slice(normalizedCwd.length + 1)
  }

  return normalizedPath
}

function getToolAccentColor(toolName: string): string | undefined {
  switch (toolName.toLowerCase()) {
    case 'bash':
    case 'shell':
      return 'red'
    case 'write':
    case 'edit':
    case 'multiedit':
    case 'notebookedit':
      return 'green'
    case 'read':
    case 'grep':
    case 'glob':
    case 'ls':
      return 'cyan'
    case 'todowrite':
      return 'yellow'
    default:
      return undefined
  }
}

function formatToolUseTitle(
  toolName: string,
  input: Record<string, unknown> | undefined,
): string {
  const normalized = toolName.toLowerCase()

  if (normalized === 'bash' || normalized === 'shell') {
    const command = getToolTextValue(input, ['command', 'cmd'])
    return command
      ? `${toolName}(${truncateInlineText(command)})`
      : toolName
  }

  if (
    normalized === 'write' ||
    normalized === 'read' ||
    normalized === 'edit' ||
    normalized === 'multiedit' ||
    normalized === 'notebookedit' ||
    normalized === 'ls'
  ) {
    const path = getDisplayPath(
      getToolTextValue(input, ['file_path', 'path']),
    )
    return path ? `${toolName}(${path})` : toolName
  }

  if (normalized === 'grep' || normalized === 'glob') {
    const pattern = getToolTextValue(input, ['pattern', 'query'])
    return pattern
      ? `${toolName}(${truncateInlineText(pattern, 56)})`
      : toolName
  }

  if (normalized === 'task') {
    const description =
      getToolTextValue(input, ['description', 'prompt']) ??
      getToolTextValue(input, ['task'])
    return description
      ? `${toolName}(${truncateInlineText(description, 56)})`
      : toolName
  }

  return toolName
}

function getToolUseBodyLines(
  toolName: string,
  input: Record<string, unknown> | undefined,
): ToolRenderLine[] {
  if (toolName.toLowerCase() !== 'todowrite') {
    return []
  }

  const todos = Array.isArray(input?.todos) ? input.todos : []
  return todos.slice(0, 4).flatMap((todo, index) => {
    const record = asRecord(todo)
    if (!record) {
      return []
    }
    const content = typeof record.content === 'string' ? record.content.trim() : ''
    if (!content) {
      return []
    }
    const status = typeof record.status === 'string' ? record.status : 'pending'
    return [
      {
        text: `${index === 0 ? '⎿' : ' '} [${status}] ${content}`,
        dimColor: true,
      },
    ]
  })
}

function buildBashResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawRecord = asRecord(rawResult)
  let stdout =
    typeof rawRecord?.stdout === 'string' ? rawRecord.stdout : ''
  let stderr =
    typeof rawRecord?.stderr === 'string' ? rawRecord.stderr : ''
  let exitCode =
    typeof rawRecord?.exitCode === 'number' ||
    typeof rawRecord?.exitCode === 'string'
      ? String(rawRecord.exitCode)
      : ''

  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  if (!stdout && !stderr) {
    const exitMatch = rawText.match(/(?:^|\n)Exit code:\s*(-?\d+)\s*$/)
    if (exitMatch) {
      exitCode = exitCode || exitMatch[1]!
    }

    const withoutExitCode = rawText
      .replace(/(?:^|\n)Exit code:\s*-?\d+\s*$/g, '')
      .trim()

    if (withoutExitCode.startsWith('STDERR:\n')) {
      stderr = withoutExitCode.slice('STDERR:\n'.length).trim()
    } else if (withoutExitCode.includes('\nSTDERR:\n')) {
      const [stdoutText, stderrText] = withoutExitCode.split('\nSTDERR:\n')
      stdout = stdoutText?.trim() ?? ''
      stderr = stderrText?.trim() ?? ''
    } else {
      stdout = withoutExitCode
    }
  }

  const bodyLines: ToolRenderLine[] = []
  let header: ToolRenderLine | undefined

  if (exitCode && exitCode !== '0') {
    header = {
      text: `Error: Exit code ${exitCode}`,
      color: 'red',
      dimColor: false,
    }
  }

  if (!header) {
    const stdoutLines = getPreviewLines(stdout, 8, true)
    if (stdoutLines.length > 0) {
      const [firstLine, ...restLines] = stdoutLines
      header = {
        text: firstLine!,
        dimColor: false,
      }
      bodyLines.push(
        ...restLines.map(text => ({
          text,
          dimColor: false,
        })),
      )
    }
  }

  const stderrLines = getPreviewLines(stderr, 8, true)
  if (stderrLines.length > 0) {
    bodyLines.push(
      ...stderrLines.map(text => ({
        text,
        color: 'red',
        dimColor: false,
      })),
    )
  }

  if (!header && isError) {
    const errorLines = getPreviewLines(rawText, 8, true)
    if (errorLines.length > 0) {
      const [firstLine, ...restLines] = errorLines
      header = {
        text: firstLine!.startsWith('Error:')
          ? firstLine!
          : `Error: ${firstLine!}`,
        color: 'red',
        dimColor: false,
      }
      bodyLines.push(
        ...restLines.map(text => ({
          text,
          color: 'red',
          dimColor: false,
        })),
      )
    }
  }

  if (!header && bodyLines.length === 0) {
    header = {
      text: '(no output)',
      dimColor: true,
    }
  }

  return {
    header,
    bodyLines,
  }
}

function buildWriteResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const record = asRecord(rawResult)
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const summaryLines = getPreviewLines(rawText, 3, true)
  const contentPreview =
    (typeof record?.content === 'string' ? record.content : null) ??
    getToolTextValue(toolInput, ['content'])

  const bodyLines: ToolRenderLine[] = []
  if (summaryLines.length > 1) {
    bodyLines.push(
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
    )
  }

  if (!isError && contentPreview) {
    bodyLines.push(
      ...getNumberedPreviewLines(contentPreview, 10).map(text => ({
        text,
        dimColor: false,
      })),
    )
  }

  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Write failed.' : 'Write completed.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError && bodyLines.length === 0,
    },
    bodyLines,
  }
}

function buildReadResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const previewLines = getPreviewLines(rawText, 10, true)
  if (previewLines.length === 0) {
    return {
      header: {
        text: isError ? 'Read failed.' : 'Read completed.',
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      },
      bodyLines: [],
    }
  }

  const looksLikeNumberedRead = /^\s*\d+\|/.test(previewLines[0] ?? '')
  if (looksLikeNumberedRead) {
    return {
      bodyLines: previewLines.map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: false,
      })),
    }
  }

  const [firstLine, ...restLines] = previewLines
  return {
    header: {
      text: firstLine!,
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    },
    bodyLines: restLines.map(text => ({
      text,
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    })),
  }
}

function buildEditResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const diffMarker = '\nDiff:\n'
  const lintMarker = '\nLint issues:\n'
  const diffIndex = rawText.indexOf(diffMarker)
  const lintIndex = rawText.indexOf(lintMarker)
  const record = asRecord(rawResult)

  let summaryText = rawText
  let diffText = ''
  let lintText = ''

  if (diffIndex !== -1) {
    summaryText = rawText.slice(0, diffIndex).trim()
    const diffEnd = lintIndex !== -1 ? lintIndex : rawText.length
    diffText = rawText.slice(diffIndex + diffMarker.length, diffEnd).trim()
  }

  if (lintIndex !== -1) {
    lintText = rawText.slice(lintIndex + lintMarker.length).trim()
  }

  const bodyLines: ToolRenderLine[] = []

  if (diffText) {
    bodyLines.push(
      ...getPreviewLines(diffText, 12, true).map(text => ({
        text,
        color: text.startsWith('+')
          ? 'green'
          : text.startsWith('-')
            ? 'red'
            : undefined,
        dimColor: text.startsWith('@@'),
      })),
    )
  } else {
    const structuredPatch = Array.isArray(record?.structuredPatch)
      ? record.structuredPatch
      : []
    if (structuredPatch.length > 0) {
      for (const hunk of structuredPatch.slice(0, 2)) {
        const hunkRecord = asRecord(hunk)
        const lines = Array.isArray(hunkRecord?.lines)
          ? hunkRecord.lines
          : []
        bodyLines.push(
          ...lines.slice(0, 8).flatMap(line =>
            typeof line === 'string'
              ? [
                  {
                    text: clipPreviewLine(line),
                    color: line.startsWith('+')
                      ? 'green'
                      : line.startsWith('-')
                        ? 'red'
                        : undefined,
                    dimColor: line.startsWith('@@'),
                  },
                ]
              : [],
          ),
        )
      }
    } else {
      const replacementPreview =
        getToolTextValue(toolInput, ['new_string']) ??
        (typeof record?.content === 'string' ? record.content : null)
      if (replacementPreview) {
        bodyLines.push(
          ...getNumberedPreviewLines(replacementPreview, 10).map(text => ({
            text,
            dimColor: false,
          })),
        )
      }
    }
  }

  if (lintText) {
    bodyLines.push(
      ...getPreviewLines(lintText, 6, true).map(text => ({
        text,
        color: 'yellow',
        dimColor: false,
      })),
    )
  }

  const summaryLines = getPreviewLines(summaryText, 3, true)
  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Edit failed.' : 'Edit completed.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError && bodyLines.length === 0,
    },
    bodyLines: [
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
      ...bodyLines,
    ],
  }
}

function buildTodoResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const summaryLines = getPreviewLines(rawText, 2, true)
  const todos = Array.isArray(toolInput?.todos) ? toolInput.todos : []

  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Todo update failed.' : 'Todo list updated.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    },
    bodyLines: [
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
      ...todos.slice(0, 4).flatMap(todo => {
        const record = asRecord(todo)
        if (!record) {
          return []
        }
        const content = typeof record.content === 'string' ? record.content.trim() : ''
        if (!content) {
          return []
        }
        const status = typeof record.status === 'string' ? record.status : 'pending'
        return [
          {
            text: `[${status}] ${content}`,
            dimColor: true,
          },
        ]
      }),
    ],
  }
}

function buildGenericResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const previewLines = getPreviewLines(rawText, 10, true)
  if (previewLines.length === 0) {
    return {
      header: {
        text: isError ? 'Tool failed.' : 'Tool completed.',
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      },
      bodyLines: [],
    }
  }

  const [firstLine, ...restLines] = previewLines
  return {
    header: {
      text: firstLine!,
      color: isError ? 'red' : undefined,
      dimColor: !isError && restLines.length === 0,
    },
    bodyLines: restLines.map(text => ({
      text,
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    })),
  }
}

function buildToolResultPresentation({
  rawResult,
  toolName,
  toolInput,
  isError,
}: {
  rawResult: unknown
  toolName?: string
  toolInput?: Record<string, unknown>
  isError: boolean
}): ToolResultPresentation {
  const normalized = String(toolName ?? '').toLowerCase()

  if (normalized === 'bash' || normalized === 'shell') {
    return buildBashResultPresentation(rawResult, isError)
  }

  if (normalized === 'write') {
    return buildWriteResultPresentation(rawResult, toolInput, isError)
  }

  if (normalized === 'read') {
    return buildReadResultPresentation(rawResult, isError)
  }

  if (
    normalized === 'edit' ||
    normalized === 'multiedit' ||
    normalized === 'notebookedit'
  ) {
    return buildEditResultPresentation(rawResult, toolInput, isError)
  }

  if (normalized === 'todowrite') {
    return buildTodoResultPresentation(rawResult, toolInput, isError)
  }

  return buildGenericResultPresentation(rawResult, isError)
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

function describeSpeculationStatus(
  speculation: CcminiSpeculationState,
  inputValue: string,
): string {
  if (speculation.status === 'ready') {
    if (inputValue.trim() === speculation.suggestion.trim() && speculation.suggestion) {
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

function formatConnectionTarget(baseUrl: string): string {
  try {
    const url = new URL(baseUrl)
    const path = url.pathname === '/' ? '' : url.pathname
    return `${url.host}${path}`
  } catch {
    return baseUrl.replace(/^https?:\/\//, '')
  }
}

function getThemeLabel(themeSetting: ThemeSetting): string {
  return THEME_OPTIONS.find(option => option.value === themeSetting)?.label ?? themeSetting
}

function getConnectionStatusHeadline(
  status: CcminiConnectionStatus,
): string {
  switch (status) {
    case 'connected':
      return 'Connected'
    case 'disconnected':
      return 'Disconnected'
    default:
      return 'Connecting'
  }
}

function getConnectionStatusDetail(
  status: CcminiConnectionStatus,
  baseUrl: string,
): string {
  const target = formatConnectionTarget(baseUrl)
  switch (status) {
    case 'connected':
      return `Bridge ready at ${target}`
    case 'disconnected':
      return `Waiting to reconnect to ${target}`
    default:
      return `Checking connectivity to ${target}`
  }
}

function getConnectionStatusColor(
  status: CcminiConnectionStatus,
  themeSetting: ThemeSetting,
): string {
  const theme = getThemeTokens(themeSetting)
  switch (status) {
    case 'connected':
      return theme.permission
    case 'disconnected':
      return theme.error
    default:
      return theme.warning
  }
}

function getRecentActivityPreview(
  messages: MessageType[],
  inboxLines: string[],
): string[] {
  if (inboxLines.length > 0) {
    return inboxLines.slice(-3)
  }

  const derived: string[] = []
  for (const message of messages) {
    const firstLine = trimMessageLines(getMessageLines(message), 1)[0]?.trim()
    if (!firstLine) {
      continue
    }
    if (
      message.type === 'system' &&
      firstLine.startsWith('ccmini transport connected:')
    ) {
      continue
    }

    if (message.type === 'user') {
      derived.push(`prompt: ${firstLine}`)
      continue
    }
    if (message.type === 'assistant') {
      derived.push(`reply: ${firstLine}`)
      continue
    }
    if (message.type === 'thinking') {
      derived.push('assistant is thinking')
      continue
    }
    derived.push(firstLine)
  }

  return derived.slice(-3)
}

type AssistantToolUseBlock = {
  type?: string
  id?: string
  name?: string
  input?: Record<string, unknown>
}

type UserToolResultBlock = {
  type?: string
  tool_use_id?: string
  content?: unknown
  is_error?: boolean
}

type ProgressPayload = {
  type?: string
  kind?: string
  toolName?: string
  content?: unknown
}

type ToolUseLookupEntry = {
  name: string
  input?: Record<string, unknown>
}

type PlannerTaskStatus = 'pending' | 'in_progress' | 'completed'

type PlannerTask = {
  id: string
  content: string
  status: PlannerTaskStatus
  dependsOn: string[]
}

type PlanPanelState =
  | {
      mode: 'idle'
    }
  | {
      mode: 'active'
      detail: string
    }
  | {
      mode: 'ready'
      detail: string
      planLines: string[]
    }

function createActivePlanPanelState(): PlanPanelState {
  return {
    mode: 'active',
    detail:
      'Read-only exploration is active. The agent is expected to inspect the codebase and propose a plan before editing.',
  }
}

function createReadyPlanPanelState(planText: string): PlanPanelState {
  return {
    mode: 'ready',
    detail: 'Plan mode exited. The approved implementation outline is attached below.',
    planLines: trimMessageLines(planText.split('\n'), 8),
  }
}

function parseRuntimePlanPanelState(value: unknown): PlanPanelState {
  const record = asRecord(value)
  if (!record) {
    return { mode: 'idle' }
  }

  const isActive = Boolean(record.isActive)
  const planText =
    typeof record.planText === 'string' ? record.planText.trim() : ''

  if (isActive) {
    return createActivePlanPanelState()
  }
  if (planText) {
    return createReadyPlanPanelState(planText)
  }
  return { mode: 'idle' }
}

function mergePlanPanelStates(
  runtimePlanState: PlanPanelState,
  transcriptPlanState: PlanPanelState,
): PlanPanelState {
  return runtimePlanState.mode !== 'idle'
    ? runtimePlanState
    : transcriptPlanState
}

type ToolRenderLine = {
  text: string
  color?: string
  dimColor?: boolean
}

type ToolResultPresentation = {
  header?: ToolRenderLine
  bodyLines: ToolRenderLine[]
}

type RenderedMessageEntry = {
  key: string
  message: MessageType
  lines: string[]
}

type CollapsibleToolKind = 'search' | 'read' | 'list'

type CollapsibleToolState = {
  toolUseId: string
  toolName: string
  kind: CollapsibleToolKind
  hint?: string
  hasToolUse: boolean
  hasToolResult: boolean
  hasProgress: boolean
}

type CollapsedReadSearchEntry = {
  kind: 'collapsed_read_search'
  key: string
  messages: RenderedMessageEntry[]
  searchCount: number
  readCount: number
  listCount: number
  hint?: string
  isActive: boolean
}

type DisplayEntry =
  | {
      kind: 'message'
      item: RenderedMessageEntry
    }
  | CollapsedReadSearchEntry

function getAssistantToolUseBlock(
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

function getUserToolResultBlock(
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

function getProgressPayload(
  message: MessageType,
): ProgressPayload | null {
  if (message.type !== 'progress') {
    return null
  }

  const data = (message as MessageType & { data?: ProgressPayload }).data
  return data ?? null
}

function getProgressToolUseId(message: MessageType): string | null {
  if (message.type !== 'progress') {
    return null
  }

  const toolUseID = (message as MessageType & { toolUseID?: unknown }).toolUseID
  return typeof toolUseID === 'string' && toolUseID.trim()
    ? toolUseID
    : null
}

function getToolBaseName(pathValue: unknown): string | null {
  if (typeof pathValue !== 'string' || !pathValue.trim()) {
    return null
  }
  const normalized = pathValue.trim()
  return normalized.split(/[/\\]/).pop() ?? normalized
}

function getToolTextValue(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string | null {
  if (!input) {
    return null
  }

  for (const key of keys) {
    const value = input[key]
    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }

  return null
}

function summarizeToolUse(
  toolName: string,
  input: Record<string, unknown> | undefined,
): {
  title: string
  detail?: string
} {
  const normalized = toolName.toLowerCase()

  if (normalized === 'todowrite') {
    const todos = Array.isArray(input?.todos) ? input.todos.length : null
    return {
      title: 'TodoWrite',
      detail:
        todos && todos > 0
          ? `Updating todo list (${todos} items)`
          : 'Updating todo list',
    }
  }

  if (normalized === 'read') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: 'Read',
      detail: path ? `Reading ${path}` : 'Reading file',
    }
  }

  if (normalized === 'write') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: 'Write',
      detail: path ? `Writing ${path}` : 'Writing file',
    }
  }

  if (normalized === 'edit' || normalized === 'multiedit' || normalized === 'notebookedit') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: toolName,
      detail: path ? `Editing ${path}` : 'Editing file',
    }
  }

  if (normalized === 'bash' || normalized === 'shell') {
    const command = getToolTextValue(input, ['command', 'cmd'])
    return {
      title: toolName,
      detail: command ? `Running ${command}` : 'Running shell command',
    }
  }

  if (normalized === 'grep') {
    const pattern = getToolTextValue(input, ['pattern', 'query'])
    return {
      title: 'Grep',
      detail: pattern ? `Searching for ${pattern}` : 'Searching files',
    }
  }

  if (normalized === 'glob') {
    const pattern = getToolTextValue(input, ['pattern'])
    return {
      title: 'Glob',
      detail: pattern ? `Finding ${pattern}` : 'Finding matching files',
    }
  }

  if (normalized === 'ls') {
    const path = getToolTextValue(input, ['path'])
    return {
      title: 'LS',
      detail: path ? `Listing ${path}` : 'Listing files',
    }
  }

  if (normalized === 'webfetch') {
    const url = getToolTextValue(input, ['url'])
    return {
      title: 'WebFetch',
      detail: url ? `Fetching ${url}` : 'Fetching URL',
    }
  }

  if (normalized === 'task') {
    const description =
      getToolTextValue(input, ['description', 'prompt']) ??
      getToolTextValue(input, ['task'])
    return {
      title: 'Task',
      detail: description ? `Delegating ${description}` : 'Delegating subtask',
    }
  }

  return {
    title: toolName,
    detail: undefined,
  }
}

function summarizeToolResultText(value: unknown): string {
  const text =
    typeof value === 'string' ? value : stringifyUnknown(value)
  const normalized = text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)

  if (normalized.length === 0) {
    return 'Tool completed.'
  }

  return normalized[0]!
}

function buildToolUseLookup(
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

function normalizePlannerTaskStatus(value: unknown): PlannerTaskStatus {
  if (value === 'completed' || value === 'in_progress' || value === 'pending') {
    return value
  }
  return 'pending'
}

function parsePlannerTask(value: unknown): PlannerTask | null {
  const record = asRecord(value)
  if (!record) {
    return null
  }

  const id =
    typeof record.id === 'string' && record.id.trim()
      ? record.id.trim()
      : ''
  const content =
    typeof record.content === 'string' && record.content.trim()
      ? record.content.trim()
      : ''

  if (!id || !content) {
    return null
  }

  return {
    id,
    content,
    status: normalizePlannerTaskStatus(record.status),
    dependsOn: Array.isArray(record.depends_on)
      ? record.depends_on
          .map(dep => (typeof dep === 'string' ? dep.trim() : ''))
          .filter(Boolean)
      : [],
  }
}

function applyTodoWriteToPlannerTasks(
  previous: PlannerTask[],
  toolInput: Record<string, unknown> | undefined,
): PlannerTask[] {
  const rawTodos = Array.isArray(toolInput?.todos) ? toolInput.todos : []
  if (rawTodos.length === 0) {
    return previous
  }

  const nextTasks = Boolean(toolInput?.merge)
    ? new Map(previous.map(task => [task.id, task]))
    : new Map<string, PlannerTask>()

  for (const rawTodo of rawTodos) {
    const task = parsePlannerTask(rawTodo)
    if (!task) {
      continue
    }

    const existing = nextTasks.get(task.id)
    nextTasks.set(task.id, {
      id: task.id,
      content: task.content || existing?.content || '',
      status: task.status,
      dependsOn: task.dependsOn.length > 0 ? task.dependsOn : (existing?.dependsOn ?? []),
    })
  }

  return [...nextTasks.values()]
}

function derivePlannerTasks(
  messages: MessageType[],
  toolUseLookup: Map<string, ToolUseLookupEntry>,
): PlannerTask[] {
  let tasks: PlannerTask[] = []

  for (const message of messages) {
    const toolResult = getUserToolResultBlock(message)
    if (!toolResult?.tool_use_id || toolResult.is_error) {
      continue
    }

    const toolUse = toolUseLookup.get(toolResult.tool_use_id)
    if (String(toolUse?.name ?? '').trim().toLowerCase() !== 'todowrite') {
      continue
    }

    tasks = applyTodoWriteToPlannerTasks(tasks, toolUse?.input)
  }

  return tasks
}

function sortPlannerTasks(tasks: PlannerTask[]): PlannerTask[] {
  const unresolved = new Set(
    tasks.filter(task => task.status !== 'completed').map(task => task.id),
  )

  const rank = (task: PlannerTask): number => {
    if (task.status === 'in_progress') {
      return 0
    }
    if (task.status === 'pending') {
      return task.dependsOn.some(dep => unresolved.has(dep)) ? 2 : 1
    }
    return 3
  }

  const compareId = (left: string, right: string): number => {
    const leftNum = Number.parseInt(left, 10)
    const rightNum = Number.parseInt(right, 10)
    if (!Number.isNaN(leftNum) && !Number.isNaN(rightNum)) {
      return leftNum - rightNum
    }
    return left.localeCompare(right)
  }

  return [...tasks].sort((left, right) => {
    const leftRank = rank(left)
    const rightRank = rank(right)
    if (leftRank !== rightRank) {
      return leftRank - rightRank
    }
    return compareId(left.id, right.id)
  })
}

function getPlannerTaskMarker(status: PlannerTaskStatus): string {
  switch (status) {
    case 'completed':
      return '[x]'
    case 'in_progress':
      return '[~]'
    default:
      return '[ ]'
  }
}

function TaskPlannerPanel({
  tasks,
  planState,
  themeSetting,
  width,
}: {
  tasks: PlannerTask[]
  planState: PlanPanelState
  themeSetting: ThemeSetting
  width: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const ordered = useMemo(() => sortPlannerTasks(tasks), [tasks])
  const unresolved = useMemo(
    () => new Set(ordered.filter(task => task.status !== 'completed').map(task => task.id)),
    [ordered],
  )

  const completedCount = ordered.filter(task => task.status === 'completed').length
  const inProgressCount = ordered.filter(task => task.status === 'in_progress').length
  const pendingCount = ordered.filter(task => task.status === 'pending').length
  const summaryParts = [
    `${completedCount} done`,
    `${inProgressCount} in progress`,
    `${pendingCount} open`,
  ]

  return (
    <Box marginTop={1} flexDirection="column">
      <Text dimColor>
        {applyForeground(`Task planner`, theme.claude)}
        <Text dimColor>{` · ${ordered.length} tasks (${summaryParts.join(', ')})`}</Text>
      </Text>
      {planState.mode !== 'idle' ? (
        <Box flexDirection="column" marginTop={1}>
          <Text color={planState.mode === 'active' ? theme.warning : theme.permission} wrap="wrap">
            {planState.mode === 'active' ? 'Plan mode active' : 'Plan ready'}
          </Text>
          <Box marginLeft={2}>
            <Text dimColor wrap="wrap">
              {planState.detail}
            </Text>
          </Box>
          {planState.mode === 'ready'
            ? planState.planLines.slice(0, 6).map((line, index) => (
                <Box key={`todo-plan-line-${index}`} marginLeft={2}>
                  <Text dimColor wrap="wrap">
                    {line}
                  </Text>
                </Box>
              ))
            : null}
        </Box>
      ) : null}
      <Box flexDirection="column" marginTop={1}>
        {ordered.map(task => {
          const blockedBy = task.dependsOn.filter(dep => unresolved.has(dep))
          const line = `${getPlannerTaskMarker(task.status)} ${task.content}`

          return (
            <Box key={task.id} flexDirection="column" marginBottom={blockedBy.length > 0 ? 1 : 0}>
              <Text
                wrap="wrap"
                bold={task.status === 'in_progress'}
                strikethrough={task.status === 'completed'}
                dimColor={task.status === 'completed' || blockedBy.length > 0}
              >
                {truncateInlineText(line, Math.max(18, width))}
              </Text>
              {blockedBy.length > 0 ? (
                <Box marginLeft={2}>
                  <Text dimColor wrap="wrap">
                    {`↳ blocked by ${blockedBy.join(', ')}`}
                  </Text>
                </Box>
              ) : null}
            </Box>
          )
        })}
      </Box>
    </Box>
  )
}

function compareTaskIds(left: string, right: string): number {
  const leftNum = Number.parseInt(left, 10)
  const rightNum = Number.parseInt(right, 10)
  if (!Number.isNaN(leftNum) && !Number.isNaN(rightNum)) {
    return leftNum - rightNum
  }
  return left.localeCompare(right)
}

function sortTaskBoardTasks(tasks: CcminiTaskBoardTask[]): CcminiTaskBoardTask[] {
  const unresolved = new Set(
    tasks.filter(task => task.status !== 'completed').map(task => task.id),
  )

  const rank = (task: CcminiTaskBoardTask): number => {
    if (task.status === 'in_progress') {
      return 0
    }
    if (task.status === 'pending') {
      return (task.blockedBy ?? []).some(dep => unresolved.has(dep)) ? 2 : 1
    }
    return 3
  }

  return [...tasks].sort((left, right) => {
    const leftRank = rank(left)
    const rightRank = rank(right)
    if (leftRank !== rightRank) {
      return leftRank - rightRank
    }
    return compareTaskIds(left.id, right.id)
  })
}

function TaskBoardPanel({
  tasks,
  planState,
  themeSetting,
  width,
}: {
  tasks: CcminiTaskBoardTask[]
  planState: PlanPanelState
  themeSetting: ThemeSetting
  width: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const { stdout } = useStdout()
  const [completionRefreshTick, setCompletionRefreshTick] = useState(0)
  const completionTimestampsRef = useRef<Map<string, number>>(new Map())
  const previousCompletedIdsRef = useRef<Set<string> | null>(null)

  const currentCompletedIds = new Set(
    tasks.filter(task => task.status === 'completed').map(task => task.id),
  )
  if (previousCompletedIdsRef.current === null) {
    previousCompletedIdsRef.current = new Set(currentCompletedIds)
  }
  const observedAt = Date.now()
  for (const taskId of currentCompletedIds) {
    if (!previousCompletedIdsRef.current.has(taskId)) {
      completionTimestampsRef.current.set(taskId, observedAt)
    }
  }
  for (const taskId of [...completionTimestampsRef.current.keys()]) {
    if (!currentCompletedIds.has(taskId)) {
      completionTimestampsRef.current.delete(taskId)
    }
  }
  previousCompletedIdsRef.current = currentCompletedIds

  useEffect(() => {
    if (completionTimestampsRef.current.size === 0) {
      return
    }

    const now = Date.now()
    let earliestExpiry = Number.POSITIVE_INFINITY
    for (const timestamp of completionTimestampsRef.current.values()) {
      const expiry = timestamp + TASK_PANEL_RECENT_COMPLETED_TTL_MS
      if (expiry > now && expiry < earliestExpiry) {
        earliestExpiry = expiry
      }
    }
    if (!Number.isFinite(earliestExpiry)) {
      return
    }

    const timer = setTimeout(() => {
      setCompletionRefreshTick(tick => tick + 1)
    }, earliestExpiry - now)
    return () => clearTimeout(timer)
  }, [tasks])

  const ordered = useMemo(() => sortTaskBoardTasks(tasks), [tasks])
  const completedCount = ordered.filter(task => task.status === 'completed').length
  const inProgressCount = ordered.filter(task => task.status === 'in_progress').length
  const pendingCount = ordered.filter(task => task.status === 'pending').length
  const unresolved = useMemo(
    () => new Set(tasks.filter(task => task.status !== 'completed').map(task => task.id)),
    [tasks],
  )
  const terminalRows = stdout?.rows ?? 24
  const maxDisplay =
    terminalRows <= 10 ? 0 : Math.min(10, Math.max(3, terminalRows - 14))
  const taskPanelState = useMemo(() => {
    if (maxDisplay <= 0) {
      return {
        visibleTasks: [] as CcminiTaskBoardTask[],
        hiddenSummary: '',
      }
    }
    if (tasks.length <= maxDisplay) {
      return {
        visibleTasks: [...tasks].sort((left, right) => compareTaskIds(left.id, right.id)),
        hiddenSummary: '',
      }
    }

    const now = Date.now()
    const recentCompleted: CcminiTaskBoardTask[] = []
    const olderCompleted: CcminiTaskBoardTask[] = []
    for (const task of tasks.filter(candidate => candidate.status === 'completed')) {
      const timestamp = completionTimestampsRef.current.get(task.id)
      if (
        typeof timestamp === 'number' &&
        now - timestamp < TASK_PANEL_RECENT_COMPLETED_TTL_MS
      ) {
        recentCompleted.push(task)
      } else {
        olderCompleted.push(task)
      }
    }

    recentCompleted.sort((left, right) => compareTaskIds(left.id, right.id))
    olderCompleted.sort((left, right) => compareTaskIds(left.id, right.id))
    const inProgressTasks = tasks
      .filter(task => task.status === 'in_progress')
      .sort((left, right) => compareTaskIds(left.id, right.id))
    const pendingTasks = tasks
      .filter(task => task.status === 'pending')
      .sort((left, right) => {
        const leftBlocked = (left.blockedBy ?? []).some(blocker => unresolved.has(blocker))
        const rightBlocked = (right.blockedBy ?? []).some(blocker => unresolved.has(blocker))
        if (leftBlocked !== rightBlocked) {
          return leftBlocked ? 1 : -1
        }
        return compareTaskIds(left.id, right.id)
      })

    const prioritized = [
      ...recentCompleted,
      ...inProgressTasks,
      ...pendingTasks,
      ...olderCompleted,
    ]
    const hiddenTasks = prioritized.slice(maxDisplay)
    const hiddenInProgress = hiddenTasks.filter(task => task.status === 'in_progress').length
    const hiddenPending = hiddenTasks.filter(task => task.status === 'pending').length
    const hiddenCompleted = hiddenTasks.filter(task => task.status === 'completed').length
    const hiddenParts: string[] = []
    if (hiddenInProgress > 0) {
      hiddenParts.push(`${hiddenInProgress} in progress`)
    }
    if (hiddenPending > 0) {
      hiddenParts.push(`${hiddenPending} pending`)
    }
    if (hiddenCompleted > 0) {
      hiddenParts.push(`${hiddenCompleted} completed`)
    }

    return {
      visibleTasks: prioritized.slice(0, maxDisplay),
      hiddenSummary:
        hiddenParts.length > 0 ? `... +${hiddenParts.join(', ')}` : '',
    }
  }, [completionRefreshTick, maxDisplay, tasks, unresolved])

  return (
    <Box marginTop={1} flexDirection="column">
      <Text dimColor>
        {applyForeground('Tasks', theme.claude)}
        <Text dimColor>{` · ${ordered.length} tasks (${completedCount} done, ${inProgressCount} in progress, ${pendingCount} open)`}</Text>
      </Text>
      {planState.mode !== 'idle' ? (
        <Box flexDirection="column" marginTop={1}>
          <Text color={planState.mode === 'active' ? theme.warning : theme.permission} wrap="wrap">
            {planState.mode === 'active' ? 'Plan mode active' : 'Plan ready'}
          </Text>
          <Box marginLeft={2}>
            <Text dimColor wrap="wrap">
              {planState.detail}
            </Text>
          </Box>
          {planState.mode === 'ready'
            ? planState.planLines.slice(0, 6).map((line, index) => (
                <Box key={`plan-line-${index}`} marginLeft={2}>
                  <Text dimColor wrap="wrap">
                    {line}
                  </Text>
                </Box>
              ))
            : null}
        </Box>
      ) : null}
      <Box flexDirection="column" marginTop={1}>
        {taskPanelState.visibleTasks.map(task => {
          const blockedBy = [...(task.blockedBy ?? [])].sort(compareTaskIds)
          const marker = getPlannerTaskMarker(task.status)
          const subjectLine = `${marker} ${task.subject}`
          const showActivity =
            task.status === 'in_progress' &&
            blockedBy.length === 0 &&
            Boolean(task.activeForm || task.description)
          const detail = showActivity ? task.activeForm || task.description : ''
          const showOwner =
            width >= 60 &&
            Boolean(task.owner) &&
            task.ownerIsActive !== false
          const ownerLabel = showOwner && task.owner ? ` (@${task.owner})` : ''

          return (
            <Box key={task.id} flexDirection="column" marginBottom={blockedBy.length > 0 ? 1 : 0}>
              <Text
                wrap="wrap"
                bold={task.status === 'in_progress'}
                strikethrough={task.status === 'completed'}
                dimColor={task.status === 'completed' || blockedBy.length > 0}
              >
                {truncateInlineText(
                  subjectLine,
                  Math.max(
                    18,
                    width - (showOwner && task.owner ? stringWidth(` (@${task.owner})`) : 0),
                  ),
                )}
                {showOwner ? (
                  <Text
                    color={theme.permission}
                    dimColor
                  >
                    {ownerLabel}
                  </Text>
                ) : null}
              </Text>
              {showActivity && detail ? (
                <Box marginLeft={2}>
                  <Text dimColor wrap="wrap">
                    {truncateInlineText(detail, Math.max(20, width))}
                    {'…'}
                  </Text>
                </Box>
              ) : null}
              {blockedBy.length > 0 ? (
                <Box marginLeft={2}>
                  <Text dimColor wrap="wrap">
                    {`↳ blocked by ${blockedBy.map(id => `#${id}`).join(', ')}`}
                  </Text>
                </Box>
              ) : null}
            </Box>
          )
        })}
        {taskPanelState.hiddenSummary ? (
          <Text dimColor>{taskPanelState.hiddenSummary}</Text>
        ) : null}
      </Box>
    </Box>
  )
}

function getMessageToolUseId(message: MessageType): string | null {
  if (message.type === 'assistant') {
    return getAssistantToolUseBlock(message)?.id ?? null
  }
  if (message.type === 'user') {
    return getUserToolResultBlock(message)?.tool_use_id ?? null
  }
  return getProgressToolUseId(message)
}

function shouldCollapseMessageGap(
  previous: MessageType | undefined,
  current: MessageType,
): boolean {
  if (!previous) {
    return false
  }

  const previousToolUseId = getMessageToolUseId(previous)
  const currentToolUseId = getMessageToolUseId(current)
  return Boolean(
    previousToolUseId &&
      currentToolUseId &&
      previousToolUseId === currentToolUseId,
  )
}

function isCollapsibleReadSearchToolName(toolName: string | undefined): boolean {
  switch (String(toolName ?? '').toLowerCase()) {
    case 'read':
    case 'grep':
    case 'glob':
    case 'ls':
      return true
    default:
      return false
  }
}

function getCollapsibleToolKind(
  toolName: string | undefined,
): CollapsibleToolKind | null {
  switch (String(toolName ?? '').toLowerCase()) {
    case 'grep':
    case 'glob':
      return 'search'
    case 'read':
      return 'read'
    case 'ls':
      return 'list'
    default:
      return null
  }
}

function getCollapsibleToolHint(
  toolName: string | undefined,
  toolInput: Record<string, unknown> | undefined,
): string | undefined {
  const normalized = String(toolName ?? '').toLowerCase()

  if (normalized === 'read' || normalized === 'ls') {
    const path = getDisplayPath(getToolTextValue(toolInput, ['file_path', 'path']))
    return path ?? undefined
  }

  if (normalized === 'grep' || normalized === 'glob') {
    const pattern = getToolTextValue(toolInput, ['pattern', 'query'])
    return pattern ? `"${pattern}"` : undefined
  }

  return undefined
}

function updateCollapsibleToolState(
  states: Map<string, CollapsibleToolState>,
  nextState: CollapsibleToolState,
): void {
  const previous = states.get(nextState.toolUseId)
  if (!previous) {
    states.set(nextState.toolUseId, nextState)
    return
  }

  states.set(nextState.toolUseId, {
    ...previous,
    hint: nextState.hint ?? previous.hint,
    hasToolUse: previous.hasToolUse || nextState.hasToolUse,
    hasToolResult: previous.hasToolResult || nextState.hasToolResult,
    hasProgress: previous.hasProgress || nextState.hasProgress,
  })
}

function getRenderedEntryCollapsibleToolState(
  entry: RenderedMessageEntry,
  toolUseLookup: Map<string, ToolUseLookupEntry>,
): CollapsibleToolState | null {
  const { message } = entry

  if (message.type === 'assistant') {
    const toolUse = getAssistantToolUseBlock(message)
    if (!toolUse?.id || !isCollapsibleReadSearchToolName(toolUse.name)) {
      return null
    }
    const kind = getCollapsibleToolKind(toolUse.name)
    if (!kind) {
      return null
    }
    return {
      toolUseId: toolUse.id,
      toolName: toolUse.name ?? 'unknown',
      kind,
      hint: getCollapsibleToolHint(toolUse.name, toolUse.input),
      hasToolUse: true,
      hasToolResult: false,
      hasProgress: false,
    }
  }

  if (message.type === 'user') {
    const toolResult = getUserToolResultBlock(message)
    if (!toolResult?.tool_use_id) {
      return null
    }
    const toolUse = toolUseLookup.get(toolResult.tool_use_id)
    if (!toolUse || !isCollapsibleReadSearchToolName(toolUse.name)) {
      return null
    }
    const kind = getCollapsibleToolKind(toolUse.name)
    if (!kind) {
      return null
    }
    return {
      toolUseId: toolResult.tool_use_id,
      toolName: toolUse.name,
      kind,
      hint: getCollapsibleToolHint(toolUse.name, toolUse.input),
      hasToolUse: false,
      hasToolResult: true,
      hasProgress: false,
    }
  }

  if (message.type === 'progress') {
    const toolUseId = getProgressToolUseId(message)
    if (!toolUseId) {
      return null
    }
    const toolUse = toolUseLookup.get(toolUseId)
    const toolName = toolUse?.name ?? String(getProgressPayload(message)?.toolName ?? '')
    if (!isCollapsibleReadSearchToolName(toolName)) {
      return null
    }
    const kind = getCollapsibleToolKind(toolName)
    if (!kind) {
      return null
    }
    return {
      toolUseId,
      toolName,
      kind,
      hint:
        getPreviewLines(String(getProgressPayload(message)?.content ?? ''), 1, true)[0] ??
        getCollapsibleToolHint(toolName, toolUse?.input),
      hasToolUse: false,
      hasToolResult: false,
      hasProgress: true,
    }
  }

  return null
}

function summarizeCollapsedReadSearchEntry(
  entry: CollapsedReadSearchEntry,
): string {
  const parts: string[] = []
  const pushPart = (first: string, continuation: string): void => {
    parts.push(parts.length === 0 ? first : continuation)
  }

  if (entry.searchCount > 0) {
    pushPart(
      `${entry.isActive ? 'Searching for' : 'Searched for'} ${entry.searchCount} ${entry.searchCount === 1 ? 'pattern' : 'patterns'}`,
      `${entry.isActive ? 'searching for' : 'searched for'} ${entry.searchCount} ${entry.searchCount === 1 ? 'pattern' : 'patterns'}`,
    )
  }
  if (entry.readCount > 0) {
    pushPart(
      `${entry.isActive ? 'Reading' : 'Read'} ${entry.readCount} ${entry.readCount === 1 ? 'file' : 'files'}`,
      `${entry.isActive ? 'reading' : 'read'} ${entry.readCount} ${entry.readCount === 1 ? 'file' : 'files'}`,
    )
  }
  if (entry.listCount > 0) {
    pushPart(
      `${entry.isActive ? 'Listing' : 'Listed'} ${entry.listCount} ${entry.listCount === 1 ? 'directory' : 'directories'}`,
      `${entry.isActive ? 'listing' : 'listed'} ${entry.listCount} ${entry.listCount === 1 ? 'directory' : 'directories'}`,
    )
  }

  const summary = parts.length > 0 ? parts.join(', ') : 'Working'
  return `${summary}${entry.isActive ? '…' : ''} (ctrl+o to expand)`
}

function collapseReadSearchEntries(
  entries: RenderedMessageEntry[],
  toolUseLookup: Map<string, ToolUseLookupEntry>,
  isLoading: boolean,
): DisplayEntry[] {
  const result: DisplayEntry[] = []
  let index = 0

  while (index < entries.length) {
    const startEntry = entries[index]!
    const startState = getRenderedEntryCollapsibleToolState(
      startEntry,
      toolUseLookup,
    )

    if (!startState) {
      result.push({
        kind: 'message',
        item: startEntry,
      })
      index += 1
      continue
    }

    const groupedEntries: RenderedMessageEntry[] = []
    const groupedStates = new Map<string, CollapsibleToolState>()

    while (index < entries.length) {
      const candidate = entries[index]!
      const candidateState = getRenderedEntryCollapsibleToolState(
        candidate,
        toolUseLookup,
      )
      if (!candidateState) {
        break
      }
      groupedEntries.push(candidate)
      updateCollapsibleToolState(groupedStates, candidateState)
      index += 1
    }

    const groupedValues = [...groupedStates.values()]
    const searchCount = groupedValues.filter(state => state.kind === 'search').length
    const readCount = groupedValues.filter(state => state.kind === 'read').length
    const listCount = groupedValues.filter(state => state.kind === 'list').length
    const lastEntry = groupedEntries[groupedEntries.length - 1]
    const hint = [...groupedValues]
      .reverse()
      .map(state => state.hint)
      .find(Boolean)
    const isActive =
      groupedValues.some(
        state =>
          (state.hasToolUse || state.hasProgress) &&
          !state.hasToolResult,
      ) ||
      Boolean(isLoading && lastEntry === entries[entries.length - 1])

    result.push({
      kind: 'collapsed_read_search',
      key: groupedEntries.map(entry => entry.key).join(':'),
      messages: groupedEntries,
      searchCount,
      readCount,
      listCount,
      hint,
      isActive,
    })
  }

  return result
}

function pickSpinnerVerb(): string {
  return SPINNER_VERBS[Math.floor(Math.random() * SPINNER_VERBS.length)]!
}

function pickSpinnerTip(messages: MessageType[]): string {
  const userTurnCount = messages.filter(message => message.type === 'user').length

  if (userTurnCount <= 1) {
    return SPINNER_TIPS[0]
  }

  return SPINNER_TIPS[userTurnCount % SPINNER_TIPS.length]!
}

function useCcminiInboxSummary(
  baseUrl: string,
  authToken: string,
): {
  lines: string[]
  error: string | null
} {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const root = baseUrl.replace(/\/$/, '')

    const poll = async (): Promise<void> => {
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
        if (cancelled) {
          return
        }

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
    const intervalId = setInterval(() => {
      void poll()
    }, 8000)

    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [authToken, baseUrl])

  return {
    lines,
    error,
  }
}

function useCcminiTasksV2(
  baseUrl: string,
  authToken: string,
  sessionId: string,
): {
  tasks: CcminiTaskBoardTask[]
  hidden: boolean
  error: string | null
  planState: PlanPanelState
} {
  const [tasks, setTasks] = useState<CcminiTaskBoardTask[]>([])
  const [hidden, setHidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [planState, setPlanState] = useState<PlanPanelState>({ mode: 'idle' })

  useEffect(() => {
    let cancelled = false
    const root = baseUrl.replace(/\/$/, '')
    const encodedSessionId = encodeURIComponent(sessionId)
    let intervalId: ReturnType<typeof setInterval> | null = null
    let hideTimer: ReturnType<typeof setTimeout> | null = null

    const resetHideTimer = (): void => {
      if (hideTimer) {
        clearTimeout(hideTimer)
        hideTimer = null
      }
    }

    const scheduleHide = (): void => {
      resetHideTimer()
      hideTimer = setTimeout(() => {
        if (!cancelled) {
          setHidden(true)
        }
      }, 5000)
    }

    const resetPoll = (hasActiveTasks: boolean): void => {
      if (intervalId) {
        clearInterval(intervalId)
      }
      intervalId = setInterval(() => {
        void poll()
      }, hasActiveTasks ? 1000 : 4000)
    }

    const poll = async (): Promise<void> => {
      try {
        const response = await fetch(
          `${root}/api/tasks?session_id=${encodedSessionId}&include_completed=true`,
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
          tasks?: unknown[]
          planState?: unknown
        }
        if (cancelled) {
          return
        }

        const nextTasks = Array.isArray(payload.tasks)
          ? payload.tasks
              .map(raw => asRecord(raw))
              .filter((record): record is Record<string, unknown> => record !== null)
              .map(record => ({
                id: typeof record.id === 'string' ? record.id : '',
                subject: typeof record.subject === 'string' ? record.subject : '',
                description: typeof record.description === 'string' ? record.description : '',
                activeForm: typeof record.activeForm === 'string' ? record.activeForm : undefined,
                owner: typeof record.owner === 'string' ? record.owner : undefined,
                ownerIsActive:
                  typeof record.ownerIsActive === 'boolean'
                    ? record.ownerIsActive
                    : undefined,
                status: normalizePlannerTaskStatus(record.status),
                blocks: Array.isArray(record.blocks)
                  ? record.blocks.filter((value): value is string => typeof value === 'string')
                  : [],
                blockedBy: Array.isArray(record.blockedBy)
                  ? record.blockedBy.filter((value): value is string => typeof value === 'string')
                  : [],
                metadata: asRecord(record.metadata) ?? undefined,
              }))
              .filter(task => task.id && task.subject)
          : []
        const nextPlanState = parseRuntimePlanPanelState(payload.planState)

        setError(null)
        setTasks(nextTasks)
        setPlanState(nextPlanState)
        const hasIncomplete = nextTasks.some(task => task.status !== 'completed')
        if (hasIncomplete || nextTasks.length === 0) {
          setHidden(false)
          resetHideTimer()
        } else {
          scheduleHide()
        }
        resetPoll(hasIncomplete)
      } catch (fetchError) {
        if (!cancelled) {
          setError(
            fetchError instanceof Error
              ? fetchError.message
              : 'tasks fetch failed',
          )
        }
      }
    }

    void poll()

    return () => {
      cancelled = true
      if (intervalId) {
        clearInterval(intervalId)
      }
      resetHideTimer()
    }
  }, [authToken, baseUrl, sessionId])

  return {
    tasks: hidden ? [] : tasks,
    hidden,
    error,
    planState,
  }
}

function extractPlanPanelState(
  messages: MessageType[],
  toolUseLookup: Map<string, ToolUseLookupEntry>,
): PlanPanelState {
  let latestEnterIndex = -1
  let latestExitIndex = -1
  let latestPlanText = ''

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index]!
    const toolUse = getAssistantToolUseBlock(message)
    if (toolUse?.id) {
      const toolName = String(toolUse.name ?? '').trim().toLowerCase()
      if (toolName === 'enterplanmode') {
        latestEnterIndex = index
      } else if (toolName === 'exitplanmode') {
        latestExitIndex = index
      }
      continue
    }

    const toolResult = getUserToolResultBlock(message)
    if (!toolResult?.tool_use_id) {
      continue
    }
    const tool = toolUseLookup.get(toolResult.tool_use_id)
    if (String(tool?.name ?? '').trim().toLowerCase() !== 'exitplanmode') {
      continue
    }
    const raw = String(message.toolUseResult ?? toolResult.content ?? '').trim()
    if (!raw) {
      continue
    }
    latestExitIndex = index
    const marker = '## Implementation Plan'
    latestPlanText = raw.includes(marker) ? raw.slice(raw.indexOf(marker)).trim() : raw
  }

  if (latestExitIndex >= 0 && latestExitIndex >= latestEnterIndex && latestPlanText) {
    return createReadyPlanPanelState(latestPlanText)
  }

  if (latestEnterIndex > latestExitIndex) {
    return createActivePlanPanelState()
  }

  return {
    mode: 'idle',
  }
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
      const toolResult = getUserToolResultBlock(message)
      if (toolResult) {
        return [summarizeToolResultText(message.toolUseResult ?? toolResult.content)]
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

function renderInputLine(inputValue: string, cursorOffset: number): React.ReactNode {
  if (inputValue.length === 0) {
    return (
      <Text>
        <Text inverse>{' '}</Text>
        <Text dimColor>{DEFAULT_INPUT_PLACEHOLDER}</Text>
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
  const isTerminalFocused = useTerminalFocus()
  const cursorRef = useDeclaredCursor({
    line: cursorLine,
    column: cursorColumn,
    active: isTerminalFocused,
  })

  return (
    <Box ref={cursorRef} flexGrow={1} flexShrink={1}>
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

function PromptFooter({
  themeSetting,
}: {
  themeSetting: ThemeSetting
}): React.ReactNode {
  return null
}

function PromptHelpMenu({
  themeSetting,
  columns,
}: {
  themeSetting: ThemeSetting
  columns: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const compact = columns < 86

  return (
    <Box marginTop={1}>
      <PanelFrame title="Shortcuts" subtitle="/help" themeSetting={themeSetting}>
        <Box flexDirection={compact ? 'column' : 'row'} gap={4} paddingX={1}>
          <Box flexDirection="column" width={compact ? undefined : 28}>
            <Text>{applyForeground('Prompt commands', theme.claude)}</Text>
            <Text dimColor>/ for commands</Text>
            <Text dimColor>@ for file paths</Text>
            <Text dimColor>& for background</Text>
            <Text dimColor>/btw for side question</Text>
            <Text dimColor>/theme for text style</Text>
          </Box>
          <Box flexDirection="column" width={compact ? undefined : 40}>
            <Text>{applyForeground('Keyboard flow', theme.claude)}</Text>
            <Text dimColor>double tap esc to clear input</Text>
            <Text dimColor>shift + tab to auto-accept edits</Text>
            <Text dimColor>ctrl + o for verbose output</Text>
            <Text dimColor>ctrl + t to toggle syntax preview</Text>
            <Text dimColor>shift + ⏎ for newline</Text>
          </Box>
        </Box>
      </PanelFrame>
    </Box>
  )
}

function CommandCatalogPanel({
  entries,
  selectedIndex,
  query,
  themeSetting,
}: {
  entries: DonorCommandCatalogEntry[]
  selectedIndex: number
  query: string
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
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
    <Box marginTop={1}>
      <PanelFrame
        title="Commands"
        subtitle={query ? `/${query}` : '/'}
        themeSetting={themeSetting}
      >
        <Box flexDirection="column" gap={1} paddingX={1}>
          <Text dimColor>
            {entries.length === 0
              ? `No extracted donor commands match "/${query}"`
              : `Showing ${entries.length} extracted donor commands for "/${query}"`}
          </Text>
          {visibleEntries.length > 0 ? (
            <Box flexDirection="column">
              {visibleEntries.map((entry, index) => {
                const actualIndex = windowStart + index
                const line = `/${entry.name}${entry.argumentHint ? ` ${entry.argumentHint}` : ''}`
                return (
                  <Text key={entry.sourcePath} dimColor={actualIndex !== selectedIndex}>
                    {actualIndex === selectedIndex
                      ? applyBackground(
                          applyForeground(` ${DONOR_POINTER} ${line} `, theme.inverseText),
                          theme.permission,
                        )
                      : `  ${line}`}
                    <Text dimColor> [{getCommandStatusLabel(entry)}]</Text>
                  </Text>
                )
              })}
            </Box>
          ) : null}
          {selectedEntry ? (
            <Box flexDirection="column">
              {describeDonorCommand(selectedEntry).map((line, index) => (
                <Text key={`${selectedEntry.sourcePath}-${index}`} dimColor={index > 0} wrap="wrap">
                  {line}
                </Text>
              ))}
            </Box>
          ) : null}
          <Text dimColor italic>
            Up/Down choose  Tab inserts  Enter runs current input  Esc cancels
          </Text>
        </Box>
      </PanelFrame>
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

function WorkingStatusFlow({
  verb,
  tip,
  themeSetting,
}: {
  verb: string
  tip: string | null
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text>{applyForeground(`${verb}...`, theme.claude)}</Text>
      {tip ? (
        <MessageResponseFlow>
          <Text dimColor wrap="wrap">
            {`Tip: ${tip}`}
          </Text>
        </MessageResponseFlow>
      ) : null}
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
    <Box marginTop={1}>
      <PanelFrame
        title="Theme"
        subtitle="Preview"
        themeSetting={previewThemeSetting}
        titleColor={theme.permission}
      >
        <Box flexDirection="column" gap={1} paddingX={1}>
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
      </PanelFrame>
    </Box>
  )
}

function CcminiInboxPanel({
  lines,
  error,
  themeSetting,
}: {
  lines: string[]
  error: string | null
  themeSetting: ThemeSetting
}): React.ReactNode {
  if (error && lines.length === 0) {
    return (
      <Box marginBottom={1}>
        <PanelFrame title="Recent activity" themeSetting={themeSetting}>
          <Box paddingX={1}>
            <Text dimColor>inbox: {error}</Text>
          </Box>
        </PanelFrame>
      </Box>
    )
  }

  if (lines.length === 0 && !error) {
    return null
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      <PanelFrame title="Recent activity" themeSetting={themeSetting}>
        <Box flexDirection="column" paddingX={1}>
          {error ? (
            <Text color="red" wrap="wrap">
              {error}
            </Text>
          ) : null}
          {lines.map((line, index) => (
            <Text key={index} dimColor wrap="wrap">
              {line}
            </Text>
          ))}
        </Box>
      </PanelFrame>
    </Box>
  )
}

function CcminiAskUserQuestionEditor({
  call,
  columns,
  onSubmit,
  onAbort,
  themeSetting,
}: {
  call: CcminiPendingToolCall
  columns: number
  onSubmit: (results: Array<{
    tool_use_id: string
    content: string
    is_error?: boolean
  }>) => void | Promise<void>
  onAbort: () => void
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const questions = useMemo(
    () => parseAskUserQuestions(call.toolInput),
    [call.toolInput],
  )
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [reviewActionIndex, setReviewActionIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, AskUserQuestionAnswer>>({})
  const [textMode, setTextMode] = useState(false)
  const [textValue, setTextValue] = useState('')
  const [textCursorOffset, setTextCursorOffset] = useState(0)

  const reviewIndex = questions.length
  const inReviewStep = currentQuestionIndex >= reviewIndex
  const currentQuestion = inReviewStep ? null : questions[currentQuestionIndex] ?? null
  const currentQuestionKey = currentQuestion
    ? getAskUserQuestionKey(currentQuestion)
    : ''
  const currentAnswer =
    answers[currentQuestionKey] ?? createEmptyAskUserQuestionAnswer()
  const answeredCount = useMemo(
    () =>
      questions.filter(question =>
        hasAskUserQuestionAnswer(
          answers[getAskUserQuestionKey(question)] ??
            createEmptyAskUserQuestionAnswer(),
        ),
      ).length,
    [answers, questions],
  )
  const allQuestionsAnswered = answeredCount === questions.length && questions.length > 0

  useEffect(() => {
    if (!currentQuestion || inReviewStep) {
      return
    }
    const nextAnswer =
      answers[getAskUserQuestionKey(currentQuestion)] ??
      createEmptyAskUserQuestionAnswer()
    setSelectedIndex(0)
    setTextMode(false)
    setTextValue(nextAnswer.freeformText)
    setTextCursorOffset(nextAnswer.freeformText.length)
  }, [currentQuestion, currentQuestionKey, inReviewStep])

  const textInputState = useTextInput({
    value: textValue,
    onChange: setTextValue,
    onSubmit: undefined,
    onExit: undefined,
    onHistoryUp: () => {},
    onHistoryDown: () => {},
    onHistoryReset: () => {},
    onClearInput: () => {
      setTextValue('')
      setTextCursorOffset(0)
    },
    focus: false,
    multiline: false,
    cursorChar: ' ',
    invert: value => chalk.inverse(value),
    themeText: value => value,
    columns: Math.max(16, columns - 12),
    disableEscapeDoublePress: true,
    externalOffset: textCursorOffset,
    onOffsetChange: setTextCursorOffset,
  })

  const submitAnswers = useCallback(
    async (nextAnswers: Record<string, AskUserQuestionAnswer>) => {
      await onSubmit([
        {
          tool_use_id: call.toolUseId,
          content: buildAskUserQuestionToolResult(
            call.toolInput,
            questions,
            nextAnswers,
          ),
        },
      ])
    },
    [call.toolUseId, onSubmit, questions],
  )

  const submitCancellation = useCallback(async () => {
    await onSubmit([
      {
        tool_use_id: call.toolUseId,
        content: 'User canceled AskUserQuestion.',
        is_error: true,
      },
    ])
  }, [call.toolUseId, onSubmit])

  const advanceQuestion = useCallback(
    async (nextAnswer: AskUserQuestionAnswer, mode: 'advance' | 'submit' = 'advance') => {
      if (!currentQuestion) {
        return
      }

      const nextAnswers = {
        ...answers,
        [currentQuestionKey]: nextAnswer,
      }
      setAnswers(nextAnswers)

      if (mode === 'submit') {
        await submitAnswers(nextAnswers)
        return
      }

      if (questions.length === 1 && !currentQuestion.allowMultiple) {
        await submitAnswers(nextAnswers)
        return
      }

      if (currentQuestionIndex >= questions.length - 1) {
        setCurrentQuestionIndex(reviewIndex)
        setReviewActionIndex(0)
        return
      }

      setCurrentQuestionIndex(prev => prev + 1)
    },
    [
      answers,
      currentQuestion,
      currentQuestionIndex,
      currentQuestionKey,
      questions.length,
      reviewIndex,
      submitAnswers,
    ],
  )

  useInput(
    (input, key) => {
      if (inReviewStep) {
        if (key.escape || (key.ctrl && input === 'c')) {
          void submitCancellation()
          return
        }

        if (key.leftArrow || (key.ctrl && input === 'p')) {
          if (questions.length > 0) {
            setCurrentQuestionIndex(Math.max(0, questions.length - 1))
            setReviewActionIndex(0)
          }
          return
        }

        if (key.upArrow) {
          setReviewActionIndex(prev => (prev === 0 ? 1 : 0))
          return
        }

        if (key.downArrow || key.tab) {
          setReviewActionIndex(prev => (prev === 0 ? 1 : 0))
          return
        }

        if (!key.return) {
          return
        }

        if (reviewActionIndex === 0) {
          void submitAnswers(answers)
        } else {
          void submitCancellation()
        }
        return
      }

      if (!currentQuestion) {
        return
      }

      if (textMode) {
        if (key.escape || (key.ctrl && input === 'c')) {
          setTextMode(false)
          setTextValue(currentAnswer.freeformText)
          setTextCursorOffset(currentAnswer.freeformText.length)
          return
        }

        if (key.return && !key.shift && !key.meta) {
          const trimmed = textValue.trim()
          if (!trimmed) {
            return
          }
          void advanceQuestion({
            ...currentAnswer,
            freeformText: trimmed,
          }, 'advance')
          return
        }

        textInputState.onInput(input, key)
        return
      }

      const actionCount = currentQuestion.allowMultiple ? 2 : 1
      const itemCount = currentQuestion.options.length + actionCount
      const submitIndex = currentQuestion.allowMultiple
        ? currentQuestion.options.length
        : -1
      const chatIndex = currentQuestion.options.length + actionCount - 1

      if (key.escape || (key.ctrl && input === 'c')) {
        void submitCancellation()
        return
      }

      if (key.upArrow || (key.ctrl && input === 'p')) {
        setSelectedIndex(prev => (prev === 0 ? itemCount - 1 : prev - 1))
        return
      }

      if (key.leftArrow) {
        if (currentQuestionIndex > 0) {
          setCurrentQuestionIndex(prev => Math.max(0, prev - 1))
        }
        return
      }

      if (key.rightArrow) {
        if (currentQuestionIndex < questions.length - 1) {
          setCurrentQuestionIndex(prev => Math.min(questions.length - 1, prev + 1))
        } else if (questions.length > 1) {
          setCurrentQuestionIndex(reviewIndex)
          setReviewActionIndex(0)
        }
        return
      }

      if (key.downArrow || key.tab || (key.ctrl && input === 'n')) {
        setSelectedIndex(prev => (prev + 1) % itemCount)
        return
      }

      const shouldActivate =
        key.return ||
        (currentQuestion.allowMultiple &&
          input === ' ' &&
          selectedIndex < currentQuestion.options.length)

      if (!shouldActivate) {
        return
      }

      if (selectedIndex < currentQuestion.options.length) {
        const option = currentQuestion.options[selectedIndex]!

        if (currentQuestion.allowMultiple) {
          setAnswers(prev => ({
            ...prev,
            [currentQuestionKey]: toggleAskUserQuestionOption(
              prev[currentQuestionKey] ?? createEmptyAskUserQuestionAnswer(),
              option,
            ),
          }))
          return
        }

        void advanceQuestion({
          selectedOptionIds: [option.id],
          selectedLabels: [option.label],
          freeformText: currentAnswer.freeformText,
        }, 'advance')
        return
      }

      if (currentQuestion.allowMultiple && selectedIndex === submitIndex) {
        if (!hasAskUserQuestionAnswer(currentAnswer)) {
          return
        }
        void advanceQuestion(currentAnswer, 'advance')
        return
      }

      if (selectedIndex === chatIndex) {
        setTextMode(true)
        setTextCursorOffset(textValue.length)
      }
    },
    { isActive: true },
  )

  if (!currentQuestion) {
    if (!inReviewStep) {
      return null
    }
  }

  const submitIndex = currentQuestion?.allowMultiple
    ? currentQuestion.options.length
    : -1
  const chatIndex =
    currentQuestion
      ? currentQuestion.options.length + (currentQuestion.allowMultiple ? 1 : 0)
      : -1
  const canSubmitSelection = currentQuestion
      ? hasAskUserQuestionAnswer(currentAnswer)
    : false
  const stepLabel =
    inReviewStep
      ? `${ASK_USER_QUESTION_ICONS.tick} Submit`
      : questions.length > 1
      ? `${currentQuestionIndex + 1}/${questions.length}`
      : undefined

  return (
    <Box marginTop={1}>
      <PanelFrame
        title="Question"
        subtitle={stepLabel}
        themeSetting={themeSetting}
        titleColor={theme.permission}
      >
        <Box flexDirection="column" paddingX={1}>
          {questions.length > 1 ? (
            <Box marginBottom={1} flexDirection="row" flexWrap="wrap">
              <Text color={currentQuestionIndex === 0 ? theme.subtle : undefined}>
                {'<- '}
              </Text>
              {questions.map((question, index) => {
                const key = getAskUserQuestionKey(question)
                const answered = hasAskUserQuestionAnswer(
                  answers[key] ?? createEmptyAskUserQuestionAnswer(),
                )
                const tab = `${answered ? '[x]' : '[ ]'} ${getAskUserQuestionHeader(question, index)}`
                return (
                  <Text key={key} wrap="wrap">
                    {index === currentQuestionIndex
                      ? applyBackground(
                          applyForeground(` ${tab} `, theme.inverseText),
                          theme.permission,
                        )
                      : ` ${tab} `}
                  </Text>
                )
              })}
              <Text wrap="wrap">
                {inReviewStep
                  ? applyBackground(
                      applyForeground(
                        ` ${ASK_USER_QUESTION_ICONS.tick} Submit `,
                        theme.inverseText,
                      ),
                      theme.permission,
                    )
                  : ` ${ASK_USER_QUESTION_ICONS.tick} Submit `}
              </Text>
              <Text
                color={
                  currentQuestionIndex === reviewIndex
                    ? theme.subtle
                    : undefined
                }
              >
                {' ->'}
              </Text>
            </Box>
          ) : null}

          {inReviewStep ? (
            <Box flexDirection="column">
              <Text bold>Review your answers</Text>
              <Box marginTop={1} flexDirection="column">
                {questions.map((question, index) => {
                  const answer =
                    answers[getAskUserQuestionKey(question)] ??
                    createEmptyAskUserQuestionAnswer()
                  return (
                    <Box key={question.id || index} flexDirection="column" marginBottom={1}>
                      <Text>{`${ASK_USER_QUESTION_ICONS.bullet} ${question.prompt}`}</Text>
                      <Box marginLeft={2}>
                        <Text color="green">
                          {`${ASK_USER_QUESTION_ICONS.arrowRight} ${summarizeAskUserQuestionAnswer(answer)}`}
                        </Text>
                      </Box>
                    </Box>
                  )
                })}
              </Box>
              {!allQuestionsAnswered ? (
                <Text color="yellow">
                  {`${ASK_USER_QUESTION_ICONS.warning} You have not answered all questions`}
                </Text>
              ) : null}
              <Box marginTop={1} flexDirection="column">
                <Text wrap="wrap">
                  {reviewActionIndex === 0
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} Submit answers`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : '  Submit answers'}
                </Text>
                <Text wrap="wrap">
                  {reviewActionIndex === 1
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} Cancel`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : '  Cancel'}
                </Text>
              </Box>
              <Box marginTop={1}>
                <Text dimColor italic>
                  Enter to select · ↑/↓ to navigate · ← to go back · Esc to cancel
                </Text>
              </Box>
            </Box>
          ) : (
            <React.Fragment>
              <Text bold wrap="wrap">
                {currentQuestion?.prompt}
              </Text>

              <Box marginTop={1} flexDirection="column">
                {currentQuestion?.options.map((option, index) => {
                  const isActive = !textMode && selectedIndex === index
                  const isSelected =
                    currentAnswer.selectedOptionIds.includes(option.id)
                  const marker = currentQuestion.allowMultiple
                    ? `[${isSelected ? 'x' : ' '}]`
                    : `${index + 1}.`
                  const content = `${marker} ${option.label}`

                  return (
                    <Box key={option.id} flexDirection="column">
                      <Text wrap="wrap">
                        {isActive
                          ? applyBackground(
                              applyForeground(
                                `${DONOR_POINTER} ${content}`,
                                theme.inverseText,
                              ),
                              theme.permission,
                            )
                          : `  ${content}`}
                      </Text>
                      {option.description ? (
                        <Text dimColor wrap="wrap">
                          {`    ${option.description}`}
                        </Text>
                      ) : null}
                    </Box>
                  )
                })}

                {currentQuestion?.allowMultiple ? (
                  <Text wrap="wrap">
                    {!textMode && selectedIndex === submitIndex
                      ? applyBackground(
                          applyForeground(
                            `${DONOR_POINTER} ${currentQuestion.options.length + 1}. Submit selection`,
                            theme.inverseText,
                          ),
                          canSubmitSelection
                            ? theme.permission
                            : theme.userMessageBackground,
                        )
                      : `  ${currentQuestion.options.length + 1}. Submit selection`}
                  </Text>
                ) : null}

                <Text wrap="wrap">
                  {!textMode && selectedIndex === chatIndex
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} ${currentQuestion.options.length + (currentQuestion.allowMultiple ? 2 : 1)}. Chat about this`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : `  ${currentQuestion.options.length + (currentQuestion.allowMultiple ? 2 : 1)}. Chat about this`}
                </Text>
              </Box>

              {textMode ? (
                <Box flexDirection="column" marginTop={1}>
                  <Text dimColor>Type something.</Text>
                  <Box marginTop={1} flexDirection="row">
                    <Text dimColor>{applyForeground(`${DONOR_POINTER} `, theme.subtle)}</Text>
                    <MainInputLine
                      inputValue={textValue}
                      renderedValue={textInputState.renderedValue}
                      cursorLine={textInputState.cursorLine}
                      cursorColumn={textInputState.cursorColumn}
                    />
                  </Box>
                  <Box marginTop={1}>
                    <Text dimColor italic>
                      Enter to submit · Esc to go back
                    </Text>
                  </Box>
                </Box>
              ) : (
                <Box marginTop={1}>
                  <Text dimColor italic>
                    {currentQuestion?.allowMultiple
                      ? 'Space/Enter to toggle · ↑/↓ to navigate · ←/→ switch question · Esc to cancel'
                      : 'Enter to select · ↑/↓ to navigate · ←/→ switch question · Esc to cancel'}
                  </Text>
                </Box>
              )}
            </React.Fragment>
          )}
        </Box>
      </PanelFrame>
    </Box>
  )
}

function CcminiPendingToolRequestPanel({
  runId,
  toolName,
  description,
  callCount,
  themeSetting,
}: {
  runId: string
  toolName: string
  description: string
  callCount: number
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box marginTop={1}>
      <PanelFrame
        title="Continuation queue"
        subtitle={toolName}
        themeSetting={themeSetting}
        titleColor={theme.warning}
      >
        <Box flexDirection="column" paddingX={1}>
          <Text wrap="wrap">{description}</Text>
          <Text dimColor>{`Run: ${runId}`}</Text>
          <Text dimColor>
            The remote executor is waiting for client-side tool results.
          </Text>
          {callCount > 1 ? (
            <Text dimColor>{callCount} tool results are waiting to be submitted.</Text>
          ) : null}
        </Box>
      </PanelFrame>
    </Box>
  )
}

function CcminiToolResultEditor({
  runId,
  calls,
  onSubmit,
  onAbort,
  themeSetting,
}: {
  runId: string
  calls: CcminiPendingToolCall[]
  onSubmit: (results: Array<{
    tool_use_id: string
      content: string
      is_error?: boolean
    }>) => void | Promise<void>
  onAbort: () => void
  themeSetting: ThemeSetting
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
    <Box marginTop={1}>
      <PanelFrame
        title={calls.length === 1 ? 'Tool result' : 'Tool results'}
        subtitle={`${completedCount}/${calls.length} ready`}
        themeSetting={themeSetting}
      >
        <Box flexDirection="column" paddingX={1}>
          <Text dimColor>{`Run ${runId} is waiting for client-side tool results.`}</Text>

          {calls.length > 1 ? (
            <Box flexDirection="column" marginTop={1}>
              {calls.map((call, index) => {
                const status = savedResults[index]
                  ? 'ready'
                  : drafts[index]
                    ? 'draft'
                    : 'pending'
                return (
                  <Text key={call.toolUseId} bold={index === activeIndex}>
                    {index === activeIndex ? DONOR_POINTER : ' '} {call.toolName} [{status}]
                  </Text>
                )
              })}
            </Box>
          ) : null}

          <Box flexDirection="column" marginTop={1}>
            <Text>{`Tool: ${activeCall.toolName}`}</Text>
            <Text dimColor>{`Tool use: ${activeCall.toolUseId}`}</Text>
            {summarizeToolCall(activeCall).map((line, index) => (
              <Text key={`${activeCall.toolUseId}-${index}`} dimColor={index > 0} wrap="wrap">
                {line}
              </Text>
            ))}
          </Box>

          <Text dimColor wrap="wrap">
            Enter saves this result. Shift+Enter inserts a newline. Prefix with{' '}
            <Text bold>error:</Text>{' '}to submit an error result. Esc cancels.
          </Text>

          <Box flexDirection="row" marginTop={1}>
            <Text>
              {DONOR_POINTER}
              {' '}
            </Text>
            {renderInputLine(drafts[activeIndex] ?? '', cursorOffset)}
          </Box>
        </Box>
      </PanelFrame>
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
  const [spinnerVerb, setSpinnerVerb] = useState<string>(() => pickSpinnerVerb())
  const [spinnerTip, setSpinnerTip] = useState<string | null>(null)
  const [pendingCcminiToolRequest, setPendingCcminiToolRequest] =
    useState<CcminiPendingToolRequest | null>(null)
  const [promptSuggestion, setPromptSuggestion] =
    useState<CcminiPromptSuggestionState>(EMPTY_PROMPT_SUGGESTION_STATE)
  const [speculation, setSpeculation] =
    useState<CcminiSpeculationState>(IDLE_SPECULATION_STATE)
  const managerRef = useRef<CcminiSessionManager | null>(null)
  const wasLoadingRef = useRef(false)
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
  const inboxSummary = useCcminiInboxSummary(
    ccminiConnectConfig.baseUrl,
    ccminiConnectConfig.authToken,
  )
  const tasksV2 = useCcminiTasksV2(
    ccminiConnectConfig.baseUrl,
    ccminiConnectConfig.authToken,
    ccminiConnectConfig.sessionId,
  )
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

  useEffect(() => {
    if (isLoading && !wasLoadingRef.current) {
      setSpinnerVerb(pickSpinnerVerb())
      setSpinnerTip(pickSpinnerTip(messages))
    }

    if (!isLoading && wasLoadingRef.current) {
      setSpinnerTip(null)
    }

    wasLoadingRef.current = isLoading
  }, [isLoading, messages])

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
    setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
    setSpeculation(IDLE_SPECULATION_STATE)

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
        setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
        setSpeculation(IDLE_SPECULATION_STATE)
        setIsLoading(false)
      },
      onError: error => {
        setConnectionStatus('disconnected')
        setPendingCcminiToolRequest(null)
        setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
        setSpeculation(IDLE_SPECULATION_STATE)
        setIsLoading(false)
        setMessages(prev => appendSystemMessageOnce(prev, error.message, 'error'))
      },
      onEvent: event => {
        if (event.type === 'stream_event') {
          const eventType = event.payload?.event_type
          if (eventType === 'request_start') {
            setIsLoading(true)
          } else if (eventType === 'prompt_suggestion') {
            setPromptSuggestion({
              text: String(event.payload?.text ?? ''),
              shownAt: Number(event.payload?.shown_at ?? 0),
              acceptedAt: Number(event.payload?.accepted_at ?? 0),
            })
          } else if (eventType === 'speculation') {
            const rawBoundary =
              typeof event.payload?.boundary === 'object' && event.payload.boundary !== null
                ? (event.payload.boundary as Record<string, unknown>)
                : {}
            setSpeculation({
              status: String(event.payload?.status ?? 'idle'),
              suggestion: String(event.payload?.suggestion ?? ''),
              reply: String(event.payload?.reply ?? ''),
              startedAt: Number(event.payload?.started_at ?? 0),
              completedAt: Number(event.payload?.completed_at ?? 0),
              error: String(event.payload?.error ?? ''),
              boundary: {
                type: String(rawBoundary.type ?? ''),
                toolName: String(rawBoundary.tool_name ?? ''),
                detail: String(rawBoundary.detail ?? ''),
                filePath: String(rawBoundary.file_path ?? ''),
                completedAt: Number(rawBoundary.completed_at ?? 0),
              },
            })
          }
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
            setIsLoading(false)
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
      setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
      setSpeculation(IDLE_SPECULATION_STATE)
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
      setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
      setSpeculation(IDLE_SPECULATION_STATE)
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
    setPromptSuggestion(EMPTY_PROMPT_SUGGESTION_STATE)
    setSpeculation(IDLE_SPECULATION_STATE)
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

      if (
        key.tab &&
        !commandCatalogActive &&
        !showThemePicker &&
        !showPromptHelp &&
        !inputValueRef.current.trim() &&
        promptSuggestion.text
      ) {
        applyMainInputState(promptSuggestion.text, promptSuggestion.text.length)
        return
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
          if (message.message.type === 'thinking' && !showFullThinking) {
            return false
          }
          return !(
            message.message.type === 'system' &&
            firstLine.startsWith('ccmini transport connected:')
          )
        })
        .slice(-visibleMessageCount),
    [showFullThinking, visibleMessageCount, renderedMessages],
  )
  const toolUseLookup = useMemo(() => buildToolUseLookup(messages), [messages])
  const transcriptPlanPanelState = useMemo(
    () => extractPlanPanelState(messages, toolUseLookup),
    [messages, toolUseLookup],
  )
  const effectivePlanPanelState = useMemo(
    () => mergePlanPanelStates(tasksV2.planState, transcriptPlanPanelState),
    [tasksV2.planState, transcriptPlanPanelState],
  )
  const plannerTasks = useMemo(
    () => derivePlannerTasks(messages, toolUseLookup),
    [messages, toolUseLookup],
  )
  const displayEntries = useMemo(
    () =>
      showFullThinking
        ? visibleMessages.map(item => ({
            kind: 'message' as const,
            item,
          }))
        : collapseReadSearchEntries(
            visibleMessages,
            toolUseLookup,
            isLoading,
          ),
    [showFullThinking, visibleMessages, toolUseLookup, isLoading],
  )

  const showWelcome = visibleMessages.length === 0
  const columns = stdout.columns ?? 100
  const messageWidth = Math.max(20, columns - 10)
  const userMessageWidth = Math.max(20, columns - 2)
  const inputWidth = Math.max(8, columns - 4)
  const recentActivityLines = useMemo(
    () => getRecentActivityPreview(messages, inboxSummary.lines),
    [inboxSummary.lines, messages],
  )
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
  const showPromptSuggestionHint =
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    !showVisiblePromptHelp &&
    !pendingCcminiToolRequest &&
    !trimmedInputValue &&
    Boolean(promptSuggestion.text)
  const speculationHint = describeSpeculationStatus(speculation, inputValue)
  const showSpeculationHint =
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    !showVisiblePromptHelp &&
    !pendingCcminiToolRequest &&
    (!trimmedInputValue || trimmedInputValue === speculation.suggestion.trim()) &&
    Boolean(speculationHint)
  const showBuddyCompanion =
    !showWelcome &&
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    !pendingCcminiToolRequest
  const taskPanelMode =
    !showWelcome &&
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    (tasksV2.tasks.length > 0
      ? 'tasks_v2'
      : plannerTasks.length > 0 || effectivePlanPanelState.mode !== 'idle'
        ? 'todo'
        : 'none')
  const showAskUserQuestionEditor =
    Boolean(
      pendingCcminiToolRequest &&
        pendingCcminiCalls.length === 1 &&
        isAskUserQuestionPendingTool(firstPendingCcminiToolCall) &&
        parseAskUserQuestions(firstPendingCcminiToolCall?.toolInput).length > 0,
    )

  return (
    <Box flexDirection="column">
      {showWelcome ? (
        <WelcomeDashboard
          themeSetting={activeThemeSetting}
          columns={columns}
          connectionStatus={connectionStatus}
          baseUrl={ccminiConnectConfig.baseUrl}
          donorCommandCount={donorCommandCatalog.length}
          recentActivityLines={recentActivityLines}
        />
      ) : (
        <CompactStatusBar
          themeSetting={activeThemeSetting}
          connectionStatus={connectionStatus}
          baseUrl={ccminiConnectConfig.baseUrl}
          donorCommandCount={donorCommandCatalog.length}
        />
      )}

      {showWelcome ? (
        <React.Fragment />
      ) : (
        <Box flexDirection="column" marginTop={1}>
          {taskPanelMode === 'tasks_v2' ? (
            <TaskBoardPanel
              tasks={tasksV2.tasks}
              planState={effectivePlanPanelState}
              themeSetting={activeThemeSetting}
              width={messageWidth}
            />
          ) : taskPanelMode === 'todo' ? (
            <TaskPlannerPanel
              tasks={plannerTasks}
              planState={effectivePlanPanelState}
              themeSetting={activeThemeSetting}
              width={messageWidth}
            />
          ) : null}
          {displayEntries.map((entry, index) => {
            const previousEntry = index > 0 ? displayEntries[index - 1] : null

            return (
              <Box
                key={entry.kind === 'message' ? entry.item.key : entry.key}
                flexDirection="column"
                marginTop={
                  index === 0
                    ? 0
                    : entry.kind === 'message' &&
                        previousEntry?.kind === 'message' &&
                        shouldCollapseMessageGap(
                          previousEntry.item.message,
                          entry.item.message,
                        )
                      ? 0
                      : 1
                }
              >
              {entry.kind === 'collapsed_read_search' ? (
                <CollapsedReadSearchFlow
                  entry={entry}
                  width={messageWidth}
                />
              ) : entry.item.message.type === 'user' ? (
                (() => {
                  const message = entry.item
                  const toolResult = getUserToolResultBlock(message.message)
                  if (toolResult) {
                    const toolUse = toolUseLookup.get(toolResult.tool_use_id ?? '')
                    return (
                      <ToolResultFlow
                        rawResult={
                          message.message.toolUseResult ?? toolResult.content
                        }
                        toolName={toolUse?.name}
                        toolInput={toolUse?.input}
                        isError={Boolean(toolResult.is_error)}
                      />
                    )
                  }

                  return (
                    <UserPromptFlow
                      content={trimMessageLines(message.lines, 8).join('\n')}
                      addMargin={false}
                      themeSetting={activeThemeSetting}
                      width={userMessageWidth}
                    />
                  )
                })()
              ) : entry.item.message.type === 'thinking' ? (
                <ThinkingFlow
                  thinking={String(
                    (
                      entry.item.message as MessageType & {
                        thinking?: string
                      }
                    ).thinking ?? '',
                  )}
                  isRedacted={Boolean(
                    (
                      entry.item.message as MessageType & {
                        isRedacted?: boolean
                      }
                    ).isRedacted,
                  )}
                  verbose={showFullThinking}
                />
              ) : entry.item.message.type === 'assistant' ? (
                (() => {
                  const message = entry.item
                  const toolUse = getAssistantToolUseBlock(message.message)
                  if (toolUse) {
                    return (
                      <ToolUseFlow
                        toolName={toolUse.name ?? 'unknown'}
                        toolInput={toolUse.input}
                        width={messageWidth}
                      />
                    )
                  }

                  return (
                    <AssistantFlow
                      lines={message.lines}
                      width={messageWidth}
                    />
                  )
                })()
              ) : entry.item.message.type === 'progress' ? (
                <ToolProgressFlow
                  content={trimMessageLines(entry.item.lines, 4).join('\n')}
                  toolName={getProgressPayload(entry.item.message)?.toolName}
                />
              ) : entry.item.message.type === 'system' ? (
                entry.item.message.level === 'info' ? (
                  <MessageResponseFlow>
                    <Text dimColor wrap="wrap">
                      {trimMessageLines(entry.item.lines, 8).join('\n')}
                    </Text>
                  </MessageResponseFlow>
                ) : (
                  <SystemFlow
                    content={trimMessageLines(entry.item.lines, 8).join('\n')}
                    addMargin={false}
                    dot
                    color={entry.item.message.level === 'error' ? 'red' : 'yellow'}
                    dimColor={false}
                    width={messageWidth}
                  />
                )
              ) : (
                <MessageResponseFlow>
                  <Text dimColor wrap="wrap">
                    {trimMessageLines(entry.item.lines, 8).join('\n')}
                  </Text>
                </MessageResponseFlow>
              )}
              </Box>
            )
          })}
        </Box>
      )}

      {isLoading && !pendingCcminiToolRequest ? (
        <WorkingStatusFlow
          verb={spinnerVerb}
          tip={spinnerTip}
          themeSetting={activeThemeSetting}
        />
      ) : null}

      {pendingCcminiToolRequest &&
      firstPendingCcminiToolCall &&
      !showAskUserQuestionEditor ? (
        <CcminiPendingToolRequestPanel
          runId={pendingCcminiToolRequest.runId}
          toolName={firstPendingCcminiToolCall.toolName}
          description={firstPendingCcminiToolCall.description}
          callCount={pendingCcminiCalls.length}
          themeSetting={activeThemeSetting}
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
          themeSetting={activeThemeSetting}
        />
      ) : null}

      {!pendingCcminiToolRequest ? (
        <React.Fragment>
          <Box marginTop={1} flexDirection="row">
            <Text dimColor={isLoading}>{applyForeground(`${DONOR_POINTER} `, themeTokens.subtle)}</Text>
            <MainInputLine
              inputValue={inputValue}
              renderedValue={textInputState.renderedValue}
              cursorLine={textInputState.cursorLine}
              cursorColumn={textInputState.cursorColumn}
            />
          </Box>
          {showPromptSuggestionHint ? (
            <Box marginLeft={2}>
              <Text dimColor>
                Tab accepts suggestion: {applyForeground(promptSuggestion.text, themeTokens.claude)}
              </Text>
            </Box>
          ) : null}
          {showSpeculationHint ? (
            <Box marginLeft={2}>
              <Text dimColor>{speculationHint}</Text>
            </Box>
          ) : null}
          {showVisibleThemePicker
            ? null
            : showVisibleCommandCatalog
              ? null
              : showVisiblePromptHelp
                ? <PromptHelpMenu themeSetting={activeThemeSetting} columns={columns} />
                : <PromptFooter themeSetting={activeThemeSetting} />}
        </React.Fragment>
      ) : null}

      {showBuddyCompanion ? (
        <Box width="100%" justifyContent="flex-end">
          <BuddyCompanion
            themeSetting={activeThemeSetting}
            columns={columns}
          />
        </Box>
      ) : null}

      {pendingCcminiToolRequest && showAskUserQuestionEditor && firstPendingCcminiToolCall ? (
        <CcminiAskUserQuestionEditor
          key={pendingCcminiToolRequest.runId}
          call={firstPendingCcminiToolCall}
          columns={columns}
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
          themeSetting={activeThemeSetting}
        />
      ) : null}

      {pendingCcminiToolRequest && !showAskUserQuestionEditor ? (
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
          themeSetting={activeThemeSetting}
        />
      ) : null}
    </Box>
  )
}
