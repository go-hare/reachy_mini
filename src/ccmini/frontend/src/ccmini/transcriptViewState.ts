export function isVisibleTranscriptMessage(
  messageType: string,
  firstLine: string,
  showFullThinking: boolean,
): boolean {
  if (messageType === 'thinking' && !showFullThinking) {
    return false
  }

  return !(
    messageType === 'system' &&
    firstLine.startsWith('ccmini transport connected:')
  )
}

export function hasConversationHistory(messageTypes: string[]): boolean {
  return messageTypes.some(
    type =>
      type === 'user' ||
      type === 'assistant' ||
      type === 'thinking',
  )
}

export function shouldShowBuddyCompanion({
  showVisibleThemePicker,
  showVisibleCommandCatalog,
  pendingToolRequestActive,
}: {
  showVisibleThemePicker: boolean
  showVisibleCommandCatalog: boolean
  pendingToolRequestActive: boolean
}): boolean {
  return (
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    !pendingToolRequestActive
  )
}

export function getComposerLayoutState({
  columns,
  inlineBuddyReservedColumns,
}: {
  columns: number
  inlineBuddyReservedColumns: number
}): {
  showInlineBuddyCompanion: boolean
  composerPanelColumns: number
} {
  const showInlineBuddyCompanion = inlineBuddyReservedColumns > 0

  return {
    showInlineBuddyCompanion,
    composerPanelColumns: showInlineBuddyCompanion
      ? Math.max(36, columns - inlineBuddyReservedColumns)
      : columns,
  }
}

export function shouldShowAskUserQuestionEditor({
  pendingToolRequestActive,
  pendingCallsLength,
  isAskUserQuestionPending,
  questionCount,
}: {
  pendingToolRequestActive: boolean
  pendingCallsLength: number
  isAskUserQuestionPending: boolean
  questionCount: number
}): boolean {
  return Boolean(
    pendingToolRequestActive &&
      pendingCallsLength === 1 &&
      isAskUserQuestionPending &&
      questionCount > 0,
  )
}
