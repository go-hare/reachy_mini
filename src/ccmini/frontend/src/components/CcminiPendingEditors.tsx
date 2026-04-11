import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import chalk from 'chalk'
import { Box, Text, useInput, useStdin, useTerminalFocus } from '../ink.js'
import { useTextInput } from '../hooks/useTextInput.js'
import {
  MainInputLine,
  renderInputLineWithPlaceholder,
} from './CcminiComposerPanel.js'
import { ConsoleSection, getFrameBorderColor } from './CcminiSectionFrame.js'
import type { CcminiPendingToolCall } from '../ccmini/bridgeTypes.js'
import { applyBackground, applyForeground } from '../ccmini/ansiText.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'
import { isEnvTruthy } from '../utils/envUtils.js'

type SavedToolResult = {
  content: string
  isError: boolean
}

type AskUserQuestionOption = {
  id: string
  label: string
  description?: string
}

type AskUserQuestion = {
  id: string
  header?: string
  prompt: string
  options: AskUserQuestionOption[]
  allowMultiple: boolean
}

type AskUserQuestionAnswer = {
  selectedOptionIds: string[]
  selectedLabels: string[]
  freeformText: string
}

const DONOR_POINTER = '❯'
const ASK_USER_QUESTION_ICONS = {
  tick: '✓',
  bullet: '•',
  arrowRight: '→',
  warning: '!',
} as const

function toPendingToolInputRecord(
  value: unknown,
): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

function createEmptyAskUserQuestionAnswer(): AskUserQuestionAnswer {
  return {
    selectedOptionIds: [],
    selectedLabels: [],
    freeformText: '',
  }
}

function getAskUserQuestionKey(question: AskUserQuestion): string {
  return question.id || question.prompt
}

function getAskUserQuestionHeader(
  question: AskUserQuestion,
  index: number,
  truncateInlineText: (value: string, maxLength?: number) => string,
): string {
  const rawHeader =
    typeof question.header === 'string' && question.header.trim()
      ? question.header.trim()
      : `Q${index + 1}`
  return truncateInlineText(rawHeader, 18)
}

export function isAskUserQuestionPendingTool(
  call: CcminiPendingToolCall | null | undefined,
): boolean {
  return String(call?.toolName ?? '').trim().toLowerCase() === 'askuserquestion'
}

export function parseAskUserQuestions(
  toolInput: Record<string, unknown> | undefined,
): AskUserQuestion[] {
  const rawQuestions = Array.isArray(toolInput?.questions)
    ? toolInput.questions
    : []
  const questions: AskUserQuestion[] = []

  for (const rawQuestion of rawQuestions) {
    const record = toPendingToolInputRecord(rawQuestion)
    if (!record) {
      continue
    }

    const prompt =
      typeof record.prompt === 'string' && record.prompt.trim()
        ? record.prompt.trim()
        : typeof record.question === 'string' && record.question.trim()
          ? record.question.trim()
          : ''
    const questionId =
      typeof record.id === 'string' && record.id.trim()
        ? record.id.trim()
        : prompt
    const rawOptions = Array.isArray(record.options) ? record.options : []
    const options: AskUserQuestionOption[] = []

    for (const rawOption of rawOptions) {
      const optionRecord = toPendingToolInputRecord(rawOption)
      if (!optionRecord) {
        continue
      }

      const label =
        typeof optionRecord.label === 'string' ? optionRecord.label.trim() : ''
      if (!label) {
        continue
      }

      options.push({
        id:
          typeof optionRecord.id === 'string' && optionRecord.id.trim()
            ? optionRecord.id.trim()
            : label,
        label,
        description:
          typeof optionRecord.description === 'string' &&
          optionRecord.description.trim()
            ? optionRecord.description.trim()
            : undefined,
      })
    }

    if (!prompt || options.length < 2) {
      continue
    }

    questions.push({
      id: questionId,
      header:
        typeof record.header === 'string' && record.header.trim()
          ? record.header.trim()
          : undefined,
      prompt,
      options,
      allowMultiple: Boolean(record.allow_multiple ?? record.allowMultiple),
    })
  }

  return questions
}

function hasAskUserQuestionAnswer(answer: AskUserQuestionAnswer): boolean {
  return (
    answer.selectedLabels.length > 0 || answer.freeformText.trim().length > 0
  )
}

