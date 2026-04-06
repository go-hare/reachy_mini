import * as React from 'react'
import { Box, Text } from '../ink.js'
import { CtrlOToExpand } from './CtrlOToExpand.js'

type ThinkingParam = {
  type: 'thinking'
  thinking: string
}

export function AssistantThinkingMessage({
  param,
  addMargin = false,
  isTranscriptMode,
  verbose,
  hideInTranscript = false,
}: {
  param: ThinkingParam
  addMargin?: boolean
  isTranscriptMode: boolean
  verbose: boolean
  hideInTranscript?: boolean
}): React.ReactNode {
  const thinking = param.thinking
  if (hideInTranscript) {
    return null
  }

  if (!thinking) {
    return (
      <Box marginTop={addMargin ? 1 : 0}>
        <Text dimColor italic>
          ∴ Thinking…
        </Text>
      </Box>
    )
  }

  const shouldShowFullThinking = isTranscriptMode || verbose
  if (!shouldShowFullThinking) {
    return (
      <Box marginTop={addMargin ? 1 : 0}>
        <Text dimColor italic>
          ∴ Thinking <CtrlOToExpand />
        </Text>
      </Box>
    )
  }

  return (
    <Box
      flexDirection="column"
      gap={1}
      marginTop={addMargin ? 1 : 0}
      width="100%"
    >
      <Text dimColor italic>
        ∴ Thinking…
      </Text>
      <Box paddingLeft={2} flexDirection="column">
        {thinking.split('\n').map((line, index) => (
          <Text key={index} dimColor>
            {line.length > 0 ? line : ' '}
          </Text>
        ))}
      </Box>
    </Box>
  )
}
