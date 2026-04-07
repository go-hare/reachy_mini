import * as React from 'react'
import { useMemo } from 'react'
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { Box, Text, useAnimationFrame } from '../ink.js'
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

const MIN_COLS_FOR_FULL_SPRITE = 100
const BUDDY_TICK_MS = 500
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

export function BuddyCompanion({
  themeSetting,
  columns,
}: {
  themeSetting: ThemeSetting
  columns: number
}): React.ReactNode {
  const buddy = useMemo(() => loadBuddyDisplayState(), [])
  const theme = getThemeTokens(themeSetting)
  const [animRef, time] = useAnimationFrame(BUDDY_TICK_MS)

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

  if (columns < MIN_COLS_FOR_FULL_SPRITE) {
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
      paddingX={1}
      alignItems="center"
      alignSelf="flex-end"
    >
      {frame.map((line, index) => (
        <Text key={index} color={theme.claude}>
          {line}
        </Text>
      ))}
      <Text dimColor>{buddy.name}</Text>
    </Box>
  )
}
