import { describe, expect, test } from 'bun:test'
import { applyCcminiBridgeEvent } from '../src/ccmini/ccminiMessageAdapter.js'

describe('applyCcminiBridgeEvent completion handling', () => {
  test('keeps transcript empty when completion arrives with empty text and no stream text', () => {
    const messages = applyCcminiBridgeEvent(
      {
        sequence_num: 1,
        type: 'stream_event',
        payload: {
          event_type: 'completion',
          text: '',
        },
      },
      [],
    )

    expect(messages).toHaveLength(0)
  })

  test('preserves streamed assistant text when completion payload is empty', () => {
    const afterText = applyCcminiBridgeEvent(
      {
        sequence_num: 1,
        type: 'stream_event',
        payload: {
          event_type: 'text',
          text: '你好',
        },
      },
      [],
    )

    const messages = applyCcminiBridgeEvent(
      {
        sequence_num: 2,
        type: 'stream_event',
        payload: {
          event_type: 'completion',
          text: '',
        },
      },
      afterText,
    )

    expect(messages).toHaveLength(1)
    expect(messages[0]?.type).toBe('assistant')
    expect(messages[0]?.isVirtual).toBe(false)
    expect(messages[0]?.message?.content).toBe('你好')
  })

  test('preserves tool activity when completion arrives without assistant text', () => {
    const afterToolCall = applyCcminiBridgeEvent(
      {
        sequence_num: 1,
        type: 'stream_event',
        payload: {
          event_type: 'tool_call',
          tool_use_id: 'tool-1',
          tool_name: 'shell_command',
          tool_input: {
            command: 'echo hello',
          },
        },
      },
      [],
    )

    const afterToolResult = applyCcminiBridgeEvent(
      {
        sequence_num: 2,
        type: 'stream_event',
        payload: {
          event_type: 'tool_result',
          tool_use_id: 'tool-1',
          result: 'hello',
          is_error: false,
          metadata: {
            output: 'hello',
          },
        },
      },
      afterToolCall,
    )

    const messages = applyCcminiBridgeEvent(
      {
        sequence_num: 3,
        type: 'stream_event',
        payload: {
          event_type: 'completion',
          text: '',
        },
      },
      afterToolResult,
    )

    expect(messages).toHaveLength(2)
    expect(messages[0]?.type).toBe('assistant')
    expect(messages[1]?.type).toBe('user')
  })
})
