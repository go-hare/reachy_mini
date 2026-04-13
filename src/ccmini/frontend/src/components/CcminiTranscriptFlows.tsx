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

function ResponseLineList({
  lines,
  width,
}: {
  lines: ToolRenderLine[]
  width: number | string
}): React.ReactNode {
  if (lines.length === 0) {
    return null
  }

  return (
    <Box flexDirection="column" width="100%">
      {lines.map((line, index) => (
        <MessageResponseFlow
          key={`response-line-${index}`}
          color={line.color}
          dimColor={line.dimColor}
        >
          <Box flexDirection="column" width={width}>
            <Text
              color={line.color}
              dimColor={line.dimColor}
              wrap="wrap"
            >
              {line.text}
            </Text>
          </Box>
        </MessageResponseFlow>
      ))}
    </Box>
  )
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
    <MessageResponseFlow color={theme.text} dimColor={false}>
      <Box flexDirection="column" width={width}>
        <Text wrap="wrap">{content}</Text>
      </Box>
    </MessageResponseFlow>
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
    <Box flexDirection="column" width="100%">
      <MessageDotFlow color={accentColor}>
        <Box flexDirection="column" width={width}>
          <Text color={accentColor} wrap="wrap">
            {title}
          </Text>
        </Box>
      </MessageDotFlow>
      <ResponseLineList lines={bodyLines} width={width} />
    </Box>
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
  const primaryLine = presentation.header ?? presentation.bodyLines[0]
  const detailLines = presentation.header
    ? presentation.bodyLines
    : presentation.bodyLines.slice(1)

  if (!primaryLine) {
    return null
  }

  return (
    <Box flexDirection="column" width="100%">
      <MessageDotFlow color={accentColor} dimColor={!accentColor}>
        <Box flexDirection="column" width={width ?? '100%'}>
          <Text
            color={primaryLine.color}
            dimColor={primaryLine.dimColor}
            wrap="wrap"
          >
            {primaryLine.text}
          </Text>
        </Box>
      </MessageDotFlow>
      <ResponseLineList lines={detailLines} width={width ?? '100%'} />
    </Box>
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
  const lines = content
    .split('\n')
    .map(line => line.trimEnd())
    .filter(Boolean)
    .map(line => ({
      text: line,
      dimColor: true,
    }))

  return (
    <ResponseLineList
      lines={lines}
      width={width ?? '100%'}
    />
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
    <MessageDotFlow color={theme.text}>
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
      <MessageResponseFlow color={theme.text} dimColor={false}>
        <Text>{applyForeground(`${verb}...`, theme.text)}</Text>
      </MessageResponseFlow>
    </Box>
  )
}
