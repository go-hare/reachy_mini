import { readdir } from 'fs/promises'
import { basename, dirname, isAbsolute, join, resolve } from 'path'

function resolveInputDirectory(inputValue: string): {
  parentDir: string
  partialName: string
} {
  const normalized = inputValue.trim()
  if (!normalized) {
    return {
      parentDir: process.cwd(),
      partialName: '',
    }
  }

  const endsWithSeparator =
    normalized.endsWith('/') || normalized.endsWith('\\')

  if (endsWithSeparator) {
    return {
      parentDir: resolve(normalized),
      partialName: '',
    }
  }

  if (isAbsolute(normalized)) {
    return {
      parentDir: dirname(normalized),
      partialName: basename(normalized),
    }
  }

  const resolved = resolve(process.cwd(), normalized)
  return {
    parentDir: dirname(resolved),
    partialName: basename(normalized),
  }
}

export async function getDirectorySuggestions(
  inputValue: string,
): Promise<string[]> {
  const { parentDir, partialName } = resolveInputDirectory(inputValue)
  try {
    const entries = await readdir(parentDir, {
      withFileTypes: true,
    })
    const prefix = partialName.toLowerCase()
    return entries
      .filter(entry => entry.isDirectory())
      .filter(entry =>
        prefix ? entry.name.toLowerCase().startsWith(prefix) : true,
      )
      .slice(0, 8)
      .map(entry => join(parentDir, entry.name))
  } catch {
    return []
  }
}
