import { useEffect, useState } from 'react'
import { getThemeTokens } from './themePalette.js'
import type { ThemeSetting } from './themeTypes.js'
import type { Message as MessageType } from '../types/message.js'
import { getMessageLines } from './transcriptState.js'

function trimMessageLines(lines: string[], maxLines = 4): string[] {
  const compact = lines
    .flatMap(line => line.split('\n'))
    .map(line => line.trimEnd())
    .filter(line => line.length > 0)

  if (compact.length <= maxLines) {
    return compact
  }

  return [...compact.slice(0, maxLines - 1), '...']
}

function summarizeInbox(
  inbox: Record<string, Array<Record<string, unknown>>>,
): string[] {
  const lines: string[] = []

  const pushRows = inbox.push_notifications
  if (pushRows?.length) {
    const last = pushRows[pushRows.length - 1]!
    const title = String(last.title ?? '').trim()
    const body = String(last.body ?? '').trim().slice(0, 72)
    lines.push(`push: ${title}${body ? ` - ${body}` : ''}`)
  }

  const fileRows = inbox.file_deliveries
  if (fileRows?.length) {
    const last = fileRows[fileRows.length - 1]!
    const path = String(last.source_path ?? '')
    const base = path.split(/[/\\]/).pop() ?? path
    lines.push(`file: ${base}`)
  }

  const prRows = inbox.subscribe_pr
  if (prRows?.length) {
    const last = prRows[prRows.length - 1]!
    lines.push(`pr: ${String(last.repository ?? '')}`)
  }

  return lines.slice(-4)
}

export function formatConnectionTarget(baseUrl: string): string {
  try {
    const url = new URL(baseUrl)
    const path = url.pathname === '/' ? '' : url.pathname
    return `${url.host}${path}`
  } catch {
    return baseUrl.replace(/^https?:\/\//, '')
  }
}

export function getConnectionStatusHeadline(
  status: 'connecting' | 'connected' | 'disconnected',
): string {
  switch (status) {
    case 'connected':
      return 'Connected'
    case 'disconnected':
      return 'Disconnected'
    default:
      return 'Connecting'
  }
}

export function getConnectionStatusDetail(
  status: 'connecting' | 'connected' | 'disconnected',
  baseUrl: string,
): string {
  const target = formatConnectionTarget(baseUrl)
  switch (status) {
    case 'connected':
      return `Bridge ready at ${target}`
    case 'disconnected':
      return `Waiting to reconnect to ${target}`
    default:
      return `Checking connectivity to ${target}`
  }
}

export function getConnectionStatusColor(
  status: 'connecting' | 'connected' | 'disconnected',
  themeSetting: ThemeSetting,
): string {
  const theme = getThemeTokens(themeSetting)
  switch (status) {
    case 'connected':
      return theme.permission
    case 'disconnected':
      return theme.error
    default:
      return theme.warning
  }
}

export function getRecentActivityPreview(
  messages: MessageType[],
  inboxLines: string[],
): string[] {
  if (inboxLines.length > 0) {
    return inboxLines.slice(-3)
  }

  const derived: string[] = []
  for (const message of messages) {
    const firstLine = trimMessageLines(getMessageLines(message), 1)[0]?.trim()
    if (!firstLine) {
      continue
    }
    if (
      message.type === 'system' &&
      firstLine.startsWith('ccmini transport connected:')
    ) {
      continue
    }

    if (message.type === 'user') {
      derived.push(`prompt: ${firstLine}`)
      continue
    }
    if (message.type === 'assistant') {
      derived.push(`reply: ${firstLine}`)
      continue
    }
    if (message.type === 'thinking') {
      derived.push('assistant is thinking')
      continue
    }
    derived.push(firstLine)
  }

  return derived.slice(-3)
}

export function useCcminiInboxSummary(
  baseUrl: string,
  authToken: string,
): {
  lines: string[]
  error: string | null
} {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const root = baseUrl.replace(/\/$/, '')

    const poll = async (): Promise<void> => {
      try {
        const response = await fetch(
          `${root}/api/kairos/inbox?limit=12&stream=all`,
          {
            headers: { Authorization: `Bearer ${authToken}` },
          },
        )
        if (!response.ok) {
          if (!cancelled) {
            setError(`HTTP ${response.status}`)
          }
          return
        }

        const payload = (await response.json()) as {
          inbox?: Record<string, Array<Record<string, unknown>>>
        }
        if (cancelled) {
          return
        }

        setError(null)
        setLines(summarizeInbox(payload.inbox ?? {}))
      } catch (fetchError) {
        if (!cancelled) {
          setError(
            fetchError instanceof Error
              ? fetchError.message
              : 'inbox fetch failed',
          )
        }
      }
    }

    void poll()
    const intervalId = setInterval(() => {
      void poll()
    }, 8000)

    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [authToken, baseUrl])

  return {
    lines,
    error,
  }
}
