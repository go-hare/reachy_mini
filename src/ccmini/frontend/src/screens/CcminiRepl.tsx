import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import chalk from 'chalk'
import { Box, useStdout, useTerminalFocus } from '../ink.js'
import { useTextInput } from '../hooks/useTextInput.js'
import { useCcminiCommandCatalogController } from '../hooks/useCcminiCommandCatalogController.js'
import { useCcminiKeyboardController } from '../hooks/useCcminiKeyboardController.js'
import { useCcminiSessionBridge } from '../hooks/useCcminiSessionBridge.js'
import { useCcminiSubmitHandlers } from '../hooks/useCcminiSubmitHandlers.js'
import { useCcminiThemeController } from '../hooks/useCcminiThemeController.js'
import { useCcminiTranscriptViewModel } from '../hooks/useCcminiTranscriptViewModel.js'
import {
  ComposerPanel,
} from '../components/CcminiComposerPanel.js'
import { CcminiDonorWelcome } from '../components/CcminiDonorWelcome.js'
import { CcminiTranscriptContent } from '../components/CcminiTranscriptContent.js'
import {
  CommandCatalogPanel,
  PromptHelpMenu,
  ThemePickerPanel,
} from '../components/CcminiOverlayPanels.js'
import {
  CcminiAskUserQuestionEditor,
  CcminiToolResultEditor,
  isAskUserQuestionPendingTool,
  parseAskUserQuestions,
} from '../components/CcminiPendingEditors.js'
import ScrollBox from '../ink/components/ScrollBox.js'
import type { ScrollBoxHandle } from '../ink/components/ScrollBox.js'
import { isFullscreenEnvEnabled } from '../utils/fullscreen.js'
import { isEnvTruthy } from '../utils/envUtils.js'
import {
  type CcminiConnectConfig,
  type CcminiPendingToolRequest,
  type CcminiPromptSuggestionState,
  type CcminiSpeculationState,
} from '../ccmini/bridgeTypes.js'
import {
  BuddyCompanion,
  getBuddyReservedColumns,
} from '../ccmini/BuddyCompanion.js'
import { deriveBuddyReaction } from '../ccmini/buddyReaction.js'
import { describeDonorCommand } from '../ccmini/donorCommandPresentation.js'
import {
  truncateInlineText,
} from '../ccmini/toolRenderUtils.js'
import {
  useCcminiInboxSummary,
} from '../ccmini/replMeta.js'
import {
  getMacroVersion,
  padLineToWidth,
  pickSpinnerVerb,
  summarizeToolCall,
} from '../ccmini/replHelpers.js'
import {
  getOverlayVisibility,
  type RecentImeCandidate,
} from '../ccmini/replInputState.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'
import type { Message as MessageType } from '../types/message.js'

type Props = {
  ccminiConnectConfig: CcminiConnectConfig
  initialMessages?: MessageType[]
  initialThemeSetting?: ThemeSetting
  onExit: () => void | Promise<void>
}

const DEFAULT_INPUT_PLACEHOLDER = 'Describe a task or type / for commands'
const DONOR_POINTER = '❯'
const EMPTY_PROMPT_SUGGESTION_STATE: CcminiPromptSuggestionState = {
  text: '',
  shownAt: 0,
  acceptedAt: 0,
}
const IDLE_SPECULATION_STATE: CcminiSpeculationState = {
  status: 'idle',
  suggestion: '',
  reply: '',
  startedAt: 0,
  completedAt: 0,
  error: '',
  boundary: {
    type: '',
    toolName: '',
    detail: '',
    filePath: '',
    completedAt: 0,
  },
}

