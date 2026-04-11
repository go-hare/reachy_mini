import * as React from 'react'
import { Box, Text } from '../ink.js'
import { applyForeground } from '../ccmini/ansiText.js'
import type { DonorCommandCatalogEntry } from '../ccmini/donorCommandCatalog.js'
import { getCommandStatusLabel } from '../ccmini/donorCommandPresentation.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import { THEME_OPTIONS, type ThemeSetting } from '../ccmini/themeTypes.js'

const COMMAND_PANEL_VISIBLE_COUNT = 8

function getVisibleCommandWindowStart(
  total: number,
  visibleCount: number,
  selectedIndex: number,
): number {
  if (total <= visibleCount) {
    return 0
  }

  const half = Math.floor(visibleCount / 2)
  const maxStart = total - visibleCount
  return Math.max(0, Math.min(selectedIndex - half, maxStart))
}

function InlineOverlayHeader({
  title,
  themeSetting,
  width,
}: {
  title: string
  themeSetting: ThemeSetting
  width: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const ruleWidth = Math.max(20, width)

  return (
    <Box flexDirection="column" width="100%">
      <Text>{applyForeground('─'.repeat(ruleWidth), theme.permission)}</Text>
      <Text wrap="wrap">{applyForeground(title, theme.permission)}</Text>
    </Box>
  )
}

export function PromptHelpMenu({
  themeSetting,
  columns,
}: {
  themeSetting: ThemeSetting
  columns: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const compact = columns < 86

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <InlineOverlayHeader
        title="Shortcuts"
        themeSetting={themeSetting}
        width={columns}
      />
      <Box
        flexDirection="column"
        marginTop={1}
        paddingLeft={1}
        width="100%"
      >
        <Box flexDirection={compact ? 'column' : 'row'} width="100%">
          <Box flexDirection="column" width={compact ? '100%' : 28}>
            <Text>{applyForeground('Prompt commands', theme.claude)}</Text>
            <Text dimColor>/ for commands</Text>
            <Text dimColor>@ for file paths</Text>
            <Text dimColor>& for background</Text>
            <Text dimColor>/btw for side question</Text>
            <Text dimColor>/theme for text style</Text>
          </Box>
          <Box
            flexDirection="column"
            width={compact ? '100%' : 40}
            marginLeft={compact ? 0 : 4}
            marginTop={compact ? 1 : 0}
          >
            <Text>{applyForeground('Keyboard flow', theme.claude)}</Text>
            <Text dimColor>double tap esc to clear input</Text>
            <Text dimColor>shift + tab to auto-accept edits</Text>
            <Text dimColor>ctrl + o for verbose output</Text>
            <Text dimColor>ctrl + t to toggle syntax preview</Text>
            <Text dimColor>shift + ⏎ for newline</Text>
          </Box>
        </Box>
      </Box>
    </Box>
  )
}

export function CommandCatalogPanel({
  entries,
  selectedIndex,
  query,
  themeSetting,
  columns,
  donorPointer,
  describeCommand,
}: {
  entries: DonorCommandCatalogEntry[]
  selectedIndex: number
  query: string
  themeSetting: ThemeSetting
  columns: number
  donorPointer: string
  describeCommand: (entry: DonorCommandCatalogEntry) => string[]
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const bodyWidth = Math.max(32, columns)
  const windowStart = getVisibleCommandWindowStart(
    entries.length,
    COMMAND_PANEL_VISIBLE_COUNT,
    selectedIndex,
  )
  const visibleEntries = entries.slice(
    windowStart,
    windowStart + COMMAND_PANEL_VISIBLE_COUNT,
  )
  const selectedEntry = entries[selectedIndex]

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <InlineOverlayHeader
        title={query ? `Commands · /${query}` : 'Commands'}
        themeSetting={themeSetting}
        width={bodyWidth}
      />
      <Box
        flexDirection="column"
        marginTop={1}
        paddingLeft={1}
        width="100%"
      >
        <Text dimColor>
          {entries.length === 0
            ? `No commands match "/${query}"`
            : `Showing ${entries.length} commands for "/${query}"`}
        </Text>
        {visibleEntries.length > 0 ? (
          <Box flexDirection="column" marginTop={1} width="100%">
            {visibleEntries.map((entry, index) => {
              const actualIndex = windowStart + index
              const line = `/${entry.name}${entry.argumentHint ? ` ${entry.argumentHint}` : ''}`
              return (
                <Text key={entry.sourcePath} dimColor={actualIndex !== selectedIndex}>
                  {actualIndex === selectedIndex
                    ? applyForeground(
                        `${donorPointer} ${line}`,
                        theme.permission,
                      )
                    : `  ${line}`}
                  <Text dimColor> [{getCommandStatusLabel(entry)}]</Text>
                </Text>
              )
            })}
          </Box>
        ) : null}
        {selectedEntry ? (
          <Box flexDirection="column" marginTop={1} width="100%">
            {describeCommand(selectedEntry).map((line, index) => (
              <Text key={`${selectedEntry.sourcePath}-${index}`} dimColor={index > 0} wrap="wrap">
                {line}
              </Text>
            ))}
          </Box>
        ) : null}
        <Box marginTop={1}>
          <Text dimColor italic wrap="wrap">
            Up/Down choose  Tab inserts  Esc cancels
          </Text>
        </Box>
      </Box>
    </Box>
  )
}

export function ThemePickerPanel({
  selectedIndex,
  previewThemeSetting,
  syntaxHighlightingDisabled,
  columns,
  donorPointer,
}: {
  selectedIndex: number
  previewThemeSetting: ThemeSetting
  syntaxHighlightingDisabled: boolean
  columns: number
  donorPointer: string
}): React.ReactNode {
  const theme = getThemeTokens(previewThemeSetting)
  const bodyWidth = Math.max(32, columns)

  return (
    <Box marginTop={1} flexDirection="column" width="100%">
      <InlineOverlayHeader
        title="Select theme"
        themeSetting={previewThemeSetting}
        width={bodyWidth}
      />
      <Box
        flexDirection="column"
        marginTop={1}
        paddingLeft={1}
        width="100%"
      >
        <Text color={theme.permission}>
          Choose the text style that looks best with your terminal.
        </Text>
        <Box flexDirection="column" marginTop={1}>
          {THEME_OPTIONS.map((option, index) => (
            <Text key={option.value}>
              {index === selectedIndex
                ? applyForeground(`${donorPointer} ${option.label}`, theme.permission)
                : `${index + 1}. ${option.label}`}
            </Text>
          ))}
        </Box>
        <Box flexDirection="column" width="100%" marginTop={1}>
          <Text dimColor>{'╌'.repeat(36)}</Text>
          <Text dimColor wrap="wrap">
            Switch between text styles. Applies to this session and future sessions.
          </Text>
          <Text color={syntaxHighlightingDisabled ? undefined : 'green'}>
            {'• Syntax preview active'}
          </Text>
          <Text dimColor>
            {syntaxHighlightingDisabled
              ? 'ctrl+t to enable syntax colors'
              : 'ctrl+t to disable syntax colors'}
          </Text>
        </Box>
        <Box marginTop={1}>
          <Text dimColor italic>
            Enter to confirm · Esc to exit
          </Text>
        </Box>
      </Box>
    </Box>
  )
}
