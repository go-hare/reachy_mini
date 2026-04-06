function normalize(version: string): [number, number, number] {
  const match = version.match(/\d+(?:\.\d+){0,2}/)?.[0] ?? '0'
  const parts = match.split('.').map(part => Number.parseInt(part, 10) || 0)
  while (parts.length < 3) {
    parts.push(0)
  }
  return [parts[0]!, parts[1]!, parts[2]!]
}

export function order(a: string, b: string): -1 | 0 | 1 {
  const left = normalize(a)
  const right = normalize(b)
  for (let index = 0; index < 3; index += 1) {
    if (left[index]! > right[index]!) {
      return 1
    }
    if (left[index]! < right[index]!) {
      return -1
    }
  }
  return 0
}

export function gt(a: string, b: string): boolean {
  return order(a, b) === 1
}

export function gte(a: string, b: string): boolean {
  return order(a, b) >= 0
}

export function lt(a: string, b: string): boolean {
  return order(a, b) === -1
}

export function lte(a: string, b: string): boolean {
  return order(a, b) <= 0
}

export function satisfies(version: string, range: string): boolean {
  const normalized = range.trim()
  if (normalized.startsWith('>=')) {
    return gte(version, normalized.slice(2))
  }
  if (normalized.startsWith('<=')) {
    return lte(version, normalized.slice(2))
  }
  if (normalized.startsWith('>')) {
    return gt(version, normalized.slice(1))
  }
  if (normalized.startsWith('<')) {
    return lt(version, normalized.slice(1))
  }
  return order(version, normalized) === 0
}
