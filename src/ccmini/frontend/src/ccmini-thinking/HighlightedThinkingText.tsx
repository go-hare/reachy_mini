import * as React from 'react'
import { Fragment } from 'react'
import { Box, Text } from '../ink.js'
import {
  findThinkingTriggerPositions,
  getRainbowColor,
} from './thinkingUtils.js'

export function HighlightedThinkingText({
  text,
}: {
  text: string
}): React.ReactNode {
  const triggers = findThinkingTriggerPositions(text)

  if (triggers.length === 0) {
    return (
      <Text>
        <Text dimColor>❯ </Text>
        <Text>{text}</Text>
      </Text>
    )
  }

  const parts: React.ReactNode[] = []
  let cursor = 0
  for (const trigger of triggers) {
    if (trigger.start > cursor) {
      parts.push(
        <Text key={`plain-${cursor}`}>
          {text.slice(cursor, trigger.start)}
        </Text>,
      )
    }

    for (let index = trigger.start; index < trigger.end; index += 1) {
      parts.push(
        <Text
          key={`rainbow-${index}`}
          color={getRainbowColor(index - trigger.start)}
        >
          {text[index]}
        </Text>,
      )
    }

    cursor = trigger.end
  }

  if (cursor < text.length) {
    parts.push(
      <Text key={`plain-${cursor}`}>
        {text.slice(cursor)}
      </Text>,
    )
  }

  return (
    <Box>
      <Text dimColor>❯ </Text>
      <Text>
        {parts.map((part, index) => (
          <Fragment key={index}>{part}</Fragment>
        ))}
      </Text>
    </Box>
  )
}
