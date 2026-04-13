import * as React from 'react'
import { stat } from 'fs/promises'
import { resolve } from 'path'
import chalk from 'chalk'
import { Box, Text, useInput, useTerminalFocus } from '../ink.js'
import { useTextInput } from '../hooks/useTextInput.js'
import {
  MainInputLine,
} from './CcminiComposerPanel.js'
import { applyBackground, applyForeground } from '../ccmini/ansiText.js'
import { getDirectorySuggestions } from '../ccmini/directorySuggestions.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'
import { isEnvTruthy } from '../utils/envUtils.js'

const DONOR_POINTER = '❯'

type Props = {
  initialValue?: string
  columns: number
  themeSetting: ThemeSetting
  onSubmit: (path: string, options: { remember: boolean }) => void | Promise<void>
  onAbort: () => void
}

async function validateDirectoryPath(inputValue: string): Promise<{
  ok: true
  resolvedPath: string
} | {
  ok: false
  error: string
}> {
  const trimmed = inputValue.trim()
  if (!trimmed) {
    return {
      ok: false,
      error: 'Please enter a directory path.',
    }
  }

  const resolvedPath = resolve(trimmed)
  try {
    const info = await stat(resolvedPath)
    if (!info.isDirectory()) {
      return {
        ok: false,
        error: `Path is not a directory: ${resolvedPath}`,
      }
    }
  } catch {
    return {
      ok: false,
      error: `Directory not found: ${resolvedPath}`,
    }
  }

  return {
    ok: true,
    resolvedPath,
  }
}

function AddDirectoryDialog({
  title,
  subtitle,
  titleRight,
  themeSetting,
  children,
}: {
  title: string
  subtitle?: React.ReactNode
  titleRight?: React.ReactNode
  themeSetting: ThemeSetting
  children: React.ReactNode
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.permission}
      borderLeft={false}
      borderRight={false}
      borderBottom={false}
      marginTop={1}
      width="100%"
    >
      <Box paddingX={1} flexDirection="column">
        <Box justifyContent="space-between">
          <Box flexDirection="column">
            <Text bold color={theme.permission}>
              {title}
            </Text>
            {subtitle != null
              ? typeof subtitle === 'string'
                ? (
                    <Text dimColor wrap="truncate-start">
                      {subtitle}
                    </Text>
                  )
                : subtitle
              : null}
          </Box>
          {titleRight}
        </Box>
      </Box>
      <Box flexDirection="column" paddingX={1}>
        {children}
      </Box>
    </Box>
  )
}

function AddDirectoryPromptSurface({
  columns,
  themeSetting,
  terminalFocused,
  showVisualCursor,
  inputValue,
  renderedValue,
  cursorLine,
  cursorColumn,
  footerText,
}: {
  columns: number
  themeSetting: ThemeSetting
  terminalFocused: boolean
  showVisualCursor: boolean
  inputValue: string
  renderedValue: string
  cursorLine: number
  cursorColumn: number
  footerText: string
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const divider = '─'.repeat(Math.max(24, columns - 6))

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <Text dimColor>{applyForeground(divider, theme.permission)}</Text>
      <Box paddingX={2}>
        <MainInputLine
          promptPrefix={`${DONOR_POINTER} `}
          dimPrefix={false}
          inputValue={inputValue}
          renderedValue={renderedValue}
          cursorLine={cursorLine}
          cursorColumn={cursorColumn}
          terminalFocused={terminalFocused}
          showVisualCursor={showVisualCursor}
        />
      </Box>
      <Text dimColor>{applyForeground(divider, theme.permission)}</Text>
      <Box paddingX={2}>
        <Text dimColor italic wrap="wrap">
          {footerText}
        </Text>
      </Box>
    </Box>
  )
}

