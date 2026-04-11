import * as React from 'react'
import { Box, Text } from '../ink.js'
import {
  AssistantRedactedThinkingMessage,
  AssistantThinkingMessage,
} from '../ccmini-thinking/index.js'
import { applyForeground } from '../ccmini/ansiText.js'
import { getThemeTokens } from '../ccmini/themePalette.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'

type ToolRenderLine = {
  text: string
  color?: string
  dimColor?: boolean
}

type ToolResultPresentation = {
  header?: ToolRenderLine
  bodyLines: ToolRenderLine[]
}

type CollapsedReadSearchEntryLike = {
  hint?: string
  isActive: boolean
}

function MessageResponseFlow({
  children,
  color,
  dimColor = true,
}: {
  children: React.ReactNode
  color?: string
  dimColor?: boolean
}): React.ReactNode {
  return (
    <Box flexDirection="row" width="100%">
      <Box minWidth={6} flexShrink={0}>
        <Text color={color} dimColor={dimColor}>
          {'  ⎿  '}
        </Text>
      </Box>
      <Box flexDirection="column" flexGrow={1} flexShrink={1}>
        {children}
      </Box>
    </Box>
  )
}

function MessageDotFlow({
  children,
  marginTop = 0,
  color,
  dimColor = false,
}: {
  children: React.ReactNode
  marginTop?: number
  color?: string
  dimColor?: boolean
}): React.ReactNode {
  return (
    <Box flexDirection="row" width="100%" marginTop={marginTop}>
      <Box minWidth={2} flexShrink={0}>
        <Text color={color} dimColor={dimColor}>
          {'●'}
        </Text>
      </Box>
      <Box flexDirection="column" flexGrow={1} flexShrink={1}>
        {children}
      </Box>
    </Box>
  )
}

