import { describe, expect, test } from 'bun:test'
import {
  getOverlayVisibility,
  isCommandCatalogActive,
  resolveLocalCommandIntent,
  shouldRestoreImeQuestionInput,
  shouldSubmitRecentImeCandidate,
} from '../src/ccmini/replInputState.js'

describe('resolveLocalCommandIntent', () => {
  test('handles built-in overlay commands', () => {
    expect(resolveLocalCommandIntent('/')).toEqual({
      type: 'open-command-catalog',
    })
    expect(resolveLocalCommandIntent('/add-dir')).toEqual({
      type: 'open-add-directory',
      rawArgs: '',
    })
    expect(resolveLocalCommandIntent('/add-dir C:\\refs\\doge')).toEqual({
      type: 'open-add-directory',
      rawArgs: 'C:\\refs\\doge',
    })
    expect(resolveLocalCommandIntent('/help')).toEqual({ type: 'open-help' })
    expect(resolveLocalCommandIntent('/theme')).toEqual({
      type: 'open-theme-picker',
    })
    expect(resolveLocalCommandIntent('/quit')).toEqual({ type: 'exit' })
  })

  test('keeps donor help lookups and backend passthrough separate', () => {
    expect(resolveLocalCommandIntent('/help /buddy')).toEqual({
      type: 'show-command-help',
      lookup: '/buddy',
    })
    expect(resolveLocalCommandIntent('/agents')).toEqual({
      type: 'backend-passthrough',
    })
    expect(resolveLocalCommandIntent('/bridge-kick')).toEqual({
      type: 'show-command-help',
      lookup: '/bridge-kick',
    })
  })
})

describe('getOverlayVisibility', () => {
  test('opens command catalog for slash-only queries', () => {
    expect(
      getOverlayVisibility({
        trimmedInputValue: '/br',
        showPromptHelp: false,
        showThemePicker: false,
        showCommandCatalog: false,
      }),
    ).toEqual({
      donorCommandQuery: 'br',
      showVisiblePromptHelp: false,
      showVisibleThemePicker: false,
      showVisibleCommandCatalog: true,
    })
  })

  test('suppresses catalog when help overlay is active', () => {
    expect(
      getOverlayVisibility({
        trimmedInputValue: '/help',
        showPromptHelp: false,
        showThemePicker: false,
        showCommandCatalog: true,
      }),
    ).toEqual({
      donorCommandQuery: 'help',
      showVisiblePromptHelp: true,
      showVisibleThemePicker: false,
      showVisibleCommandCatalog: false,
    })
  })
})

describe('isCommandCatalogActive', () => {
  test('does not activate while theme picker or help is active', () => {
    expect(
      isCommandCatalogActive({
        inputValue: '/theme',
        showThemePicker: false,
        showCommandCatalog: true,
      }),
    ).toBe(false)
    expect(
      isCommandCatalogActive({
        inputValue: '/help',
        showThemePicker: false,
        showCommandCatalog: true,
      }),
    ).toBe(false)
  })
})

describe('IME helpers', () => {
  test('restores question-mark submit from recent IME candidate', () => {
    expect(
      shouldRestoreImeQuestionInput({
        normalizedValue: '?',
        recentImeCandidate: { text: '你好', at: 950 },
        now: 1000,
        maxAgeMs: 100,
        isAppleTerminal: true,
      }),
    ).toBe(true)
  })

  test('only submits recent IME candidate on empty apple-terminal input', () => {
    expect(
      shouldSubmitRecentImeCandidate({
        inputValue: '',
        recentImeCandidate: { text: '布局', at: 900 },
        now: 1000,
        maxAgeMs: 200,
        isAppleTerminal: true,
      }),
    ).toBe(true)
    expect(
      shouldSubmitRecentImeCandidate({
        inputValue: '/buddy',
        recentImeCandidate: { text: '布局', at: 900 },
        now: 1000,
        maxAgeMs: 200,
        isAppleTerminal: true,
      }),
    ).toBe(false)
  })
})