function toggleAskUserQuestionOption(
  answer: AskUserQuestionAnswer,
  option: AskUserQuestionOption,
): AskUserQuestionAnswer {
  const existingIndex = answer.selectedOptionIds.indexOf(option.id)
  if (existingIndex >= 0) {
    return {
      ...answer,
      selectedOptionIds: answer.selectedOptionIds.filter(id => id !== option.id),
      selectedLabels: answer.selectedLabels.filter(label => label !== option.label),
    }
  }

  return {
    ...answer,
    selectedOptionIds: [...answer.selectedOptionIds, option.id],
    selectedLabels: [...answer.selectedLabels, option.label],
  }
}

function buildAskUserQuestionToolResult(
  toolInput: Record<string, unknown> | undefined,
  questions: AskUserQuestion[],
  answers: Record<string, AskUserQuestionAnswer>,
): string {
  const donorStyleAnswers: Record<string, string> = {}
  const annotations: Record<string, { notes?: string }> = {}

  for (const question of questions) {
    const answer =
      answers[getAskUserQuestionKey(question)] ??
      createEmptyAskUserQuestionAnswer()
    const selectedLabelsText = answer.selectedLabels.join(', ').trim()
    const notes = answer.freeformText.trim()
    if (!selectedLabelsText && !notes) {
      continue
    }
    donorStyleAnswers[question.prompt] = selectedLabelsText || notes
    if (selectedLabelsText && notes) {
      annotations[question.prompt] = { notes }
    }
  }

  return JSON.stringify(
    {
      ...(toolInput ?? {}),
      answers: donorStyleAnswers,
      ...(Object.keys(annotations).length > 0 ? { annotations } : {}),
    },
    null,
    2,
  )
}

function summarizeAskUserQuestionAnswer(
  answer: AskUserQuestionAnswer,
): string {
  const labelText = answer.selectedLabels.join(', ')
  const freeformText = answer.freeformText.trim()
  if (labelText && freeformText) {
    return `${labelText} | ${freeformText}`
  }
  return labelText || freeformText || 'No answer provided'
}

function parseDraftResult(value: string): SavedToolResult {
  const trimmed = value.trim()
  const isError = trimmed.startsWith('error:')
  return {
    content: isError ? trimmed.slice('error:'.length).trim() : trimmed,
    isError,
  }
}

function findNextIncompleteIndex(
  results: Array<SavedToolResult | null>,
  startIndex: number,
): number {
  for (let offset = 1; offset <= results.length; offset += 1) {
    const nextIndex = (startIndex + offset) % results.length
    if (!results[nextIndex]) {
      return nextIndex
    }
  }
  return startIndex
}

function isBackspaceInput(
  input: string,
  key: { backspace?: boolean; ctrl?: boolean },
): boolean {
  return (
    key.backspace === true ||
    input === '\x7f' ||
    input === '\b' ||
    (key.ctrl === true && input === 'h')
  )
}

function isDeleteInput(
  input: string,
  key: { delete?: boolean },
): boolean {
  return key.delete === true || input === '\x1b[3~'
}

function countDelCharacters(input: string): number {
  return (input.match(/\x7f/g) || []).length
}

function deleteBeforeCursor(
  value: string,
  cursorOffset: number,
  count = 1,
): { value: string; cursorOffset: number } {
  let nextValue = value
  let nextOffset = cursorOffset

  for (let index = 0; index < count; index += 1) {
    if (nextOffset === 0) {
      break
    }
    nextValue =
      nextValue.slice(0, nextOffset - 1) + nextValue.slice(nextOffset)
    nextOffset -= 1
  }

  return {
    value: nextValue,
    cursorOffset: nextOffset,
  }
}

function deleteAtCursor(
  value: string,
  cursorOffset: number,
  count = 1,
): { value: string; cursorOffset: number } {
  let nextValue = value

  for (let index = 0; index < count; index += 1) {
    nextValue =
      nextValue.slice(0, cursorOffset) + nextValue.slice(cursorOffset + 1)
  }

  return {
    value: nextValue,
    cursorOffset,
  }
}

function renderInputLine(
  inputValue: string,
  cursorOffset: number,
  placeholder: string,
  showVisualCursor: boolean,
): React.ReactNode {
  return renderInputLineWithPlaceholder(
    inputValue,
    cursorOffset,
    placeholder,
    showVisualCursor,
  )
}

