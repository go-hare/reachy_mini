import { isBackendPassthroughCommandName } from './donorCommandPresentation.js'

export type RecentImeCandidate = {
  text: string
  at: number
}

export type LocalCommandIntent =
  | { type: 'open-command-catalog' }
  | { type: 'open-add-directory'; rawArgs: string }
  | { type: 'open-help' }
  | { type: 'show-command-help'; lookup: string }
  | { type: 'open-theme-picker' }
  | { type: 'exit' }
  | { type: 'backend-passthrough' }
  | { type: 'unhandled' }

export function resolveLocalCommandIntent(value: string): LocalCommandIntent {
  const normalized = value.trim()
  if (normalized === '/' || normalized === '/commands') {
    return { type: 'open-command-catalog' }
  }

  if (normalized === '/help') {
    return { type: 'open-help' }
  }

  if (normalized === '/add-dir' || normalized.startsWith('/add-dir ')) {
    return {
      type: 'open-add-directory',
      rawArgs: normalized.slice('/add-dir'.length).trim(),
    }
  }

  if (normalized.startsWith('/help ')) {
    return {
      type: 'show-command-help',
      lookup: normalized.slice('/help '.length).trim(),
    }
  }

  if (normalized === '/theme') {
    return { type: 'open-theme-picker' }
  }

  if (normalized === '/exit' || normalized === '/quit') {
    return { type: 'exit' }
  }

  if (normalized.startsWith('/')) {
    const slashCommandName = normalized
      .slice(1)
      .split(/\s+/, 1)[0]
      ?.toLowerCase() ?? ''
    if (isBackendPassthroughCommandName(slashCommandName)) {
      return { type: 'backend-passthrough' }
    }

    return {
      type: 'show-command-help',
      lookup: normalized,
    }
  }

  return { type: 'unhandled' }
}

export function getDonorCommandQuery(
  trimmedInputValue: string,
  showCommandCatalog: boolean,
): string | null {
  if (trimmedInputValue.startsWith('/')) {
    const value = trimmedInputValue.slice(1)
    return value.includes(' ') ? null : value
  }

  return showCommandCatalog ? '' : null
}

export function getOverlayVisibility({
  trimmedInputValue,
  showPromptHelp,
  showThemePicker,
  showCommandCatalog,
}: {
  trimmedInputValue: string
  showPromptHelp: boolean
  showThemePicker: boolean
  showCommandCatalog: boolean
}): {
  donorCommandQuery: string | null
  showVisiblePromptHelp: boolean
  showVisibleThemePicker: boolean
  showVisibleCommandCatalog: boolean
} {
  const donorCommandQuery = getDonorCommandQuery(
    trimmedInputValue,
    showCommandCatalog,
  )
  const showVisiblePromptHelp =
    showPromptHelp || trimmedInputValue === '/help'
  const showVisibleThemePicker =
    showThemePicker || trimmedInputValue === '/theme'
  const showVisibleCommandCatalog =
    !showVisibleThemePicker &&
    trimmedInputValue !== '/help' &&
    (
      showCommandCatalog ||
      donorCommandQuery !== null
    )

  return {
    donorCommandQuery,
    showVisiblePromptHelp,
    showVisibleThemePicker,
    showVisibleCommandCatalog,
  }
}

export function isThemePickerActive(
  showThemePicker: boolean,
  inputValue: string,
): boolean {
  return showThemePicker || inputValue.trim() === '/theme'
}

export function isCommandCatalogActive({
  inputValue,
  showThemePicker,
  showCommandCatalog,
}: {
  inputValue: string
  showThemePicker: boolean
  showCommandCatalog: boolean
}): boolean {
  if (isThemePickerActive(showThemePicker, inputValue)) {
    return false
  }

  const trimmed = inputValue.trim()
  if (trimmed === '/help') {
    return false
  }

  return (
    showCommandCatalog ||
    (
      trimmed.startsWith('/') &&
      !trimmed.slice(1).includes(' ')
    )
  )
}

export function shouldRestoreImeQuestionInput({
  normalizedValue,
  recentImeCandidate,
  now,
  maxAgeMs,
  isAppleTerminal,
}: {
  normalizedValue: string
  recentImeCandidate: RecentImeCandidate
  now: number
  maxAgeMs: number
  isAppleTerminal: boolean
}): boolean {
  return Boolean(
    isAppleTerminal &&
      normalizedValue === '?' &&
      recentImeCandidate.text &&
      now - recentImeCandidate.at < maxAgeMs,
  )
}

export function shouldSubmitRecentImeCandidate({
  inputValue,
  recentImeCandidate,
  now,
  maxAgeMs,
  isAppleTerminal,
}: {
  inputValue: string
  recentImeCandidate: RecentImeCandidate
  now: number
  maxAgeMs: number
  isAppleTerminal: boolean
}): boolean {
  return Boolean(
    isAppleTerminal &&
      !inputValue.trim() &&
      recentImeCandidate.text &&
      now - recentImeCandidate.at < maxAgeMs,
  )
}
