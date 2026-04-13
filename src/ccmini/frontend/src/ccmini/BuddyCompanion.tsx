import * as React from 'react'
import { useMemo } from 'react'
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { Box, Text, useAnimationFrame } from '../ink.js'
import { stringWidth } from '../ink/stringWidth.js'
import { getThemeTokens } from './themePalette.js'
import type { ThemeSetting } from './themeTypes.js'

type BuddyConfig = {
  companionMuted?: unknown
  buddy_enabled?: unknown
  companion?: {
    name?: unknown
  }
}

type BuddyDisplayState = {
  name: string
}

const BUDDY_TICK_MS = 500
const SPRITE_PADDING_X = 1
const BUBBLE_WRAP_WIDTH = 22
const BUBBLE_BORDER_WIDTH = 4
const IDLE_SEQUENCE = [0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0] as const
const RABBIT_FRAMES = [
  [
    '   (\\__/)',
    '  ( ◉  ◉ )',
    ' =(  ..  )=',
    '  (")__(") ',
  ],
  [
    '   (|__/)',
    '  ( ◉  ◉ )',
    ' =(  ..  )=',
    '  (")__(") ',
  ],
  [
    '   (\\__/)',
    '  ( ◉  ◉ )',
    ' =( .  . )=',
    '  (")__(") ',
  ],
] as const
const NARROW_RABBIT_FRAMES = ['(◉..◉)', '(|..◉)', '(◉..-)', '(◉..◉)'] as const
const FULL_SPRITE_WIDTH = Math.max(
  ...RABBIT_FRAMES.flatMap(frame => frame.map(line => stringWidth(line))),
)

function wrapBubbleText(text: string, width: number): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (!normalized) {
    return []
  }

  const words = normalized.split(' ')
  const lines: string[] = []
  let current = ''

  for (const word of words) {
    const next = current ? `${current} ${word}` : word
    if (stringWidth(next) <= width) {
      current = next
      continue
    }

    if (current) {
      lines.push(current)
    }
    current = word
  }

  if (current) {
    lines.push(current)
  }

  return lines
}

function readBuddyConfig(path: string): BuddyConfig {
  try {
    const content = readFileSync(path, 'utf8')
    const parsed = JSON.parse(content) as unknown
    if (!parsed || typeof parsed !== 'object') {
      return {}
    }
    return parsed as BuddyConfig
  } catch {
    return {}
  }
}

function loadBuddyDisplayState(): BuddyDisplayState | null {
  const merged = {
    ...readBuddyConfig(join(homedir(), '.mini_agent', 'config.json')),
    ...readBuddyConfig(join(homedir(), '.ccmini', 'config.json')),
  }

  if (merged.buddy_enabled === false || merged.companionMuted === true) {
    return null
  }

  const rawName = merged.companion?.name
  const name =
    typeof rawName === 'string' && rawName.trim().length > 0
      ? rawName.trim()
      : 'Buddy'

  return { name }
}

function getBuddyContentWidth(name: string, speaking: boolean): number {
  const labelWidth = stringWidth(name) + 2
  const spriteWidth = FULL_SPRITE_WIDTH + SPRITE_PADDING_X
  const bubbleWidth = speaking ? BUBBLE_WRAP_WIDTH + BUBBLE_BORDER_WIDTH : 0
  return Math.max(spriteWidth, labelWidth, bubbleWidth) + 1
}

export function getBuddyReservedColumns(
  _columns: number,
  speaking = false,
): number {
  const buddy = loadBuddyDisplayState()
  if (!buddy) {
    return 0
  }

  return getBuddyContentWidth(buddy.name, speaking)
}

