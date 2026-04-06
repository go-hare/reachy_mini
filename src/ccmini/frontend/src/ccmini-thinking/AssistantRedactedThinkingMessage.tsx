import * as React from 'react'
import { Box, Text } from '../ink.js'

export function AssistantRedactedThinkingMessage({
  addMargin = false,
}: {
  addMargin?: boolean
}): React.ReactNode {
  return (
    <Box marginTop={addMargin ? 1 : 0}>
      <Text dimColor italic>
        ✻ Thinking…
      </Text>
    </Box>
  )
}
