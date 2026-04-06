// @ts-nocheck
export const ADDITIONAL_HYPERLINK_TERMINALS = [
  'ghostty',
  'Hyper',
  'kitty',
  'alacritty',
  'iTerm.app',
  'iTerm2',
]

type EnvLike = Record<string, string | undefined>

type SupportsHyperlinksOptions = {
  env?: EnvLike
  stdoutSupported?: boolean
}

export function supportsHyperlinks(
  options?: SupportsHyperlinksOptions,
): boolean {
  const env = options?.env ?? process.env

  if (options?.stdoutSupported) {
    return true
  }

  const termProgram = env['TERM_PROGRAM']
  if (termProgram && ADDITIONAL_HYPERLINK_TERMINALS.includes(termProgram)) {
    return true
  }

  const lcTerminal = env['LC_TERMINAL']
  if (lcTerminal && ADDITIONAL_HYPERLINK_TERMINALS.includes(lcTerminal)) {
    return true
  }

  const term = env['TERM']
  return Boolean(term?.includes('kitty'))
}
