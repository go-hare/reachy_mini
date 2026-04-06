export function logError(error: unknown): void {
  const line =
    error instanceof Error
      ? `${error.stack ?? error.message}\n`
      : `${String(error)}\n`
  process.stderr.write(line)
}
