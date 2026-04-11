import * as React from 'react'
import { Box } from '../ink.js'
import {
  AssistantFlow,
  SystemFlow,
  ThinkingFlow,
  ToolProgressFlow,
  ToolResultFlow,
  ToolUseFlow,
  UserPromptFlow,
  WorkingStatusFlow,
} from './CcminiTranscriptFlows.js'
import { CcminiPendingToolRequestPanel } from './CcminiPendingEditors.js'
import {
  getAssistantToolUseBlock,
  getProgressPayload,
  getUserToolResultBlock,
  type ToolUseLookupEntry,
} from '../ccmini/transcriptState.js'
import {
  buildToolResultPresentation,
  formatToolUseTitle,
  getToolAccentColor,
  getToolUseBodyLines,
} from '../ccmini/toolRenderUtils.js'
import type { ThemeSetting } from '../ccmini/themeTypes.js'
import type { Message as MessageType } from '../types/message.js'
import type { CcminiPendingToolCall, CcminiPendingToolRequest } from '../ccmini/bridgeTypes.js'
import type { RenderedTranscriptMessage } from '../hooks/useCcminiTranscriptViewModel.js'

export function CcminiTranscriptContent({
  visibleMessages,
  toolUseLookup,
  conversationWidth,
  activeThemeSetting,
  showFullThinking,
  pendingToolRequest,
  firstPendingToolCall,
  pendingCallCount,
  showAskUserQuestionEditor,
  isLoading,
  spinnerVerb,
}: {
  visibleMessages: RenderedTranscriptMessage[]
  toolUseLookup: Map<string, ToolUseLookupEntry>
  conversationWidth: number
  activeThemeSetting: ThemeSetting
  showFullThinking: boolean
  pendingToolRequest: CcminiPendingToolRequest | null
  firstPendingToolCall: CcminiPendingToolCall | undefined
  pendingCallCount: number
  showAskUserQuestionEditor: boolean
  isLoading: boolean
  spinnerVerb: string
}): React.ReactNode {
  return (
    <React.Fragment>
      {visibleMessages.map((entry, index) => (
        <Box
          key={entry.key}
          flexDirection="column"
          marginTop={index === 0 ? 0 : 1}
        >
          {entry.message.type === 'user' ? (
            (() => {
              const toolResult = getUserToolResultBlock(entry.message)
              if (toolResult) {
                const toolUse = toolResult.tool_use_id
                  ? toolUseLookup.get(toolResult.tool_use_id)
                  : undefined
                return (
                  <ToolResultFlow
                    rawResult={entry.message.toolUseResult ?? toolResult.content}
                    toolName={toolUse?.name}
                    toolInput={toolUse?.input}
                    isError={Boolean(toolResult.is_error)}
                    width={conversationWidth}
                    buildToolResultPresentation={buildToolResultPresentation}
                    getToolAccentColor={getToolAccentColor}
                  />
                )
              }

              return (
                <UserPromptFlow
                  content={entry.lines.join('\n').trimEnd()}
                  addMargin={false}
                  themeSetting={activeThemeSetting}
                  width={conversationWidth}
                />
              )
            })()
          ) : entry.message.type === 'thinking' ? (
            <ThinkingFlow
              thinking={String(
                (
                  entry.message as MessageType & {
                    thinking?: string
                  }
                ).thinking ?? '',
              )}
              isRedacted={Boolean(
                (
                  entry.message as MessageType & {
                    isRedacted?: boolean
                  }
                ).isRedacted,
              )}
              verbose={showFullThinking}
              themeSetting={activeThemeSetting}
            />
          ) : entry.message.type === 'assistant' ? (
            (() => {
              const toolUse = getAssistantToolUseBlock(entry.message)
              if (toolUse) {
                return (
                  <ToolUseFlow
                    toolName={toolUse.name ?? 'unknown'}
                    toolInput={toolUse.input}
                    width={conversationWidth}
                    formatToolUseTitle={formatToolUseTitle}
                    getToolAccentColor={getToolAccentColor}
                    getToolUseBodyLines={getToolUseBodyLines}
                  />
                )
              }

              return (
                <AssistantFlow
                  lines={entry.lines}
                  width={conversationWidth}
                  themeSetting={activeThemeSetting}
                />
              )
            })()
          ) : entry.message.type === 'progress' ? (
            <ToolProgressFlow
              content={entry.lines.join('\n').trimEnd()}
              toolName={getProgressPayload(entry.message)?.toolName}
              width={conversationWidth}
              getToolAccentColor={getToolAccentColor}
            />
          ) : entry.message.type === 'system' ? (
            entry.message.level === 'info' ? (
              <SystemFlow
                content={entry.lines.join('\n').trimEnd()}
                addMargin={false}
                dot
                dimColor
                width={conversationWidth}
              />
            ) : (
              <SystemFlow
                content={entry.lines.join('\n').trimEnd()}
                addMargin={false}
                dot
                color={entry.message.level === 'error' ? 'red' : 'yellow'}
                dimColor={false}
                width={conversationWidth}
              />
            )
          ) : (
            <SystemFlow
              content={entry.lines.join('\n').trimEnd()}
              addMargin={false}
              dot
              dimColor
              width={conversationWidth}
            />
          )}
        </Box>
      ))}

      {pendingToolRequest &&
      firstPendingToolCall &&
      !showAskUserQuestionEditor ? (
        <CcminiPendingToolRequestPanel
          runId={pendingToolRequest.runId}
          toolName={firstPendingToolCall.toolName}
          description={firstPendingToolCall.description}
          callCount={pendingCallCount}
          themeSetting={activeThemeSetting}
        />
      ) : null}
      {isLoading && !pendingToolRequest ? (
        <WorkingStatusFlow
          verb={spinnerVerb}
          themeSetting={activeThemeSetting}
        />
      ) : null}
    </React.Fragment>
  )
}
