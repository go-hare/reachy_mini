import { readdirSync, readFileSync } from 'fs'
import { dirname, extname, resolve } from 'path'
import { fileURLToPath } from 'url'

export type DonorCommandCatalogEntry = {
  name: string
  description: string
  aliases: string[]
  argumentHint?: string
  sourcePath: string
}

const COMMAND_FILE_EXTENSIONS = new Set(['.ts', '.tsx', '.js'])
const CATALOG_CACHE = new Map<string, DonorCommandCatalogEntry[]>()

function getDonorCommandsRoot(): string {
  return resolve(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    'donor-ui',
    'commands',
  )
}

function walkFiles(root: string): string[] {
  const entries = readdirSync(root, { withFileTypes: true })
  const paths: string[] = []

  for (const entry of entries) {
    const fullPath = resolve(root, entry.name)
    if (entry.isDirectory()) {
      paths.push(...walkFiles(fullPath))
      continue
    }

    if (!entry.isFile()) {
      continue
    }

    if (!COMMAND_FILE_EXTENSIONS.has(extname(entry.name))) {
      continue
    }

    paths.push(fullPath)
  }

  return paths
}

function readQuotedValue(source: string, field: string): string | undefined {
  const match = source.match(
    new RegExp(`${field}\\s*:\\s*(['"\`])([\\s\\S]*?)\\1`, 'm'),
  )
  return match?.[2]
}

function readAliases(source: string): string[] {
  const lineMatch = source.match(/aliases\s*:\s*([^\n]+)/m)
  if (!lineMatch) {
    return []
  }

  const aliases: string[] = []
  const bracketMatches = [...lineMatch[1]!.matchAll(/\[([^\]]*)\]/g)]
  for (const bracketMatch of bracketMatches) {
    const inner = bracketMatch[1] ?? ''
    const literalMatches = [...inner.matchAll(/['"`]([^'"`]+)['"`]/g)]
    for (const literalMatch of literalMatches) {
      aliases.push(literalMatch[1]!)
    }
  }

  return [...new Set(aliases)]
}

function parseCommandFile(filePath: string): DonorCommandCatalogEntry | null {
  const source = readFileSync(filePath, 'utf8')
  if (!source.includes('satisfies Command') && !source.includes('type:')) {
    return null
  }

  const name = readQuotedValue(source, 'name')
  const description = readQuotedValue(source, 'description')

  if (!name || !description) {
    return null
  }

  return {
    name,
    description,
    aliases: readAliases(source),
    argumentHint: readQuotedValue(source, 'argumentHint'),
    sourcePath: filePath,
  }
}

export function getDonorCommandCatalog(): DonorCommandCatalogEntry[] {
  const root = getDonorCommandsRoot()
  const cached = CATALOG_CACHE.get(root)
  if (cached) {
    return cached
  }

  const commands = walkFiles(root)
    .map(parseCommandFile)
    .filter((entry): entry is DonorCommandCatalogEntry => entry !== null)
    .sort((left, right) => left.name.localeCompare(right.name))

  CATALOG_CACHE.set(root, commands)
  return commands
}

export function findDonorCommand(
  identifier: string,
): DonorCommandCatalogEntry | undefined {
  const normalized = identifier.trim().replace(/^\//, '')
  if (!normalized) {
    return undefined
  }

  const lower = normalized.toLowerCase()
  return getDonorCommandCatalog().find(entry =>
    entry.name.toLowerCase() === lower ||
    entry.aliases.some(alias => alias.toLowerCase() === lower),
  )
}

export function getDonorCommandSuggestions(
  query: string,
): DonorCommandCatalogEntry[] {
  const normalized = query.trim().replace(/^\//, '').toLowerCase()
  const commands = getDonorCommandCatalog()

  if (!normalized) {
    return commands
  }

  return commands.filter(entry =>
    entry.name.toLowerCase().includes(normalized) ||
    entry.aliases.some(alias => alias.toLowerCase().includes(normalized)),
  )
}
