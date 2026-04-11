import { describe, expect, test } from 'bun:test'
import {
  getPendingToolRequestFromPayload,
  parsePromptSuggestionState,
  parseSpeculationState,
  removePendingToolCallById,
  shouldClearPendingToolRequest,
  shouldStopLoadingForEvent,
} from '../src/ccmini/sessionEventState.js'

describe('parsePromptSuggestionState', () => {
  test('maps payload fields into prompt suggestion state', () => {
    expect(
      parsePromptSuggestionState({
        text: '继续拆分',
        shown_at: 12,
        accepted_at: 34,
      }),
    ).toEqual({
      text: '继续拆分',
      shownAt: 12,
      acceptedAt: 34,
    })
  })
})

describe('parseSpeculationState', () => {
  test('normalizes nested boundary fields', () => {
    expect(
      parseSpeculationState({
        status: 'blocked',
        suggestion: '继续',
        reply: '我来继续',
        started_at: 10,
        completed_at: 20,
        error: '',
        boundary: {
          type: 'tool',
          tool_name: 'Read',
          detail: 'waiting',
          file_path: 'src/foo.ts',
          completed_at: 18,
        },
      }),
    ).toEqual({
      status: 'blocked',
      suggestion: '继续',
      reply: '我来继续',
      startedAt: 10,
      completedAt: 20,
      error: '',
      boundary: {
        type: 'tool',
        toolName: 'Read',
        detail: 'waiting',
        filePath: 'src/foo.ts',
        completedAt: 18,
      },
    })
  })
})

describe('pending tool request helpers', () => {
  test('builds pending request from payload and filters invalid calls', () => {
    expect(
      getPendingToolRequestFromPayload({
        run_id: 'run-1',
        calls: [
          {
            tool_use_id: 'call-1',
            tool_name: 'Read',
            tool_input: { file_path: 'a.ts' },
          },
          {
            tool_name: 'MissingId',
          },
        ],
      }),
    ).toEqual({
      runId: 'run-1',
      calls: [
        {
          toolName: 'Read',
          toolUseId: 'call-1',
          description: 'Provide a client-side result for Read',
          toolInput: { file_path: 'a.ts' },
        },
      ],
    })
  })

  test('removes matched tool call and clears when exhausted', () => {
    const initial = {
      runId: 'run-1',
      calls: [
        {
          toolName: 'Read',
          toolUseId: 'call-1',
          description: 'Provide a client-side result for Read',
        },
        {
          toolName: 'Glob',
          toolUseId: 'call-2',
          description: 'Provide a client-side result for Glob',
        },
      ],
    }

    expect(removePendingToolCallById(initial, 'call-1')).toEqual({
      runId: 'run-1',
      calls: [
        {
          toolName: 'Glob',
          toolUseId: 'call-2',
          description: 'Provide a client-side result for Glob',
        },
      ],
    })
    expect(removePendingToolCallById(initial, 'missing')).toBe(initial)
    expect(
      removePendingToolCallById(
        {
          runId: 'run-1',
          calls: [
            {
              toolName: 'Read',
              toolUseId: 'call-1',
              description: 'Provide a client-side result for Read',
            },
          ],
        },
        'call-1',
      ),
    ).toBeNull()
  })
})

describe('event cleanup helpers', () => {
  test('flags final and pending-tool events correctly', () => {
    expect(shouldClearPendingToolRequest('completion')).toBe(true)
    expect(shouldClearPendingToolRequest('executor_error')).toBe(true)
    expect(shouldClearPendingToolRequest('tool_result')).toBe(false)
    expect(shouldStopLoadingForEvent('pending_tool_call')).toBe(true)
    expect(shouldStopLoadingForEvent('completion')).toBe(true)
    expect(shouldStopLoadingForEvent('speculation')).toBe(false)
  })
})
