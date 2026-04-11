import type { DonorCommandCatalogEntry } from './donorCommandCatalog.js'

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

export function isFrontendLocalCommandName(name: string): boolean {
  return FRONTEND_LOCAL_COMMAND_NAMES.has(name)
}

export function isBackendPassthroughCommandName(name: string): boolean {
  return BACKEND_PASSTHROUGH_COMMAND_NAMES.has(name)
}

export function getCommandAutocompleteValue(
  entry: DonorCommandCatalogEntry,
): string {
  return `/${entry.name}${entry.argumentHint ? ' ' : ''}`
}

export function getCommandStatusLabel(
  entry: DonorCommandCatalogEntry,
): string {
  if (isFrontendLocalCommandName(entry.name)) {
    return 'native'
  }

  if (isBackendPassthroughCommandName(entry.name)) {
    return 'backend'
  }

  return 'reference'
}

export function describeDonorCommand(
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
      ? 'Status: ready in this frontend.'
      : isBackendPassthroughCommandName(entry.name)
        ? 'Status: forwarded to the connected runtime.'
        : 'Status: available as a reference command from the donor project.',
  )

  return lines
}