type AskEditorProps = {
  call: CcminiPendingToolCall
  columns: number
  onSubmit: (results: Array<{
    tool_use_id: string
    content: string
    is_error?: boolean
  }>) => void | Promise<void>
  onAbort: () => void
  themeSetting: ThemeSetting
  truncateInlineText: (value: string, maxLength?: number) => string
}

export function CcminiAskUserQuestionEditor({
  call,
  columns,
  onSubmit,
  onAbort,
  themeSetting,
  truncateInlineText,
}: AskEditorProps): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const isTerminalFocused = useTerminalFocus()
  const accessibilityEnabled = useMemo(
    () => isEnvTruthy(process.env.CLAUDE_CODE_ACCESSIBILITY),
    [],
  )
  const showVisualCursor = isTerminalFocused && !accessibilityEnabled
  const questions = useMemo(
    () => parseAskUserQuestions(call.toolInput),
    [call.toolInput],
  )
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [reviewActionIndex, setReviewActionIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, AskUserQuestionAnswer>>({})
  const [textMode, setTextMode] = useState(false)
  const [textValue, setTextValue] = useState('')
  const [textCursorOffset, setTextCursorOffset] = useState(0)

  const reviewIndex = questions.length
  const inReviewStep = currentQuestionIndex >= reviewIndex
  const currentQuestion = inReviewStep ? null : questions[currentQuestionIndex] ?? null
  const currentQuestionKey = currentQuestion
    ? getAskUserQuestionKey(currentQuestion)
    : ''
  const currentAnswer =
    answers[currentQuestionKey] ?? createEmptyAskUserQuestionAnswer()
  const answeredCount = useMemo(
    () =>
      questions.filter(question =>
        hasAskUserQuestionAnswer(
          answers[getAskUserQuestionKey(question)] ??
            createEmptyAskUserQuestionAnswer(),
        ),
      ).length,
    [answers, questions],
  )
  const allQuestionsAnswered = answeredCount === questions.length && questions.length > 0

  useEffect(() => {
    if (!currentQuestion || inReviewStep) {
      return
    }
    const nextAnswer =
      answers[getAskUserQuestionKey(currentQuestion)] ??
      createEmptyAskUserQuestionAnswer()
    setSelectedIndex(0)
    setTextMode(false)
    setTextValue(nextAnswer.freeformText)
    setTextCursorOffset(nextAnswer.freeformText.length)
  }, [answers, currentQuestion, inReviewStep])

  const textInputState = useTextInput({
    value: textValue,
    onChange: setTextValue,
    onSubmit: undefined,
    onExit: undefined,
    onHistoryUp: () => {},
    onHistoryDown: () => {},
    onHistoryReset: () => {},
    onClearInput: () => {
      setTextValue('')
      setTextCursorOffset(0)
    },
    focus: false,
    multiline: false,
    cursorChar: ' ',
    invert: value => (showVisualCursor ? chalk.inverse(value) : value),
    themeText: value => value,
    columns: Math.max(16, columns - 12),
    disableEscapeDoublePress: true,
    externalOffset: textCursorOffset,
    onOffsetChange: setTextCursorOffset,
  })

  const submitAnswers = useCallback(
    async (nextAnswers: Record<string, AskUserQuestionAnswer>) => {
      await onSubmit([
        {
          tool_use_id: call.toolUseId,
          content: buildAskUserQuestionToolResult(
            call.toolInput,
            questions,
            nextAnswers,
          ),
        },
      ])
    },
    [call.toolInput, call.toolUseId, onSubmit, questions],
  )

  const submitCancellation = useCallback(async () => {
    await onSubmit([
      {
        tool_use_id: call.toolUseId,
        content: 'User canceled AskUserQuestion.',
        is_error: true,
      },
    ])
  }, [call.toolUseId, onSubmit])

  const advanceQuestion = useCallback(
    async (
      nextAnswer: AskUserQuestionAnswer,
      mode: 'advance' | 'submit' = 'advance',
    ) => {
      if (!currentQuestion) {
        return
      }

      const nextAnswers = {
        ...answers,
        [currentQuestionKey]: nextAnswer,
      }
      setAnswers(nextAnswers)

      if (mode === 'submit') {
        await submitAnswers(nextAnswers)
        return
      }

      if (questions.length === 1 && !currentQuestion.allowMultiple) {
        await submitAnswers(nextAnswers)
        return
      }

      if (currentQuestionIndex >= questions.length - 1) {
        setCurrentQuestionIndex(reviewIndex)
        setReviewActionIndex(0)
        return
      }

      setCurrentQuestionIndex(prev => prev + 1)
    },
    [
      answers,
      currentQuestion,
      currentQuestionIndex,
      currentQuestionKey,
      questions.length,
      reviewIndex,
      submitAnswers,
    ],
  )

  useInput(
    (input, key) => {
      if (inReviewStep) {
        if (key.escape || (key.ctrl && input === 'c')) {
          void submitCancellation()
          return
        }

        if (key.leftArrow || (key.ctrl && input === 'p')) {
          if (questions.length > 0) {
            setCurrentQuestionIndex(Math.max(0, questions.length - 1))
            setReviewActionIndex(0)
          }
          return
        }

        if (key.upArrow) {
          setReviewActionIndex(prev => (prev === 0 ? 1 : 0))
          return
        }

        if (key.downArrow || key.tab) {
          setReviewActionIndex(prev => (prev === 0 ? 1 : 0))
          return
        }

        if (!key.return) {
          return
        }

        if (reviewActionIndex === 0) {
          void submitAnswers(answers)
        } else {
          void submitCancellation()
        }
        return
      }

      if (!currentQuestion) {
        return
      }

      if (textMode) {
        if (key.escape || (key.ctrl && input === 'c')) {
          setTextMode(false)
          setTextValue(currentAnswer.freeformText)
          setTextCursorOffset(currentAnswer.freeformText.length)
          return
        }

        if (key.return && !key.shift && !key.meta) {
          const trimmed = textValue.trim()
          if (!trimmed) {
            return
          }
          void advanceQuestion(
            {
              ...currentAnswer,
              freeformText: trimmed,
            },
            'advance',
          )
          return
        }

        textInputState.onInput(input, key)
        return
      }

      const actionCount = currentQuestion.allowMultiple ? 2 : 1
      const itemCount = currentQuestion.options.length + actionCount
      const submitIndex = currentQuestion.allowMultiple
        ? currentQuestion.options.length
        : -1
      const chatIndex = currentQuestion.options.length + actionCount - 1

      if (key.escape || (key.ctrl && input === 'c')) {
        void submitCancellation()
        return
      }

      if (key.upArrow || (key.ctrl && input === 'p')) {
        setSelectedIndex(prev => (prev === 0 ? itemCount - 1 : prev - 1))
        return
      }

      if (key.leftArrow) {
        if (currentQuestionIndex > 0) {
          setCurrentQuestionIndex(prev => Math.max(0, prev - 1))
        }
        return
      }

      if (key.rightArrow) {
        if (currentQuestionIndex < questions.length - 1) {
          setCurrentQuestionIndex(prev =>
            Math.min(questions.length - 1, prev + 1),
          )
        } else if (questions.length > 1) {
          setCurrentQuestionIndex(reviewIndex)
          setReviewActionIndex(0)
        }
        return
      }

      if (key.downArrow || key.tab || (key.ctrl && input === 'n')) {
        setSelectedIndex(prev => (prev + 1) % itemCount)
        return
      }

      const shouldActivate =
        key.return ||
        (currentQuestion.allowMultiple &&
          input === ' ' &&
          selectedIndex < currentQuestion.options.length)

      if (!shouldActivate) {
        return
      }

      if (selectedIndex < currentQuestion.options.length) {
        const option = currentQuestion.options[selectedIndex]!

        if (currentQuestion.allowMultiple) {
          setAnswers(prev => ({
            ...prev,
            [currentQuestionKey]: toggleAskUserQuestionOption(
              prev[currentQuestionKey] ?? createEmptyAskUserQuestionAnswer(),
              option,
            ),
          }))
          return
        }

        void advanceQuestion(
          {
            selectedOptionIds: [option.id],
            selectedLabels: [option.label],
            freeformText: currentAnswer.freeformText,
          },
          'advance',
        )
        return
      }

      if (currentQuestion.allowMultiple && selectedIndex === submitIndex) {
        if (!hasAskUserQuestionAnswer(currentAnswer)) {
          return
        }
        void advanceQuestion(currentAnswer, 'advance')
        return
      }

      if (selectedIndex === chatIndex) {
        setTextMode(true)
        setTextCursorOffset(textValue.length)
      }
    },
    { isActive: true },
  )

  if (!currentQuestion && !inReviewStep) {
    return null
  }

  const submitIndex = currentQuestion?.allowMultiple
    ? currentQuestion.options.length
    : -1
  const chatIndex = currentQuestion
    ? currentQuestion.options.length + (currentQuestion.allowMultiple ? 1 : 0)
    : -1
  const canSubmitSelection = currentQuestion
    ? hasAskUserQuestionAnswer(currentAnswer)
    : false
  const stepLabel = inReviewStep
    ? `${ASK_USER_QUESTION_ICONS.tick} Submit`
    : questions.length > 1
      ? `${currentQuestionIndex + 1}/${questions.length}`
      : undefined

  return (
    <ConsoleSection
      title="Question Flow"
      subtitle={stepLabel ?? 'interactive'}
      themeSetting={themeSetting}
      titleColor={theme.permission}
    >
      <Box flexDirection="column">
        <Text dimColor wrap="wrap">
          {`Tool ${call.toolName || 'AskUserQuestion'} is requesting structured input before the run can continue.`}
        </Text>
        <Text dimColor wrap="wrap">
          {`Question set ${answeredCount}/${questions.length} answered`}
        </Text>
        <Box marginTop={1} flexDirection="column">
          {questions.length > 1 ? (
            <Box marginBottom={1} flexDirection="row" flexWrap="wrap">
              <Text color={currentQuestionIndex === 0 ? theme.subtle : undefined}>
                {'<- '}
              </Text>
              {questions.map((question, index) => {
                const key = getAskUserQuestionKey(question)
                const answered = hasAskUserQuestionAnswer(
                  answers[key] ?? createEmptyAskUserQuestionAnswer(),
                )
                const tab = `${answered ? '[x]' : '[ ]'} ${getAskUserQuestionHeader(question, index, truncateInlineText)}`
                return (
                  <Text key={key} wrap="wrap">
                    {index === currentQuestionIndex
                      ? applyBackground(
                          applyForeground(` ${tab} `, theme.inverseText),
                          theme.permission,
                        )
                      : ` ${tab} `}
                  </Text>
                )
              })}
              <Text wrap="wrap">
                {inReviewStep
                  ? applyBackground(
                      applyForeground(
                        ` ${ASK_USER_QUESTION_ICONS.tick} Submit `,
                        theme.inverseText,
                      ),
                      theme.permission,
                    )
                  : ` ${ASK_USER_QUESTION_ICONS.tick} Submit `}
              </Text>
              <Text color={currentQuestionIndex === reviewIndex ? theme.subtle : undefined}>
                {' ->'}
              </Text>
            </Box>
          ) : null}

          {inReviewStep ? (
            <Box flexDirection="column">
              <Text bold>Review your answers</Text>
              <Box marginTop={1} flexDirection="column">
                {questions.map((question, index) => {
                  const answer =
                    answers[getAskUserQuestionKey(question)] ??
                    createEmptyAskUserQuestionAnswer()
                  return (
                    <Box key={question.id || index} flexDirection="column" marginBottom={1}>
                      <Text>{`${ASK_USER_QUESTION_ICONS.bullet} ${question.prompt}`}</Text>
                      <Box marginLeft={2}>
                        <Text color="green">
                          {`${ASK_USER_QUESTION_ICONS.arrowRight} ${summarizeAskUserQuestionAnswer(answer)}`}
                        </Text>
                      </Box>
                    </Box>
                  )
                })}
              </Box>
              {!allQuestionsAnswered ? (
                <Text color="yellow">
                  {`${ASK_USER_QUESTION_ICONS.warning} You have not answered all questions`}
                </Text>
              ) : null}
              <Box marginTop={1} flexDirection="column">
                <Text wrap="wrap">
                  {reviewActionIndex === 0
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} Submit answers`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : '  Submit answers'}
                </Text>
                <Text wrap="wrap">
                  {reviewActionIndex === 1
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} Cancel`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : '  Cancel'}
                </Text>
              </Box>
              <Box marginTop={1}>
                <Text dimColor italic>
                  Enter to select · ↑/↓ to navigate · ← to go back · Esc to cancel
                </Text>
              </Box>
            </Box>
          ) : (
            <React.Fragment>
              <Text bold wrap="wrap">
                {currentQuestion?.prompt}
              </Text>

              <Box marginTop={1} flexDirection="column">
                {currentQuestion?.options.map((option, index) => {
                  const isActive = !textMode && selectedIndex === index
                  const isSelected =
                    currentAnswer.selectedOptionIds.includes(option.id)
                  const marker = currentQuestion.allowMultiple
                    ? `[${isSelected ? 'x' : ' '}]`
                    : `${index + 1}.`
                  const content = `${marker} ${option.label}`

                  return (
                    <Box key={option.id} flexDirection="column">
                      <Text wrap="wrap">
                        {isActive
                          ? applyBackground(
                              applyForeground(
                                `${DONOR_POINTER} ${content}`,
                                theme.inverseText,
                              ),
                              theme.permission,
                            )
                          : `  ${content}`}
                      </Text>
                      {option.description ? (
                        <Text dimColor wrap="wrap">
                          {`    ${option.description}`}
                        </Text>
                      ) : null}
                    </Box>
                  )
                })}

                {currentQuestion?.allowMultiple ? (
                  <Text wrap="wrap">
                    {!textMode && selectedIndex === submitIndex
                      ? applyBackground(
                          applyForeground(
                            `${DONOR_POINTER} ${currentQuestion.options.length + 1}. Submit selection`,
                            theme.inverseText,
                          ),
                          canSubmitSelection
                            ? theme.permission
                            : theme.userMessageBackground,
                        )
                      : `  ${currentQuestion.options.length + 1}. Submit selection`}
                  </Text>
                ) : null}

                <Text wrap="wrap">
                  {!textMode && selectedIndex === chatIndex
                    ? applyBackground(
                        applyForeground(
                          `${DONOR_POINTER} ${currentQuestion.options.length + (currentQuestion.allowMultiple ? 2 : 1)}. Chat about this`,
                          theme.inverseText,
                        ),
                        theme.permission,
                      )
                    : `  ${currentQuestion.options.length + (currentQuestion.allowMultiple ? 2 : 1)}. Chat about this`}
                </Text>
              </Box>

              {textMode ? (
                <Box flexDirection="column" marginTop={1}>
                  <Text dimColor>Type something.</Text>
                  <Box marginTop={1}>
                    <MainInputLine
                      promptPrefix={`${DONOR_POINTER} `}
                      dimPrefix
                      inputValue={textValue}
                      renderedValue={textInputState.renderedValue}
                      cursorLine={textInputState.cursorLine}
                      cursorColumn={textInputState.cursorColumn}
                      terminalFocused={isTerminalFocused}
                      showVisualCursor={showVisualCursor}
                    />
                  </Box>
                  <Box marginTop={1}>
                    <Text dimColor italic>
                      Enter to submit · Esc to go back
                    </Text>
                  </Box>
                </Box>
              ) : (
                <Box marginTop={1}>
                  <Text dimColor italic>
                    {currentQuestion?.allowMultiple
                      ? 'Space/Enter to toggle · ↑/↓ to navigate · ←/→ switch question · Esc to cancel'
                      : 'Enter to select · ↑/↓ to navigate · ←/→ switch question · Esc to cancel'}
                  </Text>
                </Box>
              )}
            </React.Fragment>
          )}
        </Box>
      </Box>
    </ConsoleSection>
  )
}

