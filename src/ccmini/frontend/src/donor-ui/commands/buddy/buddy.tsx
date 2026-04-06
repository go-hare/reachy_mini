import * as React from 'react'
import { Box, Text } from '../../ink.js'
import type { LocalJSXCommandCall } from '../../types/command.js'
import { renderToString } from '../../utils/staticRender.js'
import { getGlobalConfig, saveGlobalConfig } from '../../utils/config.js'
import { companionUserId, getCompanion, roll } from '../../buddy/companion.js'
import {
  RARITY_STARS,
  STAT_NAMES,
  type StoredCompanion,
} from '../../buddy/types.js'

type BuddyAction = 'show' | 'pet' | 'mute' | 'unmute' | 'help'

type BuddySummaryProps = {
  lines: string[]
}

function BuddySummary({ lines }: BuddySummaryProps): React.ReactNode {
  return (
    <Box flexDirection="column">
      {lines.map((line, i) => (
        <Text key={i}>{line}</Text>
      ))}
    </Box>
  )
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function buildBuddyName(userId: string): string {
  const prefixes = [
    'Byte',
    'Nibble',
    'Patch',
    'Pixel',
    'Mochi',
    'Biscuit',
    'Comet',
    'Pebble',
  ]
  const suffixes = [
    'Buddy',
    'Bean',
    'Puff',
    'Loop',
    'Sprite',
    'Scout',
    'Spark',
    'Dot',
  ]
  const seed = roll(userId).inspirationSeed
  return `${prefixes[seed % prefixes.length]} ${suffixes[Math.floor(seed / prefixes.length) % suffixes.length]}`
}

function buildPersonality(userId: string): string {
  const { bones, inspirationSeed } = roll(userId)
  const moods = [
    'cheers on clean fixes',
    'collects tiny debugging victories',
    'loves watching clever terminal work',
    'gets especially excited about elegant patches',
    'keeps an eye out for suspicious edge cases',
  ]
  const verbs = [
    'chirps',
    'bounces',
    'glows',
    'wiggles',
    'hums',
  ]
  const mood = moods[inspirationSeed % moods.length]
  const verb = verbs[Math.floor(inspirationSeed / moods.length) % verbs.length]
  return `A ${bones.rarity} ${bones.species} who ${verb}s softly and ${mood}.`
}

function createStoredCompanion(): StoredCompanion {
  const userId = companionUserId()
  return {
    name: buildBuddyName(userId),
    personality: buildPersonality(userId),
    hatchedAt: Date.now(),
  }
}

function ensureCompanion(): { companion: NonNullable<ReturnType<typeof getCompanion>>; hatched: boolean } {
  const existing = getCompanion()
  if (existing) {
    return { companion: existing, hatched: false }
  }

  const stored = createStoredCompanion()
  saveGlobalConfig(current => ({
    ...current,
    companion: stored,
    companionMuted: false,
  }))

  return {
    companion: getCompanion()!,
    hatched: true,
  }
}

function parseAction(args: string): BuddyAction {
  const action = args.trim().toLowerCase()
  switch (action) {
    case '':
      return 'show'
    case 'pet':
    case 'mute':
    case 'unmute':
    case 'help':
      return action
    default:
      return 'help'
  }
}

function buildSummaryLines(companion: NonNullable<ReturnType<typeof getCompanion>>): string[] {
  const stats = STAT_NAMES.map(name => `${name} ${companion.stats[name]}`).join(' · ')
  return [
    `${companion.name} ${RARITY_STARS[companion.rarity]}`,
    `${titleCase(companion.species)}${companion.shiny ? ' · shiny' : ''}${companion.hat !== 'none' ? ` · hat: ${companion.hat}` : ''}`,
    companion.personality,
    stats,
  ]
}

async function showBuddy(onDone: Parameters<LocalJSXCommandCall>[0]): Promise<void> {
  const { companion, hatched } = ensureCompanion()
  const lines = buildSummaryLines(companion)
  const output = await renderToString(
    <BuddySummary
      lines={hatched ? ['Your buddy hatched.', ...lines] : lines}
    />,
  )
  onDone(output)
}

export const call: LocalJSXCommandCall = async (onDone, context, args) => {
  const action = parseAction(args)

  if (action === 'help') {
    onDone('Usage: /buddy [pet|mute|unmute|help]')
    return null
  }

  if (action === 'mute') {
    const { companion } = ensureCompanion()
    saveGlobalConfig(current => ({
      ...current,
      companion: current.companion ?? companion,
      companionMuted: true,
    }))
    onDone(`${companion.name} is muted. Use /buddy unmute to bring them back.`)
    return null
  }

  if (action === 'unmute') {
    const { companion } = ensureCompanion()
    saveGlobalConfig(current => ({
      ...current,
      companion: current.companion ?? companion,
      companionMuted: false,
    }))
    onDone(`${companion.name} is back.`)
    return null
  }

  if (action === 'pet') {
    const { companion } = ensureCompanion()
    context.setAppState(prev => ({
      ...prev,
      companionPetAt: Date.now(),
    }))
    onDone(`You pet ${companion.name}.`)
    return null
  }

  await showBuddy(onDone)
  return null
}