export function CcminiAddDirectoryEditor({
  initialValue = '',
  columns,
  themeSetting,
  onSubmit,
  onAbort,
}: Props): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const isTerminalFocused = useTerminalFocus()
  const accessibilityEnabled = React.useMemo(
    () => isEnvTruthy(process.env.CLAUDE_CODE_ACCESSIBILITY),
    [],
  )
  const showVisualCursor = isTerminalFocused && !accessibilityEnabled
  const [inputValue, setInputValue] = React.useState(initialValue)
  const [cursorOffset, setCursorOffset] = React.useState(initialValue.length)
  const [suggestions, setSuggestions] = React.useState<string[]>([])
  const [selectedSuggestionIndex, setSelectedSuggestionIndex] = React.useState(0)
  const [error, setError] = React.useState('')
  const [confirmedPath, setConfirmedPath] = React.useState('')
  const [selectedConfirmIndex, setSelectedConfirmIndex] = React.useState(0)

  const textInputState = useTextInput({
    value: inputValue,
    onChange: setInputValue,
    onSubmit: undefined,
    onExit: undefined,
    onHistoryUp: () => {},
    onHistoryDown: () => {},
    onHistoryReset: () => {},
    onClearInput: () => {
      setInputValue('')
      setCursorOffset(0)
    },
    focus: false,
    multiline: false,
    cursorChar: ' ',
    invert: value => (showVisualCursor ? chalk.inverse(value) : value),
    themeText: value => value,
    columns: Math.max(24, columns - 12),
    disableEscapeDoublePress: true,
    externalOffset: cursorOffset,
    onOffsetChange: setCursorOffset,
  })

  React.useEffect(() => {
    if (confirmedPath) {
      return
    }
    const timer = setTimeout(() => {
      void getDirectorySuggestions(inputValue).then(nextSuggestions => {
        setSuggestions(nextSuggestions)
        setSelectedSuggestionIndex(0)
      })
    }, 80)
    return () => clearTimeout(timer)
  }, [confirmedPath, inputValue])

  const applySuggestion = React.useCallback((value: string) => {
    const nextValue = value.endsWith('\\') || value.endsWith('/')
      ? value
      : `${value}${process.platform === 'win32' ? '\\' : '/'}`
    setInputValue(nextValue)
    setCursorOffset(nextValue.length)
    setError('')
  }, [])

  const confirmDirectory = React.useCallback(async (value: string) => {
    const result = await validateDirectoryPath(value)
    if ('error' in result) {
      setError(result.error)
      return
    }
    setError('')
    setSuggestions([])
    setConfirmedPath(result.resolvedPath)
    setSelectedConfirmIndex(0)
  }, [])

  useInput((input, key) => {
    if (confirmedPath) {
      if (key.escape || (key.ctrl && input === 'c')) {
        onAbort()
        return
      }
      if (key.upArrow || key.leftArrow) {
        setSelectedConfirmIndex(prev => (prev === 0 ? 2 : prev - 1))
        return
      }
      if (key.downArrow || key.rightArrow || key.tab) {
        setSelectedConfirmIndex(prev => (prev + 1) % 3)
        return
      }
      if (!key.return) {
        return
      }
      if (selectedConfirmIndex === 0) {
        void onSubmit(confirmedPath, { remember: false })
      } else if (selectedConfirmIndex === 1) {
        void onSubmit(confirmedPath, { remember: true })
      } else {
        onAbort()
      }
      return
    }

    if (key.escape || (key.ctrl && input === 'c')) {
      onAbort()
      return
    }

    if (suggestions.length > 0) {
      if (key.upArrow || (key.ctrl && input === 'p')) {
        setSelectedSuggestionIndex(prev =>
          prev <= 0 ? suggestions.length - 1 : prev - 1,
        )
        return
      }
      if (key.downArrow || key.tab || (key.ctrl && input === 'n')) {
        setSelectedSuggestionIndex(prev => (prev + 1) % suggestions.length)
        return
      }
      if (key.return) {
        const selectedSuggestion = suggestions[selectedSuggestionIndex]
        if (selectedSuggestion) {
          void confirmDirectory(selectedSuggestion)
          return
        }
      }
    }

    if (key.return) {
      void confirmDirectory(inputValue)
      return
    }

    textInputState.onInput(input, key)
  })

  return (
    <AddDirectoryDialog
      title="Reference Project"
      subtitle="Attach a donor or reference directory before continuing."
      titleRight={<Text dimColor>/add-dir</Text>}
      themeSetting={themeSetting}
    >
      <Box flexDirection="column">
        {!confirmedPath ? (
          <React.Fragment>
            <Text color={theme.permission} wrap="wrap">
              Add a donor/reference directory to this session so the agent can
              read it as an extra working context.
            </Text>
            <Text dimColor wrap="wrap">
              Enter a directory path, then choose whether to keep it for this
              session only or remember it globally.
            </Text>
            <AddDirectoryPromptSurface
              columns={columns}
              themeSetting={themeSetting}
              terminalFocused={isTerminalFocused}
              showVisualCursor={showVisualCursor}
              inputValue={inputValue}
              renderedValue={textInputState.renderedValue}
              cursorLine={textInputState.cursorLine}
              cursorColumn={textInputState.cursorColumn}
              footerText="Enter to continue · ↑/↓ to navigate suggestions · Esc to cancel"
            />
            {suggestions.length > 0 ? (
              <Box marginTop={1} flexDirection="column">
                {suggestions.map((suggestion, index) => (
                  <Text key={suggestion} wrap="wrap">
                    {index === selectedSuggestionIndex
                      ? applyBackground(
                          applyForeground(
                            `${DONOR_POINTER} ${suggestion}`,
                            theme.inverseText,
                          ),
                          theme.permission,
                        )
                      : `  ${suggestion}`}
                  </Text>
                ))}
              </Box>
            ) : null}
            {error ? (
              <Box marginTop={1}>
                <Text color={theme.error} wrap="wrap">
                  {error}
                </Text>
              </Box>
            ) : null}
          </React.Fragment>
        ) : (
          <React.Fragment>
            <Text color={theme.permission} wrap="wrap">
              {confirmedPath}
            </Text>
            <Text dimColor wrap="wrap">
              Choose how this reference directory should be added.
            </Text>
            <Box marginTop={1} flexDirection="column">
              <Text wrap="wrap">
                {selectedConfirmIndex === 0
                  ? applyBackground(
                      applyForeground(
                        `${DONOR_POINTER} Yes, for this session`,
                        theme.inverseText,
                      ),
                      theme.permission,
                    )
                  : '  Yes, for this session'}
              </Text>
              <Text wrap="wrap">
                {selectedConfirmIndex === 1
                  ? applyBackground(
                      applyForeground(
                        `${DONOR_POINTER} Yes, and remember this directory`,
                        theme.inverseText,
                      ),
                      theme.permission,
                    )
                  : '  Yes, and remember this directory'}
              </Text>
              <Text wrap="wrap">
                {selectedConfirmIndex === 2
                  ? applyBackground(
                      applyForeground(
                        `${DONOR_POINTER} No`,
                        theme.inverseText,
                      ),
                      theme.error,
                    )
                  : '  No'}
              </Text>
            </Box>
            <Box marginTop={1}>
              <Text dimColor italic>
                Enter to choose · ↑/↓ to navigate · Esc to cancel
              </Text>
            </Box>
          </React.Fragment>
        )}
      </Box>
    </AddDirectoryDialog>
  )
}
