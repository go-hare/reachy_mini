function detectTerminal(): string | null {
  if (process.env.TERM_PROGRAM) {
    return process.env.TERM_PROGRAM
  }
  if (process.env.TERM) {
    return process.env.TERM
  }
  if (process.env.TMUX) {
    return 'tmux'
  }
  return null
}

export const env = {
  terminal: detectTerminal(),
}
