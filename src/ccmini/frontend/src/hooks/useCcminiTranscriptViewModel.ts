import { useMemo } from 'react'
import { getBuddyReservedColumns } from '../ccmini/BuddyCompanion.js'
import { getRecentActivityPreview } from '../ccmini/replMeta.js'
import {
  buildToolUseLookup,
  getMessageLines,
} from '../ccmini/transcriptState.js'
import {
  getComposerLayoutState,
  hasConversationHistory,
  isVisibleTranscriptMessage,
  shouldShowAskUserQuestionEditor,
  shouldShowBuddyCompanion,
} from '../ccmini/transcriptViewState.js'
import type { Message as MessageType } from '../types/message.js'

export type RenderedTranscriptMessage = {
  key: string
  message: MessageType
  lines: string[]
}

type UseCcminiTranscriptViewModelOptions = {
  messages: MessageType[]
  showFullThinking: boolean
  inboxLines: string[]
  showVisibleThemePicker: boolean
  showVisibleCommandCatalog: boolean
  pendingToolRequestActive: boolean
  columns: number
  pendingCallsLength: number
  isAskUserQuestionPending: boolean
  askUserQuestionCount: number
}

export function useCcminiTranscriptViewModel({
  messages,
  showFullThinking,
  inboxLines,
  showVisibleThemePicker,
  showVisibleCommandCatalog,
  pendingToolRequestActive,
  columns,
  pendingCallsLength,
  isAskUserQuestionPending,
  askUserQuestionCount,
}: UseCcminiTranscriptViewModelOptions): {
  visibleMessages: RenderedTranscriptMessage[]
  toolUseLookup: ReturnType<typeof buildToolUseLookup>
  showWelcome: boolean
  conversationWidth: number
  recentActivityLines: string[]
  showBuddyCompanion: boolean
  showInlineBuddyCompanion: boolean
  composerPanelColumns: number
  showAskUserQuestionEditor: boolean
} {
  const renderedMessages = useMemo(
    () =>
      messages.map((message, index) => ({
        key: message.uuid ?? `${message.type}-${index}`,
        message,
        lines: getMessageLines(message),
      })),
    [messages],
  )

  const visibleMessages = useMemo(
    () =>
      renderedMessages.filter(message =>
        isVisibleTranscriptMessage(
          message.message.type,
          message.lines[0] ?? '',
          showFullThinking,
        ),
      ),
    [renderedMessages, showFullThinking],
  )

  const toolUseLookup = useMemo(() => buildToolUseLookup(messages), [messages])
  const showWelcome = !hasConversationHistory(
    visibleMessages.map(message => message.message.type),
  )
  const conversationWidth = Math.max(20, columns - 4)
  const recentActivityLines = useMemo(
    () => getRecentActivityPreview(messages, inboxLines),
    [inboxLines, messages],
  )
  const showBuddyCompanion = shouldShowBuddyCompanion({
    showVisibleThemePicker,
    showVisibleCommandCatalog,
    pendingToolRequestActive,
  })
  const inlineBuddyReservedColumns = showBuddyCompanion
    ? getBuddyReservedColumns(columns)
    : 0
  const {
    showInlineBuddyCompanion,
    composerPanelColumns,
  } = getComposerLayoutState({
    columns,
    inlineBuddyReservedColumns,
  })
  const showAskUserQuestionEditor = shouldShowAskUserQuestionEditor({
    pendingToolRequestActive,
    pendingCallsLength,
    isAskUserQuestionPending,
    questionCount: askUserQuestionCount,
  })

  return {
    visibleMessages,
    toolUseLookup,
    showWelcome,
    conversationWidth,
    recentActivityLines,
    showBuddyCompanion,
    showInlineBuddyCompanion,
    composerPanelColumns,
    showAskUserQuestionEditor,
  }
}