export function AssistantFlow({
  lines,
  width,
  themeSetting,
}: {
  lines: string[]
  width: number
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const content = lines.join('\n').trimEnd()

  return (
    <MessageDotFlow color={theme.claude}>
      <Box flexDirection="column" width={width}>
        <Text wrap="wrap">{content}</Text>
      </Box>
    </MessageDotFlow>
  )
}

export function SystemFlow({
  content,
  addMargin,
  dot,
  color,
  dimColor,
  width,
}: {
  content: string
  addMargin: boolean
  dot: boolean
  color?: 'yellow' | 'red'
  dimColor: boolean
  width: number
}): React.ReactNode {
  return (
    <Box width="100%">
      {dot ? (
        <MessageDotFlow
          marginTop={addMargin ? 1 : 0}
          color={color}
          dimColor={dimColor}
        >
          <Box flexDirection="column" width={width}>
            <Text color={color} dimColor={dimColor} wrap="wrap">
              {content.trim()}
            </Text>
          </Box>
        </MessageDotFlow>
      ) : (
        <MessageResponseFlow color={color} dimColor={dimColor}>
          <Box flexDirection="column" width={width}>
            <Text color={color} dimColor={dimColor} wrap="wrap">
              {content.trim()}
            </Text>
          </Box>
        </MessageResponseFlow>
      )}
    </Box>
  )
}

export function UserPromptFlow({
  content,
  addMargin,
  themeSetting,
  width,
}: {
  content: string
  addMargin: boolean
  themeSetting: ThemeSetting
  width: number
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)
  const lines = content.split('\n')

  return (
    <Box marginTop={addMargin ? 1 : 0} width="100%">
      <Box
        flexDirection="column"
        width={width}
        paddingRight={1}
        backgroundColor={theme.userMessageBackground}
      >
        {lines.map((line, index) => (
          <Text key={index} wrap="wrap">
            {line || ' '}
          </Text>
        ))}
      </Box>
    </Box>
  )
}

export function ToolUseFlow({
  toolName,
  toolInput,
  width,
  formatToolUseTitle,
  getToolAccentColor,
  getToolUseBodyLines,
}: {
  toolName: string
  toolInput?: Record<string, unknown>
  width: number
  formatToolUseTitle: (
    toolName: string,
    input: Record<string, unknown> | undefined,
  ) => string
  getToolAccentColor: (toolName: string) => string | undefined
  getToolUseBodyLines: (
    toolName: string,
    input: Record<string, unknown> | undefined,
  ) => ToolRenderLine[]
}): React.ReactNode {
  const title = formatToolUseTitle(toolName, toolInput)
  const accentColor = getToolAccentColor(toolName)
  const bodyLines = getToolUseBodyLines(toolName, toolInput)

  return (
    <MessageDotFlow color={accentColor}>
      <Box flexDirection="column" width={width}>
        <Text color={accentColor} wrap="wrap">
          {title}
        </Text>
        {bodyLines.length > 0
          ? bodyLines.map((line, index) => (
              <Text
                key={`${toolName}-${index}`}
                color={line.color}
                dimColor={line.dimColor}
                wrap="wrap"
              >
                {line.text}
              </Text>
            ))
          : null}
      </Box>
    </MessageDotFlow>
  )
}

export function ToolResultFlow({
  rawResult,
  toolName,
  toolInput,
  isError,
  width,
  buildToolResultPresentation,
  getToolAccentColor,
}: {
  rawResult: unknown
  toolName?: string
  toolInput?: Record<string, unknown>
  isError: boolean
  width?: number
  buildToolResultPresentation: (args: {
    rawResult: unknown
    toolName?: string
    toolInput?: Record<string, unknown>
    isError: boolean
  }) => ToolResultPresentation
  getToolAccentColor: (toolName: string) => string | undefined
}): React.ReactNode {
  const presentation = buildToolResultPresentation({
    rawResult,
    toolName,
    toolInput,
    isError,
  })
  const accentColor = isError
    ? 'red'
    : toolName
      ? getToolAccentColor(toolName)
      : undefined

  return (
    <MessageDotFlow color={accentColor} dimColor={!accentColor}>
      <Box flexDirection="column" width={width ?? '100%'}>
        {presentation.header ? (
          <Text
            color={presentation.header.color}
            dimColor={presentation.header.dimColor}
            wrap="wrap"
          >
            {presentation.header.text}
          </Text>
        ) : null}
        {presentation.bodyLines.map((line, index) => (
          <Text
            key={`${toolName ?? 'tool'}-result-${index}`}
            color={line.color}
            dimColor={line.dimColor}
            wrap="wrap"
          >
            {line.text}
          </Text>
        ))}
      </Box>
    </MessageDotFlow>
  )
}

export function ToolProgressFlow({
  content,
  toolName,
  width,
  getToolAccentColor,
}: {
  content: string
  toolName?: string
  width?: number
  getToolAccentColor: (toolName: string) => string | undefined
}): React.ReactNode {
  return (
    <MessageDotFlow
      color={toolName ? getToolAccentColor(toolName) : undefined}
      dimColor={!toolName}
    >
      <Box flexDirection="column" width={width ?? '100%'}>
        {content
          .split('\n')
          .map(line => line.trimEnd())
          .filter(Boolean)
          .map((line, index) => (
            <Text key={`${toolName ?? 'tool'}-progress-${index}`} dimColor wrap="wrap">
              {line}
            </Text>
          ))}
      </Box>
    </MessageDotFlow>
  )
}

export function CollapsedReadSearchFlow({
  entry,
  width,
  summarizeCollapsedReadSearchEntry,
}: {
  entry: CollapsedReadSearchEntryLike
  width: number
  summarizeCollapsedReadSearchEntry: (
    entry: CollapsedReadSearchEntryLike,
  ) => string
}): React.ReactNode {
  const summary = summarizeCollapsedReadSearchEntry(entry)

  return (
    <Box flexDirection="column" width="100%">
      <MessageDotFlow dimColor={!entry.isActive}>
        <Box flexDirection="column" width={width}>
          <Text dimColor={!entry.isActive} wrap="wrap">
            {summary}
          </Text>
        </Box>
      </MessageDotFlow>
      {entry.isActive && entry.hint ? (
        <MessageResponseFlow>
          <Text dimColor wrap="wrap">
            {entry.hint}
          </Text>
        </MessageResponseFlow>
      ) : null}
    </Box>
  )
}

export function ThinkingFlow({
  thinking,
  isRedacted,
  verbose,
  themeSetting,
}: {
  thinking: string
  isRedacted: boolean
  verbose: boolean
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <MessageDotFlow color={theme.claude}>
      <Box flexDirection="column" width="100%">
        {isRedacted ? (
          <AssistantRedactedThinkingMessage />
        ) : (
          <AssistantThinkingMessage
            param={{
              type: 'thinking',
              thinking,
            }}
            isTranscriptMode={false}
            verbose={verbose}
          />
        )}
      </Box>
    </MessageDotFlow>
  )
}

export function WorkingStatusFlow({
  verb,
  themeSetting,
}: {
  verb: string
  themeSetting: ThemeSetting
}): React.ReactNode {
  const theme = getThemeTokens(themeSetting)

  return (
    <Box marginTop={1} flexDirection="column">
      <MessageDotFlow color={theme.claude}>
        <Text>{applyForeground(`${verb}...`, theme.claude)}</Text>
      </MessageDotFlow>
    </Box>
  )
}
