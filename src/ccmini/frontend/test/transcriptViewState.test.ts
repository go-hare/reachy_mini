import { describe, expect, test } from 'bun:test'
import {
  getComposerLayoutState,
  hasConversationHistory,
  isVisibleTranscriptMessage,
  shouldShowAskUserQuestionEditor,
  shouldShowBuddyCompanion,
} from '../src/ccmini/transcriptViewState.js'

describe('isVisibleTranscriptMessage', () => {
  test('hides collapsed thinking and bridge connected status', () => {
    expect(isVisibleTranscriptMessage('thinking', 'Thinking…', false)).toBe(false)
    expect(
      isVisibleTranscriptMessage(
        'system',
        'ccmini transport connected: http://localhost:8000',
        true,
      ),
    ).toBe(false)
    expect(isVisibleTranscriptMessage('assistant', 'hello', false)).toBe(true)
  })
})

describe('hasConversationHistory', () => {
  test('detects user-facing conversation types only', () => {
    expect(hasConversationHistory(['system', 'progress'])).toBe(false)
    expect(hasConversationHistory(['system', 'assistant'])).toBe(true)
  })
})

describe('layout helpers', () => {
  test('computes buddy and composer layout state', () => {
    expect(
      shouldShowBuddyCompanion({
        showVisibleThemePicker: false,
        showVisibleCommandCatalog: false,
        pendingToolRequestActive: false,
      }),
    ).toBe(true)
    expect(
      getComposerLayoutState({
        columns: 120,
        inlineBuddyReservedColumns: 24,
        showWelcome: true,
      }),
    ).toEqual({
      showInlineBuddyCompanion: true,
      composerPanelColumns: 96,
    })
  })
})

describe('shouldShowAskUserQuestionEditor', () => {
  test('requires single ask-user-question call with parsed prompts', () => {
    expect(
      shouldShowAskUserQuestionEditor({
        pendingToolRequestActive: true,
        pendingCallsLength: 1,
        isAskUserQuestionPending: true,
        questionCount: 2,
      }),
    ).toBe(true)
    expect(
      shouldShowAskUserQuestionEditor({
        pendingToolRequestActive: true,
        pendingCallsLength: 2,
        isAskUserQuestionPending: true,
        questionCount: 2,
      }),
    ).toBe(false)
  })
})
