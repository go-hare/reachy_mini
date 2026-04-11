import chalk from 'chalk'

export function applyForeground(text: string, color: string): string {
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

export function applyBackground(text: string, color: string): string {
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