type PendingPanelProps = {
  runId: string
  toolName: string
  description: string
  callCount: number
  themeSetting: ThemeSetting
}

export function CcminiPendingToolRequestPanel({
  runId,
  toolName,
  description,
  callCount,
  themeSetting,
}: PendingPanelProps): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <ConsoleSection
      title="Continuation Queue"
      subtitle={toolName}
      themeSetting={themeSetting}
      titleColor={theme.warning}
    >
      <Text wrap="wrap">{description}</Text>
      <Text dimColor>{`Run: ${runId}`}</Text>
      <Text dimColor>
        The remote executor is waiting for client-side tool results.
      </Text>
      {callCount > 1 ? (
        <Text dimColor>{callCount} tool results are waiting to be submitted.</Text>
      ) : null}
    </ConsoleSection>
  )
}

type ToolResultEditorProps = {
  runId: string
  calls: CcminiPendingToolCall[]
  onSubmit: (results: Array<{
    tool_use_id: string
    content: string
    is_error?: boolean
  }>) => void | Promise<void>
  onAbort: () => void
  themeSetting: ThemeSetting
  summarizeToolCall: (call: CcminiPendingToolCall) => string[]
  defaultInputPlaceholder: string
}

export function CcminiToolResultEditor({
  runId,
  calls,
  onSubmit,
  onAbort,
  themeSetting,
  summarizeToolCall,
  defaultInputPlaceholder,
}: ToolResultEditorProps): React.ReactNode {
  const { stdin } = useStdin()
  const isTerminalFocused = useTerminalFocus()
  const accessibilityEnabled = useMemo(
    () => isEnvTruthy(process.env.CLAUDE_CODE_ACCESSIBILITY),
    [],
  )
  const showVisualCursor = isTerminalFocused && !accessibilityEnabled
  const [activeIndex, setActiveIndex] = useState(0)
  const [drafts, setDrafts] = useState(() => calls.map(() => ''))
  const [savedResults, setSavedResults] = useState<Array<SavedToolResult | null>>(
    () => calls.map(() => null),
  )
  const [cursorOffset, setCursorOffset] = useState(0)
  const skipNextDeleteRef = useRef(0)
  const draftsRef = useRef(drafts)
  const cursorOffsetRef = useRef(cursorOffset)
  const activeIndexRef = useRef(activeIndex)

  const activeCall = calls[activeIndex]
  const completedCount = useMemo(
    () => savedResults.filter(result => result !== null).length,
    [savedResults],
  )

  const saveCurrentResult = useCallback(() => {
    const parsed = parseDraftResult(drafts[activeIndex] ?? '')
    const nextResults = [...savedResults]
    nextResults[activeIndex] = parsed
    setSavedResults(nextResults)

    if (nextResults.every(result => result !== null)) {
      void onSubmit(
        nextResults.map((result, index) => ({
          tool_use_id: calls[index]!.toolUseId,
          content: result!.content,
          is_error: result!.isError || undefined,
        })),
      )
      return
    }

    const nextIndex = findNextIncompleteIndex(nextResults, activeIndex)
    setActiveIndex(nextIndex)
    setCursorOffset((drafts[nextIndex] ?? '').length)
  }, [activeIndex, calls, drafts, onSubmit, savedResults])

  useEffect(() => {
    draftsRef.current = drafts
    cursorOffsetRef.current = cursorOffset
    activeIndexRef.current = activeIndex
  }, [activeIndex, cursorOffset, drafts])

  useEffect(() => {
    const handleRawDelete = (chunk: string | Buffer): void => {
      const input = typeof chunk === 'string' ? chunk : chunk.toString('utf8')
      const delCount = countDelCharacters(input)
      if (delCount === 0) {
        return
      }

      const currentIndex = activeIndexRef.current
      const currentDraft = draftsRef.current[currentIndex] ?? ''
      const next = deleteBeforeCursor(
        currentDraft,
        cursorOffsetRef.current,
        delCount,
      )
      skipNextDeleteRef.current += delCount
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[currentIndex] = next.value
          draftsRef.current = updated
          return updated
        })
        setSavedResults(prev => {
          if (!prev[currentIndex]) {
            return prev
          }
          const updated = [...prev]
          updated[currentIndex] = null
          return updated
        })
      }
      cursorOffsetRef.current = next.cursorOffset
      setCursorOffset(next.cursorOffset)
    }

    stdin.on('data', handleRawDelete)
    return () => {
      stdin.off('data', handleRawDelete)
    }
  }, [stdin])

  useInput((input, key) => {
    if (key.delete && skipNextDeleteRef.current > 0) {
      skipNextDeleteRef.current -= 1
      return
    }

    const rawDelCount =
      !key.backspace && !key.delete ? countDelCharacters(input) : 0

    if (key.escape || (key.ctrl && input === 'c')) {
      onAbort()
      return
    }

    if (calls.length > 1) {
      if (key.upArrow) {
        setActiveIndex(prev => (prev === 0 ? calls.length - 1 : prev - 1))
        setCursorOffset(0)
        return
      }
      if (key.downArrow || key.tab) {
        setActiveIndex(prev => (prev + 1) % calls.length)
        setCursorOffset(0)
        return
      }
    }

    if (key.leftArrow) {
      setCursorOffset(prev => Math.max(0, prev - 1))
      return
    }
    if (key.rightArrow) {
      setCursorOffset(prev =>
        Math.min((drafts[activeIndex] ?? '').length, prev + 1),
      )
      return
    }
    if (key.home) {
      setCursorOffset(0)
      return
    }
    if (key.end) {
      setCursorOffset((drafts[activeIndex] ?? '').length)
      return
    }
    if (rawDelCount > 0) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteBeforeCursor(currentDraft, cursorOffset, rawDelCount)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) {
            return prev
          }
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      setCursorOffset(next.cursorOffset)
      return
    }
    if (isBackspaceInput(input, key)) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteBeforeCursor(currentDraft, cursorOffset)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) {
            return prev
          }
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      setCursorOffset(next.cursorOffset)
      return
    }
    if (isDeleteInput(input, key)) {
      const currentDraft = drafts[activeIndex] ?? ''
      const next = deleteAtCursor(currentDraft, cursorOffset)
      if (next.value !== currentDraft) {
        setDrafts(prev => {
          const updated = [...prev]
          updated[activeIndex] = next.value
          return updated
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) {
            return prev
          }
          const updated = [...prev]
          updated[activeIndex] = null
          return updated
        })
      }
      return
    }
    if (key.return) {
      if (key.shift || key.meta) {
        setDrafts(prev => {
          const current = prev[activeIndex] ?? ''
          const next = [...prev]
          next[activeIndex] =
            current.slice(0, cursorOffset) + '\n' + current.slice(cursorOffset)
          return next
        })
        setSavedResults(prev => {
          if (!prev[activeIndex]) {
            return prev
          }
          const next = [...prev]
          next[activeIndex] = null
          return next
        })
        setCursorOffset(prev => prev + 1)
        return
      }
      saveCurrentResult()
      return
    }

    if (!input || key.ctrl || key.meta) {
      return
    }

    setDrafts(prev => {
      const current = prev[activeIndex] ?? ''
      const next = [...prev]
      next[activeIndex] =
        current.slice(0, cursorOffset) + input + current.slice(cursorOffset)
      return next
    })
    setSavedResults(prev => {
      if (!prev[activeIndex]) {
        return prev
      }
      const next = [...prev]
      next[activeIndex] = null
      return next
    })
    setCursorOffset(prev => prev + input.length)
  })

  if (!activeCall) {
    return null
  }

  return (
    <ConsoleSection
      title={calls.length === 1 ? 'Tool Result' : 'Tool Results'}
      subtitle={`${completedCount}/${calls.length} ready`}
      themeSetting={themeSetting}
    >
      <Box flexDirection="column">
        <Text dimColor>{`Run ${runId} is waiting for client-side tool results.`}</Text>

        {calls.length > 1 ? (
          <Box flexDirection="column" marginTop={1}>
            {calls.map((call, index) => {
              const status = savedResults[index]
                ? 'ready'
                : drafts[index]
                  ? 'draft'
                  : 'pending'
              return (
                <Text key={call.toolUseId} bold={index === activeIndex}>
                  {index === activeIndex ? DONOR_POINTER : ' '} {call.toolName} [{status}]
                </Text>
              )
            })}
          </Box>
        ) : null}

        <Box flexDirection="column" marginTop={1}>
          <Text>{`Tool: ${activeCall.toolName}`}</Text>
          <Text dimColor>{`Tool use: ${activeCall.toolUseId}`}</Text>
          {summarizeToolCall(activeCall).map((line, index) => (
            <Text key={`${activeCall.toolUseId}-${index}`} dimColor={index > 0} wrap="wrap">
              {line}
            </Text>
          ))}
        </Box>

        <Text dimColor wrap="wrap">
          Enter saves this result. Shift+Enter inserts a newline. Prefix with{' '}
          <Text bold>error:</Text>{' '}to submit an error result. Esc cancels.
        </Text>

        <Box
          flexDirection="row"
          marginTop={1}
          borderStyle="round"
          borderColor={getFrameBorderColor(themeSetting)}
          paddingX={1}
        >
          <Text>
            {DONOR_POINTER}
            {' '}
          </Text>
          {renderInputLine(
            drafts[activeIndex] ?? '',
            cursorOffset,
            defaultInputPlaceholder,
            showVisualCursor,
          )}
        </Box>
      </Box>
    </ConsoleSection>
  )
}
