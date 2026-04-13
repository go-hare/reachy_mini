import * as React from 'react'
import { Ansi, Box, Text } from '../ink.js'
import { applyForeground } from '../ccmini/ansiText.js'
import { stringWidth } from '../ink/stringWidth.js'
import { useDeclaredCursor } from '../ink/hooks/use-declared-cursor.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'

export type ComposerNoticeLine = {
  text: string
  color?: string
  dimColor?: boolean
}

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
  terminalFocused: boolean
  showVisualCursor: boolean
  placeholderText?: string
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
  placeholderText,
  noticeLines = [],
  footerLeft,
  footerRight,
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
  placeholderText: string
  noticeLines?: ComposerNoticeLine[]
  footerLeft: string
  footerRight: string
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const divider = '─'.repeat(Math.max(16, columns))

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <Text dimColor>{applyForeground(divider, theme.claude)}</Text>
      <Box paddingX={2} flexDirection="column">
        <MainInputLine
          promptPrefix={`${donorPointer} `}
          dimPrefix={false}
          inputValue={inputValue}
          renderedValue={renderedValue}
          cursorLine={cursorLine}
          cursorColumn={cursorColumn}
          placeholderText={placeholderText}
          terminalFocused={terminalFocused}
          showVisualCursor={showVisualCursor}
        />
        {noticeLines.length > 0 ? (
          <Box marginTop={1} flexDirection="column" width="100%">
            {noticeLines.map((line, index) => (
              <Text
                key={`composer-notice-${index}`}
                color={line.color}
                dimColor={line.dimColor}
                wrap="wrap"
              >
                {line.text}
              </Text>
            ))}
          </Box>
        ) : null}
      </Box>
      <Text dimColor>{applyForeground(divider, theme.claude)}</Text>
      <Box paddingX={2} width="100%">
        <Text dimColor wrap="truncate-end">
          {padLineToWidth(
            footerLeft,
            footerRight,
            Math.max(24, columns - 4),
          )}
        </Text>
      </Box>
    </Box>
  )
}
