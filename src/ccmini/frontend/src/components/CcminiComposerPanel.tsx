import * as React from 'react'
import { Ansi, Box, Text } from '../ink.js'
import { applyForeground } from '../ccmini/ansiText.js'
import { stringWidth } from '../ink/stringWidth.js'
import { useDeclaredCursor } from '../ink/hooks/use-declared-cursor.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'

export function renderInputLineWithPlaceholder(
  inputValue: string,
  cursorOffset: number,
  placeholderText: string,
  showVisualCursor: boolean,
): React.ReactNode {
  if (inputValue.length === 0) {
    return (
      <Text>
        {showVisualCursor ? <Text inverse>{' '}</Text> : null}
        {placeholderText ? <Text>{placeholderText}</Text> : null}
      </Text>
    )
  }

  const safeOffset = Math.max(0, Math.min(cursorOffset, inputValue.length))
  const before = inputValue.slice(0, safeOffset)
  const current = inputValue[safeOffset] ?? ' '
  const after = inputValue.slice(
    safeOffset + (safeOffset < inputValue.length ? 1 : 0),
  )

  return (
    <Text>
      {before}
      {showVisualCursor ? <Text inverse>{current}</Text> : <Text>{current}</Text>}
      {after}
    </Text>
  )
}

export function MainInputLine({
  promptPrefix = '> ',
  dimPrefix = false,
  inputValue,
  renderedValue,
  cursorLine,
  cursorColumn,
  placeholderText = '',
  terminalFocused,
  showVisualCursor,
}: {
  promptPrefix?: string
  dimPrefix?: boolean
  inputValue: string
  renderedValue: string
  cursorLine: number
  cursorColumn: number
  placeholderText?: string
  terminalFocused: boolean
  showVisualCursor: boolean
}): React.ReactNode {
  const prefixWidth = stringWidth(promptPrefix)
  const cursorRef = useDeclaredCursor({
    line: cursorLine,
    column: cursorColumn + prefixWidth,
    active: terminalFocused,
  })

  return (
    <Box
      ref={cursorRef}
      flexDirection="row"
      flexGrow={1}
      flexShrink={1}
      minHeight={1}
      width="100%"
    >
      <Box minWidth={stringWidth(promptPrefix)} flexShrink={0}>
        <Text dimColor={dimPrefix}>{promptPrefix}</Text>
      </Box>
      <Text wrap="truncate-end">
        {inputValue.length === 0 ? (
          renderInputLineWithPlaceholder(
            inputValue,
            0,
            placeholderText,
            showVisualCursor,
          )
        ) : (
          <Ansi>{renderedValue}</Ansi>
        )}
      </Text>
    </Box>
  )
}

export function ComposerPanel({
  themeSetting,
  columns,
  inputValue,
  renderedValue,
  cursorLine,
  cursorColumn,
  donorPointer,
  padLineToWidth,
  terminalFocused,
  showVisualCursor,
}: {
  themeSetting: ThemeSetting
  columns: number
  inputValue: string
  renderedValue: string
  cursorLine: number
  cursorColumn: number
  donorPointer: string
  padLineToWidth: (left: string, right: string, width: number) => string
  terminalFocused: boolean
  showVisualCursor: boolean
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const divider = '─'.repeat(Math.max(16, columns))

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <Text dimColor>{applyForeground(divider, theme.claude)}</Text>
      <Box paddingLeft={1} flexDirection="column">
        <MainInputLine
          promptPrefix={`${donorPointer} `}
          dimPrefix={false}
          inputValue={inputValue}
          renderedValue={renderedValue}
          cursorLine={cursorLine}
          cursorColumn={cursorColumn}
          placeholderText=""
          terminalFocused={terminalFocused}
          showVisualCursor={showVisualCursor}
        />
      </Box>
      <Text dimColor>{applyForeground(divider, theme.claude)}</Text>
      <Box paddingLeft={1} width="100%">
        <Text dimColor wrap="truncate-end">
          {padLineToWidth(
            '? for shortcuts',
            '● high · /effort',
            Math.max(24, columns - 2),
          )}
        </Text>
      </Box>
    </Box>
  )
}