export function BuddyCompanion({
  themeSetting,
  columns,
  reaction = null,
  maxWidth,
}: {
  themeSetting: ThemeSetting
  columns: number
  reaction?: string | null
  maxWidth?: number
}): React.ReactNode {
  const buddy = useMemo(() => loadBuddyDisplayState(), [])
  const theme = getThemeTokens(themeSetting)
  const [animRef, time] = useAnimationFrame(BUDDY_TICK_MS)
  const bubbleWrapWidth = Math.max(
    12,
    Math.min(
      BUBBLE_WRAP_WIDTH,
      Math.max(
        12,
        (maxWidth ?? BUBBLE_WRAP_WIDTH + BUBBLE_BORDER_WIDTH) -
          BUBBLE_BORDER_WIDTH,
      ),
    ),
  )
  const speechLines = useMemo(
    () => wrapBubbleText(reaction ?? '', bubbleWrapWidth),
    [bubbleWrapWidth, reaction],
  )

  if (!buddy) {
    return null
  }

  const tick = Math.floor(time / BUDDY_TICK_MS)
  const idleStep = IDLE_SEQUENCE[tick % IDLE_SEQUENCE.length] ?? 0
  const blink = idleStep === -1
  const frameIndex =
    idleStep === -1 ? 0 : idleStep % RABBIT_FRAMES.length
  const frame = RABBIT_FRAMES[frameIndex]!.map(line =>
    blink ? line.replaceAll('◉', '-') : line,
  )
  const speaking = speechLines.length > 0
  const fullSpriteMinWidth = FULL_SPRITE_WIDTH
  const reservedColumns = Math.max(
    0,
    maxWidth ?? getBuddyContentWidth(buddy.name, speaking),
  )

  if (reservedColumns < fullSpriteMinWidth) {
    const face =
      NARROW_RABBIT_FRAMES[
        blink ? NARROW_RABBIT_FRAMES.length - 1 : frameIndex
      ] ?? NARROW_RABBIT_FRAMES[0]

    return (
      <Box ref={animRef} paddingX={1} alignSelf="flex-end">
        <Text>
          <Text color={theme.claude}>{face}</Text>
          {' '}
          <Text dimColor>{buddy.name}</Text>
        </Text>
      </Box>
    )
  }

  return (
    <Box
      ref={animRef}
      flexDirection="column"
      alignItems="flex-end"
      alignSelf="flex-end"
      width={reservedColumns}
    >
      {speaking ? (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={theme.claude}
          paddingX={1}
          marginBottom={1}
          alignSelf="flex-end"
        >
          {speechLines.map((line, index) => (
            <Text key={`speech-${index}`} color={theme.claude}>
              {line}
            </Text>
          ))}
        </Box>
      ) : null}
      <Box
        flexDirection="column"
        paddingX={1}
        alignItems="center"
        width={Math.max(FULL_SPRITE_WIDTH + SPRITE_PADDING_X, stringWidth(buddy.name) + 2)}
      >
        {frame.map((line, index) => (
          <Text key={index} color={theme.claude}>
            {line}
          </Text>
        ))}
        <Text dimColor>{buddy.name}</Text>
      </Box>
    </Box>
  )
}

export function BuddyReactionBubble({
  themeSetting,
  reaction,
  maxWidth,
}: {
  themeSetting: ThemeSetting
  reaction: string | null
  maxWidth?: number
}): React.ReactNode {
  const buddy = useMemo(() => loadBuddyDisplayState(), [])
  const theme = getThemeTokens(themeSetting)
  const bubbleWrapWidth = Math.max(
    12,
    Math.min(
      BUBBLE_WRAP_WIDTH,
      Math.max(
        12,
        (maxWidth ?? BUBBLE_WRAP_WIDTH + BUBBLE_BORDER_WIDTH) -
          BUBBLE_BORDER_WIDTH,
      ),
    ),
  )
  const speechLines = useMemo(
    () => wrapBubbleText(reaction ?? '', bubbleWrapWidth),
    [bubbleWrapWidth, reaction],
  )

  if (!buddy || speechLines.length === 0) {
    return null
  }

  return (
    <Box justifyContent="flex-end" width="100%">
      <Box
        flexDirection="column"
        borderStyle="round"
        borderColor={theme.claude}
        paddingX={1}
        alignSelf="flex-end"
      >
        {speechLines.map((line, index) => (
          <Text key={`speech-${index}`} color={theme.claude}>
            {line}
          </Text>
        ))}
      </Box>
    </Box>
  )
}
