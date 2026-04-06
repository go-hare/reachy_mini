export function logForDebugging(
  message: string,
  options?: {
    level?: 'info' | 'warn' | 'error'
  },
): void {
  if (!process.env.CLAUDE_CODE_DEBUG_REPAINTS && !process.env.CLAUDE_CODE_DEBUG) {
    return
  }

  const level = options?.level ?? 'info'
  const line = `[ccmini-ink:${level}] ${message}\n`
  process.stderr.write(line)
}
