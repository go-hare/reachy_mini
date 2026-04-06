export type ThinkingTriggerPosition = {
  word: string
  start: number
  end: number
}

// Donor UI highlights the "ultrathink" keyword in rainbow colors.
export function findThinkingTriggerPositions(
  text: string,
): ThinkingTriggerPosition[] {
  const positions: ThinkingTriggerPosition[] = []
  const matches = text.matchAll(/\bultrathink\b/gi)

  for (const match of matches) {
    if (match.index === undefined) {
      continue
    }
    positions.push({
      word: match[0],
      start: match.index,
      end: match.index + match[0].length,
    })
  }

  return positions
}

const RAINBOW_COLORS = [
  'red',
  'yellow',
  'green',
  'cyan',
  'blue',
  'magenta',
] as const

export function getRainbowColor(index: number): (typeof RAINBOW_COLORS)[number] {
  return RAINBOW_COLORS[index % RAINBOW_COLORS.length]!
}