export function CcminiRepl({
  ccminiConnectConfig,
  initialMessages = [],
  initialThemeSetting = 'light',
  onExit,
}: Props): React.ReactNode {
  const [messages, setMessages] = useState<MessageType[]>(initialMessages)
  const [inputValue, setInputValue] = useState('')
  const [cursorOffset, setCursorOffset] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const [showPromptHelp, setShowPromptHelp] = useState(false)
  const [showFullThinking, setShowFullThinking] = useState(false)
  const [spinnerVerb, setSpinnerVerb] = useState<string>(() => pickSpinnerVerb())
  const [buddyReaction, setBuddyReaction] = useState<string | null>(null)
  const [pendingCcminiToolRequest, setPendingCcminiToolRequest] =
    useState<CcminiPendingToolRequest | null>(null)
  const [promptSuggestion, setPromptSuggestion] =
    useState<CcminiPromptSuggestionState>(EMPTY_PROMPT_SUGGESTION_STATE)
  const [speculation, setSpeculation] =
    useState<CcminiSpeculationState>(IDLE_SPECULATION_STATE)
  const wasLoadingRef = useRef(false)
  const lastBuddyReactionFingerprintRef = useRef('')
  const recentImeCandidateRef = useRef<RecentImeCandidate>({
    text: '',
    at: 0,
  })
  const inputValueRef = useRef(inputValue)
  const cursorOffsetRef = useRef(cursorOffset)
  const scrollRef = useRef<ScrollBoxHandle | null>(null)
  const lastAutoScrollFingerprintRef = useRef('')
  const { stdout } = useStdout()
  const isTerminalFocused = useTerminalFocus()
  const accessibilityEnabled = useMemo(
    () => isEnvTruthy(process.env.CLAUDE_CODE_ACCESSIBILITY),
    [],
  )
  const showVisualCursor = isTerminalFocused && !accessibilityEnabled

  const pendingCcminiCalls = pendingCcminiToolRequest?.calls ?? []
  const firstPendingCcminiToolCall = pendingCcminiCalls[0]
  const inboxSummary = useCcminiInboxSummary(
    ccminiConnectConfig.baseUrl,
    ccminiConnectConfig.authToken,
  )
  const trimmedInputValue = inputValue.trim()

  const applyMainInputState = useCallback(
    (nextValue: string, nextOffset: number): void => {
      inputValueRef.current = nextValue
      cursorOffsetRef.current = nextOffset
      setInputValue(nextValue)
      setCursorOffset(nextOffset)
    },
    [],
  )

  const setMainInputValue = useCallback((nextValue: string): void => {
    inputValueRef.current = nextValue
    setInputValue(nextValue)
  }, [])

  const setMainCursorOffset = useCallback((nextOffset: number): void => {
    cursorOffsetRef.current = nextOffset
    setCursorOffset(nextOffset)
  }, [])

  useEffect(() => {
    inputValueRef.current = inputValue
    cursorOffsetRef.current = cursorOffset
  }, [cursorOffset, inputValue])

  useEffect(() => {
    if (isLoading && !wasLoadingRef.current) {
      setSpinnerVerb(pickSpinnerVerb())
      setBuddyReaction(null)
    }

    if (!isLoading && wasLoadingRef.current) {
      const nextBuddyReaction = deriveBuddyReaction(messages)
      if (
        nextBuddyReaction.reaction &&
        nextBuddyReaction.fingerprint &&
        nextBuddyReaction.fingerprint !== lastBuddyReactionFingerprintRef.current
      ) {
        lastBuddyReactionFingerprintRef.current = nextBuddyReaction.fingerprint
        setBuddyReaction(nextBuddyReaction.reaction)
      }
    }

    wasLoadingRef.current = isLoading
  }, [isLoading, messages])

  useEffect(() => {
    if (!buddyReaction) {
      return
    }

    const timer = setTimeout(() => {
      setBuddyReaction(prev => (prev === buddyReaction ? null : prev))
    }, 8000)

    return () => clearTimeout(timer)
  }, [buddyReaction])

  const {
    activeThemeSetting,
    themePickerIndex,
    syntaxHighlightingDisabled,
    showThemePicker,
    setPreviewThemeSetting,
    setThemePickerIndex,
    setSyntaxHighlightingDisabled,
    openThemePicker,
    closeThemePicker,
    commitThemeSetting,
  } = useCcminiThemeController({
    initialThemeSetting,
    setMessages,
  })

  const {
    showCommandCatalog,
    commandCatalogIndex,
    donorCommandQuery,
    donorCommandSuggestions,
    selectedDonorCommand,
    setShowCommandCatalog,
    setCommandCatalogIndex,
    closeCommandCatalog,
    autocompleteSelectedCommand,
  } = useCcminiCommandCatalogController({
    trimmedInputValue,
    applyMainInputState,
    setShowPromptHelp,
  })
  const {
    showVisibleCommandCatalog,
    showVisiblePromptHelp,
    showVisibleThemePicker,
  } = getOverlayVisibility({
    trimmedInputValue,
    showPromptHelp,
    showThemePicker,
    showCommandCatalog,
  })

  const { sendMessage, submitToolResults } = useCcminiSessionBridge({
    ccminiConnectConfig,
    emptyPromptSuggestionState: EMPTY_PROMPT_SUGGESTION_STATE,
    idleSpeculationState: IDLE_SPECULATION_STATE,
    wasLoadingRef,
    setIsLoading,
    setPendingCcminiToolRequest,
    setPromptSuggestion,
    setSpeculation,
    setMessages,
  })

  const { submitInputValue } = useCcminiSubmitHandlers({
    applyMainInputState,
    openThemePicker,
    onExit,
    sendMessage,
    recentImeCandidateRef,
    setShowPromptHelp,
    setShowCommandCatalog,
    setMessages,
    setPromptSuggestion,
    emptyPromptSuggestionState: EMPTY_PROMPT_SUGGESTION_STATE,
    setSpeculation,
    idleSpeculationState: IDLE_SPECULATION_STATE,
    setIsLoading,
  })

  const columns = stdout.columns ?? 100
  const terminalRows = stdout.rows ?? 24
  const footerBuddyReservedColumns =
    !showVisibleThemePicker &&
    !showVisibleCommandCatalog &&
    !pendingCcminiToolRequest
      ? getBuddyReservedColumns(columns)
      : 0

  const textInputState = useTextInput({
    value: inputValue,
    onChange: setMainInputValue,
    onSubmit: value => {
      void submitInputValue(value)
    },
    onExit: () => {
      void onExit()
    },
    onHistoryUp: () => {},
    onHistoryDown: () => {},
    onHistoryReset: () => {},
    onClearInput: () => applyMainInputState('', 0),
    focus: true,
    multiline: false,
    cursorChar: ' ',
    invert: value => (showVisualCursor ? chalk.inverse(value) : value),
    themeText: value => value,
    // Account for panel borders, inner padding, and the leading prompt marker.
    columns: Math.max(8, columns - 10 - footerBuddyReservedColumns),
    disableEscapeDoublePress:
      showCommandCatalog || showThemePicker || showPromptHelp,
    externalOffset: cursorOffset,
    onOffsetChange: setMainCursorOffset,
  })

  useCcminiKeyboardController({
    inputValueRef,
    recentImeCandidateRef,
    pendingToolRequestActive: Boolean(pendingCcminiToolRequest),
    showThemePicker,
    showCommandCatalog,
    showPromptHelp,
    donorCommandSuggestionsLength: donorCommandSuggestions.length,
    selectedDonorCommand,
    promptSuggestionText: promptSuggestion.text,
    themePickerIndex,
    setThemePickerIndex,
    setPreviewThemeSetting,
    setSyntaxHighlightingDisabled,
    commitThemeSetting,
    closeThemePicker,
    setCommandCatalogIndex,
    autocompleteSelectedCommand,
    applyMainInputState,
    onExit,
    setShowFullThinking,
    setShowPromptHelp,
    closeCommandCatalog,
    submitInputValue,
    onTextInputInput: textInputState.onInput,
  })

  const askUserQuestionCount = parseAskUserQuestions(
    firstPendingCcminiToolCall?.toolInput,
  ).length
  const {
    visibleMessages,
    toolUseLookup,
    showWelcome,
    conversationWidth,
    recentActivityLines,
    showBuddyCompanion,
    showInlineBuddyCompanion,
    composerPanelColumns,
    showAskUserQuestionEditor,
  } = useCcminiTranscriptViewModel({
    messages,
    showFullThinking,
    inboxLines: inboxSummary.lines,
    showVisibleThemePicker,
    showVisibleCommandCatalog,
    pendingToolRequestActive: Boolean(pendingCcminiToolRequest),
    columns,
    pendingCallsLength: pendingCcminiCalls.length,
    isAskUserQuestionPending: isAskUserQuestionPendingTool(
      firstPendingCcminiToolCall,
    ),
    askUserQuestionCount,
  })
  const fullscreenMode = isFullscreenEnvEnabled()

  useEffect(() => {
    if (!fullscreenMode) {
      lastAutoScrollFingerprintRef.current = ''
      return
    }

    const fingerprint = [
      visibleMessages.length,
      isLoading ? 'loading' : 'idle',
      pendingCcminiToolRequest?.runId ?? '',
    ].join(':')

    if (lastAutoScrollFingerprintRef.current === fingerprint) {
      return
    }
    lastAutoScrollFingerprintRef.current = fingerprint

    const scrollHandle = scrollRef.current
    if (!scrollHandle || !scrollHandle.isSticky()) {
      return
    }

    scrollHandle.scrollToBottom()
  }, [
    fullscreenMode,
    isLoading,
    pendingCcminiToolRequest?.runId,
    visibleMessages.length,
  ])

  return (
    <Box
      flexDirection="column"
      width="100%"
      height={fullscreenMode ? terminalRows : undefined}
    >
      {showWelcome ? (
        <CcminiDonorWelcome
          themeSetting={activeThemeSetting}
          columns={columns}
          version={getMacroVersion()}
          recentActivityLines={recentActivityLines}
        />
      ) : null}

      {showWelcome ? (
        fullscreenMode ? <Box flexGrow={1} /> : null
      ) : (
        fullscreenMode ? (
          <ScrollBox
            ref={scrollRef}
            flexDirection="column"
            flexGrow={1}
            flexShrink={1}
            width="100%"
            marginTop={1}
            stickyScroll
          >
            <CcminiTranscriptContent
              visibleMessages={visibleMessages}
              toolUseLookup={toolUseLookup}
              conversationWidth={conversationWidth}
              activeThemeSetting={activeThemeSetting}
              showFullThinking={showFullThinking}
              pendingToolRequest={pendingCcminiToolRequest}
              firstPendingToolCall={firstPendingCcminiToolCall}
              pendingCallCount={pendingCcminiCalls.length}
              showAskUserQuestionEditor={showAskUserQuestionEditor}
              isLoading={isLoading}
              spinnerVerb={spinnerVerb}
            />
          </ScrollBox>
        ) : (
          <Box flexDirection="column" width="100%" marginTop={1}>
            <CcminiTranscriptContent
              visibleMessages={visibleMessages}
              toolUseLookup={toolUseLookup}
              conversationWidth={conversationWidth}
              activeThemeSetting={activeThemeSetting}
              showFullThinking={showFullThinking}
              pendingToolRequest={pendingCcminiToolRequest}
              firstPendingToolCall={firstPendingCcminiToolCall}
              pendingCallCount={pendingCcminiCalls.length}
              showAskUserQuestionEditor={showAskUserQuestionEditor}
              isLoading={isLoading}
              spinnerVerb={spinnerVerb}
            />
          </Box>
        )
      )}

      {showVisibleThemePicker ? (
        <ThemePickerPanel
          selectedIndex={themePickerIndex}
          previewThemeSetting={activeThemeSetting}
          syntaxHighlightingDisabled={syntaxHighlightingDisabled}
          columns={columns}
          donorPointer={DONOR_POINTER}
        />
      ) : null}

      {showVisibleCommandCatalog ? (
        <CommandCatalogPanel
          entries={donorCommandSuggestions}
          selectedIndex={commandCatalogIndex}
          query={donorCommandQuery ?? ''}
          themeSetting={activeThemeSetting}
          columns={columns}
          donorPointer={DONOR_POINTER}
          describeCommand={describeDonorCommand}
        />
      ) : null}

      {!pendingCcminiToolRequest ? (
        <React.Fragment>
          <Box
            flexDirection={showInlineBuddyCompanion ? 'row' : 'column'}
            width="100%"
            alignItems={showInlineBuddyCompanion ? 'flex-end' : undefined}
          >
            <Box
              width={showInlineBuddyCompanion ? composerPanelColumns : '100%'}
              flexGrow={1}
              flexShrink={1}
            >
              <ComposerPanel
                themeSetting={activeThemeSetting}
                columns={composerPanelColumns}
                inputValue={inputValue}
                renderedValue={textInputState.renderedValue}
                cursorLine={textInputState.cursorLine}
                cursorColumn={textInputState.cursorColumn}
                donorPointer={DONOR_POINTER}
                padLineToWidth={padLineToWidth}
                terminalFocused={isTerminalFocused}
                showVisualCursor={showVisualCursor}
              />
            </Box>
            {showInlineBuddyCompanion ? (
              <BuddyCompanion
                themeSetting={activeThemeSetting}
                columns={columns}
                reaction={buddyReaction}
              />
            ) : null}
          </Box>
          {showVisibleThemePicker
            ? null
            : showVisibleCommandCatalog
              ? null
              : showVisiblePromptHelp
                ? (
                    <PromptHelpMenu
                      themeSetting={activeThemeSetting}
                      columns={columns}
                    />
                  )
                : null}
        </React.Fragment>
      ) : null}

      {showBuddyCompanion && !showInlineBuddyCompanion ? (
        <Box width="100%" justifyContent="flex-end">
          <BuddyCompanion
            themeSetting={activeThemeSetting}
            columns={columns}
            reaction={buddyReaction}
          />
        </Box>
      ) : null}

      {pendingCcminiToolRequest && showAskUserQuestionEditor && firstPendingCcminiToolCall ? (
        <CcminiAskUserQuestionEditor
          key={pendingCcminiToolRequest.runId}
          call={firstPendingCcminiToolCall}
          columns={columns}
          onSubmit={async results => {
            const ok = await submitToolResults(
              pendingCcminiToolRequest.runId,
              results,
            )
            if (ok) {
              setPendingCcminiToolRequest(null)
            }
          }}
          onAbort={() => {
            setPendingCcminiToolRequest(null)
            setIsLoading(false)
          }}
          themeSetting={activeThemeSetting}
          truncateInlineText={truncateInlineText}
        />
      ) : null}

      {pendingCcminiToolRequest && !showAskUserQuestionEditor ? (
        <CcminiToolResultEditor
          key={pendingCcminiToolRequest.runId}
          runId={pendingCcminiToolRequest.runId}
          calls={pendingCcminiCalls}
          onSubmit={async results => {
            const ok = await submitToolResults(
              pendingCcminiToolRequest.runId,
              results,
            )
            if (ok) {
              setPendingCcminiToolRequest(null)
            }
          }}
          onAbort={() => {
            setPendingCcminiToolRequest(null)
            setIsLoading(false)
          }}
          themeSetting={activeThemeSetting}
          summarizeToolCall={summarizeToolCall}
          defaultInputPlaceholder={DEFAULT_INPUT_PLACEHOLDER}
        />
      ) : null}
    </Box>
  )
}
