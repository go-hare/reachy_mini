import React from 'react'
import chalk from 'chalk'
import { Box, Text } from '../ink.js'
import { stringWidth } from '../ink/stringWidth.js'
import {
  getResolvedThemeSetting,
  getThemeTokens,
} from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'

type Props = {
  themeSetting: ThemeSetting
  columns: number
  version: string
  recentActivityLines: string[]
}

const MAX_LEFT_WIDTH = 50
const BORDER_PADDING = 4
const DIVIDER_WIDTH = 1
const CONTENT_PADDING = 2

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

function getFrameBorderColor(
  themeSetting: ThemeSetting,
): 'ansi:red' | 'ansi:redBright' {
  return getResolvedThemeSetting(themeSetting).startsWith('light')
    ? 'ansi:red'
    : 'ansi:redBright'
}

function truncateToWidth(value: string, maxLength: number): string {
  if (stringWidth(value) <= maxLength) {
    return value
  }
  return `${value.slice(0, Math.max(0, maxLength - 1))}…`
}

function truncatePath(path: string, maxLength: number): string {
  if (stringWidth(path) <= maxLength) {
    return path
  }

  const separator = path.includes('\\') ? '\\' : '/'
  const ellipsis = '…'
  const parts = path.split(separator)
  const first = parts[0] || ''
  const last = parts[parts.length - 1] || ''

  if (parts.length <= 1) {
    return truncateToWidth(path, maxLength)
  }

  if (first === '') {
    return `${separator}${truncateToWidth(last, Math.max(1, maxLength - 1))}`
  }

  const reserved = stringWidth(first) + stringWidth(last) + 3
  if (reserved >= maxLength) {
    return `${truncateToWidth(first, Math.max(1, maxLength - stringWidth(last) - 2))}${separator}${ellipsis}${separator}${last}`
  }

  return `${first}${separator}${ellipsis}${separator}${last}`
}

function calculateOptimalLeftWidth(
  welcomeMessage: string,
  truncatedCwd: string,
  modelLine: string,
): number {
  const contentWidth = Math.max(
    stringWidth(welcomeMessage),
    stringWidth(truncatedCwd),
    stringWidth(modelLine),
    20,
  )
  return Math.min(contentWidth + 4, MAX_LEFT_WIDTH)
}

function calculateLayoutDimensions(
  columns: number,
  optimalLeftWidth: number,
): {
  leftWidth: number
  rightWidth: number
} {
  const leftWidth = optimalLeftWidth
  const usedSpace =
    BORDER_PADDING + CONTENT_PADDING + DIVIDER_WIDTH + leftWidth
  const availableForRight = columns - usedSpace

  let rightWidth = Math.max(30, availableForRight)
  const totalWidth = Math.min(
    leftWidth + rightWidth + DIVIDER_WIDTH + CONTENT_PADDING,
    columns - BORDER_PADDING,
  )

  if (totalWidth < leftWidth + rightWidth + DIVIDER_WIDTH + CONTENT_PADDING) {
    rightWidth = totalWidth - leftWidth - DIVIDER_WIDTH - CONTENT_PADDING
  }

  return {
    leftWidth,
    rightWidth,
  }
}

function calculateFeedWidth(title: string, lines: string[]): number {
  let maxWidth = stringWidth(title)
  for (const line of lines) {
    maxWidth = Math.max(maxWidth, stringWidth(line))
  }
  return maxWidth
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

function FeedBlock({
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
  const isEmpty = lines.length === 0
  const visibleLines = isEmpty ? ['No recent activity'] : lines

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={theme.claude}>
        {title}
      </Text>
      {visibleLines.map((line, index) => (
        <Text key={index} dimColor={isEmpty} wrap="wrap">
          {line}
        </Text>
      ))}
    </Box>
  )
}

export function CcminiDonorWelcome({
  themeSetting,
  columns,
  version,
  recentActivityLines,
}: Props): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const welcomeMessage = 'Welcome back!'
  const modelLine = 'glm-5 · API Usage FREE!'
  const cwdLine = truncatePath(process.cwd(), MAX_LEFT_WIDTH - 4)
  const optimalLeftWidth = calculateOptimalLeftWidth(
    welcomeMessage,
    cwdLine,
    modelLine,
  )
  const { leftWidth, rightWidth } = calculateLayoutDimensions(
    columns,
    optimalLeftWidth,
  )

  const tipsLines = [
    'Run /init to create a CLAUDE.md file with instructions for Claude',
  ]
  const activityLines = recentActivityLines.slice(0, 1)
  const feedWidth = Math.min(
    Math.max(
      calculateFeedWidth('Tips for getting started', tipsLines),
      calculateFeedWidth('Recent activity', activityLines.length > 0 ? activityLines : ['No recent activity']),
    ),
    rightWidth,
  )

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={getFrameBorderColor(themeSetting)}
      borderText={{
        content: `${applyForeground(' Claude Code ', theme.claude)}${applyForeground(` v${version} `, theme.subtle)}`,
        position: 'top',
        align: 'start',
        offset: 3,
      }}
      width="100%"
    >
      <Box flexDirection="row" paddingX={1} gap={1}>
        <Box
          flexDirection="column"
          width={leftWidth}
          justifyContent="space-between"
          alignItems="center"
          minHeight={9}
        >
          <Box marginTop={1}>
            <Text bold>{welcomeMessage}</Text>
          </Box>
          <ClawdMascot themeSetting={themeSetting} />
          <Box flexDirection="column" alignItems="center">
            <Text dimColor>{modelLine}</Text>
            <Text dimColor>{cwdLine}</Text>
          </Box>
        </Box>
        <Box
          height="100%"
          borderStyle="single"
          borderColor={getFrameBorderColor(themeSetting)}
          borderTop={false}
          borderBottom={false}
          borderLeft={false}
        />
        <Box flexDirection="column" width={feedWidth}>
          <FeedBlock
            title="Tips for getting started"
            lines={tipsLines}
            width={feedWidth}
            themeSetting={themeSetting}
          />
          <Text color={theme.claude}>{'─'.repeat(feedWidth)}</Text>
          <FeedBlock
            title="Recent activity"
            lines={activityLines}
            width={feedWidth}
            themeSetting={themeSetting}
          />
        </Box>
      </Box>
    </Box>
  )
}
