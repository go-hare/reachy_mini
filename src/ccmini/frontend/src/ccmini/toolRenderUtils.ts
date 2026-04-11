import { stringWidth } from '../ink/stringWidth.js'

export type ToolRenderLine = {
  text: string
  color?: string
  dimColor?: boolean
}

export type ToolResultPresentation = {
  header?: ToolRenderLine
  bodyLines: ToolRenderLine[]
}

export function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

function extractTaggedContent(text: string, tag: string): string | null {
  const openTag = `<${tag}>`
  const closeTag = `</${tag}>`
  const startIndex = text.indexOf(openTag)
  const endIndex = text.indexOf(closeTag)
  if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
    return null
  }
  return text.slice(startIndex + openTag.length, endIndex).trim()
}

export function unwrapPersistedOutput(text: string): string {
  return extractTaggedContent(text, 'persisted-output') ?? text
}

function clipPreviewLine(line: string, maxLength = 140): string {
  if (stringWidth(line) <= maxLength) {
    return line
  }
  return `${line.slice(0, Math.max(0, maxLength - 3))}...`
}

function normalizePreviewLines(
  value: string,
  keepEmpty = false,
): string[] {
  const rawLines = value
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map(line => line.replace(/\r/g, ''))

  let start = 0
  while (start < rawLines.length && !rawLines[start]?.trim()) {
    start += 1
  }

  let end = rawLines.length
  while (end > start && !rawLines[end - 1]?.trim()) {
    end -= 1
  }

  const trimmed = rawLines.slice(start, end).map(line => clipPreviewLine(line))
  return keepEmpty
    ? trimmed
    : trimmed.filter(line => line.trim().length > 0)
}

function truncatePreviewLines(lines: string[], maxLines: number): string[] {
  if (lines.length <= maxLines) {
    return lines
  }
  const remaining = lines.length - maxLines
  return [
    ...lines.slice(0, maxLines),
    `... +${remaining} more line${remaining === 1 ? '' : 's'}`,
  ]
}

export function getPreviewLines(
  value: string,
  maxLines = 8,
  keepEmpty = false,
): string[] {
  return truncatePreviewLines(
    normalizePreviewLines(value, keepEmpty),
    maxLines,
  )
}

export function getNumberedPreviewLines(
  value: string,
  maxLines = 8,
): string[] {
  const rawLines = normalizePreviewLines(value, true)
  if (rawLines.length === 0) {
    return []
  }
  const visibleLines = rawLines.slice(0, maxLines)
  const width = String(visibleLines.length).length
  const numbered = visibleLines.map((line, index) =>
    `${String(index + 1).padStart(width, ' ')} ${line || ' '}`,
  )
  if (rawLines.length > maxLines) {
    numbered.push(`... +${rawLines.length - maxLines} more lines`)
  }
  return numbered
}

export function normalizeResultLines(
  value: string,
  keepEmpty = false,
): string[] {
  const rawLines = value
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map(line => line.replace(/\r/g, ''))

  let start = 0
  while (start < rawLines.length && !rawLines[start]?.trim()) {
    start += 1
  }

  let end = rawLines.length
  while (end > start && !rawLines[end - 1]?.trim()) {
    end -= 1
  }

  const trimmed = rawLines.slice(start, end)
  return keepEmpty
    ? trimmed
    : trimmed.filter(line => line.trim().length > 0)
}

export function getNumberedResultLines(value: string): string[] {
  const rawLines = normalizeResultLines(value, true)
  if (rawLines.length === 0) {
    return []
  }
  const width = String(rawLines.length).length
  return rawLines.map((line, index) =>
    `${String(index + 1).padStart(width, ' ')} ${line || ' '}`,
  )
}

export function truncateInlineText(
  value: string,
  maxLength = 76,
): string {
  if (stringWidth(value) <= maxLength) {
    return value
  }
  return `${value.slice(0, Math.max(0, maxLength - 3))}...`
}

export function getDisplayPath(pathValue: unknown): string | null {
  if (typeof pathValue !== 'string' || !pathValue.trim()) {
    return null
  }

  const trimmed = pathValue.trim()
  const cwd = process.cwd()
  const normalizedPath = trimmed.replace(/\//g, '\\')
  const normalizedCwd = cwd.replace(/\//g, '\\')

  if (
    normalizedPath.toLowerCase().startsWith(
      `${normalizedCwd.toLowerCase()}\\`,
    )
  ) {
    return normalizedPath.slice(normalizedCwd.length + 1)
  }

  return normalizedPath
}

export function getToolBaseName(pathValue: unknown): string | null {
  if (typeof pathValue !== 'string' || !pathValue.trim()) {
    return null
  }
  const normalized = pathValue.trim()
  return normalized.split(/[/\\]/).pop() ?? normalized
}

export function getToolTextValue(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string | null {
  if (!input) {
    return null
  }

  for (const key of keys) {
    const value = input[key]
    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }

  return null
}

export function getToolAccentColor(toolName: string): string | undefined {
  switch (toolName.toLowerCase()) {
    case 'bash':
    case 'shell':
      return 'red'
    case 'write':
    case 'edit':
    case 'multiedit':
    case 'notebookedit':
      return 'green'
    case 'read':
    case 'grep':
    case 'glob':
    case 'ls':
      return 'cyan'
    case 'todowrite':
      return 'yellow'
    default:
      return undefined
  }
}

export function formatToolUseTitle(
  toolName: string,
  input: Record<string, unknown> | undefined,
): string {
  const normalized = toolName.toLowerCase()

  if (normalized === 'bash' || normalized === 'shell') {
    const command = getToolTextValue(input, ['command', 'cmd'])
    return command
      ? `${toolName}(${truncateInlineText(command)})`
      : toolName
  }

  if (
    normalized === 'write' ||
    normalized === 'read' ||
    normalized === 'edit' ||
    normalized === 'multiedit' ||
    normalized === 'notebookedit' ||
    normalized === 'ls'
  ) {
    const path = getDisplayPath(
      getToolTextValue(input, ['file_path', 'path']),
    )
    return path ? `${toolName}(${path})` : toolName
  }

  if (normalized === 'grep' || normalized === 'glob') {
    const pattern = getToolTextValue(input, ['pattern', 'query'])
    return pattern
      ? `${toolName}(${truncateInlineText(pattern, 56)})`
      : toolName
  }

  if (normalized === 'task') {
    const description =
      getToolTextValue(input, ['description', 'prompt']) ??
      getToolTextValue(input, ['task'])
    return description
      ? `${toolName}(${truncateInlineText(description, 56)})`
      : toolName
  }

  return toolName
}

export function getToolUseBodyLines(
  toolName: string,
  input: Record<string, unknown> | undefined,
): ToolRenderLine[] {
  if (toolName.toLowerCase() !== 'todowrite') {
    return []
  }

  const todos = Array.isArray(input?.todos) ? input.todos : []
  return todos.slice(0, 4).flatMap((todo, index) => {
    const record = asRecord(todo)
    if (!record) {
      return []
    }
    const content = typeof record.content === 'string' ? record.content.trim() : ''
    if (!content) {
      return []
    }
    const status = typeof record.status === 'string' ? record.status : 'pending'
    return [
      {
        text: `${index === 0 ? '⎿' : ' '} [${status}] ${content}`,
        dimColor: true,
      },
    ]
  })
}

export function summarizeToolUse(
  toolName: string,
  input: Record<string, unknown> | undefined,
): {
  title: string
  detail?: string
} {
  const normalized = toolName.toLowerCase()

  if (normalized === 'todowrite') {
    const todos = Array.isArray(input?.todos) ? input.todos.length : null
    return {
      title: 'TodoWrite',
      detail:
        todos && todos > 0
          ? `Updating todo list (${todos} items)`
          : 'Updating todo list',
    }
  }

  if (normalized === 'read') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: 'Read',
      detail: path ? `Reading ${path}` : 'Reading file',
    }
  }

  if (normalized === 'write') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: 'Write',
      detail: path ? `Writing ${path}` : 'Writing file',
    }
  }

  if (normalized === 'edit' || normalized === 'multiedit' || normalized === 'notebookedit') {
    const path = getToolBaseName(getToolTextValue(input, ['file_path', 'path']))
    return {
      title: toolName,
      detail: path ? `Editing ${path}` : 'Editing file',
    }
  }

  if (normalized === 'bash' || normalized === 'shell') {
    const command = getToolTextValue(input, ['command', 'cmd'])
    return {
      title: toolName,
      detail: command ? `Running ${command}` : 'Running shell command',
    }
  }

  if (normalized === 'grep') {
    const pattern = getToolTextValue(input, ['pattern', 'query'])
    return {
      title: 'Grep',
      detail: pattern ? `Searching for ${pattern}` : 'Searching files',
    }
  }

  if (normalized === 'glob') {
    const pattern = getToolTextValue(input, ['pattern'])
    return {
      title: 'Glob',
      detail: pattern ? `Finding ${pattern}` : 'Finding matching files',
    }
  }

  if (normalized === 'ls') {
    const path = getToolTextValue(input, ['path'])
    return {
      title: 'LS',
      detail: path ? `Listing ${path}` : 'Listing files',
    }
  }

  if (normalized === 'webfetch') {
    const url = getToolTextValue(input, ['url'])
    return {
      title: 'WebFetch',
      detail: url ? `Fetching ${url}` : 'Fetching URL',
    }
  }

  if (normalized === 'task') {
    const description =
      getToolTextValue(input, ['description', 'prompt']) ??
      getToolTextValue(input, ['task'])
    return {
      title: 'Task',
      detail: description ? `Delegating ${description}` : 'Delegating subtask',
    }
  }

  return {
    title: toolName,
    detail: undefined,
  }
}

export function summarizeToolResultText(value: unknown): string {
  const text =
    typeof value === 'string' ? value : stringifyUnknown(value)
  const normalized = text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)

  if (normalized.length === 0) {
    return 'Tool completed.'
  }

  return normalized[0]!
}

function buildBashResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawRecord = asRecord(rawResult)
  let stdout =
    typeof rawRecord?.stdout === 'string' ? rawRecord.stdout : ''
  let stderr =
    typeof rawRecord?.stderr === 'string' ? rawRecord.stderr : ''
  let exitCode =
    typeof rawRecord?.exitCode === 'number' ||
    typeof rawRecord?.exitCode === 'string'
      ? String(rawRecord.exitCode)
      : ''

  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  if (!stdout && !stderr) {
    const exitMatch = rawText.match(/(?:^|\n)Exit code:\s*(-?\d+)\s*$/)
    if (exitMatch) {
      exitCode = exitCode || exitMatch[1]!
    }

    const withoutExitCode = rawText
      .replace(/(?:^|\n)Exit code:\s*-?\d+\s*$/g, '')
      .trim()

    if (withoutExitCode.startsWith('STDERR:\n')) {
      stderr = withoutExitCode.slice('STDERR:\n'.length).trim()
    } else if (withoutExitCode.includes('\nSTDERR:\n')) {
      const [stdoutText, stderrText] = withoutExitCode.split('\nSTDERR:\n')
      stdout = stdoutText?.trim() ?? ''
      stderr = stderrText?.trim() ?? ''
    } else {
      stdout = withoutExitCode
    }
  }

  const bodyLines: ToolRenderLine[] = []
  let header: ToolRenderLine | undefined

  if (exitCode && exitCode !== '0') {
    header = {
      text: `Error: Exit code ${exitCode}`,
      color: 'red',
      dimColor: false,
    }
  }

  if (!header) {
    const stdoutLines = normalizeResultLines(stdout, true)
    if (stdoutLines.length > 0) {
      const [firstLine, ...restLines] = stdoutLines
      header = {
        text: firstLine!,
        dimColor: false,
      }
      bodyLines.push(
        ...restLines.map(text => ({
          text,
          dimColor: false,
        })),
      )
    }
  }

  const stderrLines = normalizeResultLines(stderr, true)
  if (stderrLines.length > 0) {
    bodyLines.push(
      ...stderrLines.map(text => ({
        text,
        color: 'red',
        dimColor: false,
      })),
    )
  }

  if (!header && isError) {
    const errorLines = normalizeResultLines(rawText, true)
    if (errorLines.length > 0) {
      const [firstLine, ...restLines] = errorLines
      header = {
        text: firstLine!.startsWith('Error:')
          ? firstLine!
          : `Error: ${firstLine!}`,
        color: 'red',
        dimColor: false,
      }
      bodyLines.push(
        ...restLines.map(text => ({
          text,
          color: 'red',
          dimColor: false,
        })),
      )
    }
  }

  if (!header && bodyLines.length === 0) {
    header = {
      text: '(no output)',
      dimColor: true,
    }
  }

  return {
    header,
    bodyLines,
  }
}

function buildWriteResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const record = asRecord(rawResult)
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const summaryLines = normalizeResultLines(rawText, true)
  const contentPreview =
    (typeof record?.content === 'string' ? record.content : null) ??
    getToolTextValue(toolInput, ['content'])

  const bodyLines: ToolRenderLine[] = []
  if (summaryLines.length > 1) {
    bodyLines.push(
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
    )
  }

  if (!isError && contentPreview) {
    bodyLines.push(
      ...getNumberedResultLines(contentPreview).map(text => ({
        text,
        dimColor: false,
      })),
    )
  }

  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Write failed.' : 'Write completed.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError && bodyLines.length === 0,
    },
    bodyLines,
  }
}

function buildReadResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const rawRecord = asRecord(rawResult)
  const rawFile = asRecord(rawRecord?.file)
  const rawText =
    typeof rawFile?.content === 'string'
      ? rawFile.content
      : unwrapPersistedOutput(stringifyUnknown(rawResult))
  const normalizedLines = normalizeResultLines(rawText, true).map(line =>
    line.replace(/^\s*\d+\s*\|\s?/, ''),
  )
  const lineCount =
    typeof rawFile?.numLines === 'number'
      ? rawFile.numLines
      : normalizedLines.filter(line => line.trim().length > 0).length
  const startLine =
    typeof rawFile?.startLine === 'number'
      ? rawFile.startLine
      : typeof toolInput?.offset === 'number'
        ? toolInput.offset
        : null
  const totalLines =
    typeof rawFile?.totalLines === 'number' ? rawFile.totalLines : null
  const displayPath =
    getDisplayPath(
      rawFile?.filePath ??
      getToolTextValue(toolInput, ['file_path', 'path']) ??
      '',
    ) ?? 'file'

  if (isError) {
    const firstLine =
      normalizedLines.find(line => line.trim().length > 0) ?? 'Read failed.'
    return {
      header: {
        text: firstLine.startsWith('Error:') ? firstLine : `Error: ${firstLine}`,
        color: 'red',
        dimColor: false,
      },
      bodyLines: normalizedLines.slice(1, 4).map(text => ({
        text,
        color: 'red',
        dimColor: false,
      })),
    }
  }

  const detailParts = [`${lineCount} ${lineCount === 1 ? 'line' : 'lines'}`]
  if (startLine !== null && startLine > 0) {
    detailParts.push(`from line ${startLine}`)
  }
  if (
    totalLines !== null &&
    totalLines > 0 &&
    lineCount > 0 &&
    lineCount < totalLines
  ) {
    detailParts.push(`${totalLines} total`)
  }

  return {
    header: {
      text: `Read ${displayPath}`,
      color: 'cyan',
      dimColor: false,
    },
    bodyLines: [
      {
        text: detailParts.join(' · '),
        dimColor: true,
      },
      {
        text: 'Content hidden in transcript to keep the message flow compact.',
        dimColor: true,
      },
    ],
  }
}

function buildListResultPresentation(
  rawResult: unknown,
  toolName: 'ls' | 'glob',
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const lines = normalizeResultLines(rawText, true)

  if (lines.length === 0) {
    return {
      header: {
        text: isError ? `${toolName.toUpperCase()} failed.` : 'No files returned.',
        color: isError ? 'red' : 'cyan',
        dimColor: false,
      },
      bodyLines: [],
    }
  }

  const visible = lines.slice(0, 1)
  const remaining = Math.max(0, lines.length - visible.length)

  return {
    header: {
      text:
        toolName === 'ls'
          ? `Listed ${lines.length} ${lines.length === 1 ? 'path' : 'paths'}`
          : `Matched ${lines.length} ${lines.length === 1 ? 'path' : 'paths'}`,
      color: isError ? 'red' : 'cyan',
      dimColor: false,
    },
    bodyLines: [
      ...visible.map(text => ({
        text: truncateInlineText(text, 44),
        dimColor: false,
      })),
      ...(remaining > 0
        ? [
            {
              text: `... +${remaining} more ${remaining === 1 ? 'path' : 'paths'}`,
              dimColor: true,
            },
          ]
        : []),
    ],
  }
}

function buildSearchResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const lines = normalizeResultLines(rawText, true)

  if (lines.length === 0) {
    return {
      header: {
        text: isError ? 'Search failed.' : 'No matches found.',
        color: isError ? 'red' : 'cyan',
        dimColor: false,
      },
      bodyLines: [],
    }
  }

  const visible = lines.slice(0, 2)
  const remaining = Math.max(0, lines.length - visible.length)

  return {
    header: {
      text: `Found ${lines.length} ${lines.length === 1 ? 'match' : 'matches'}`,
      color: isError ? 'red' : 'cyan',
      dimColor: false,
    },
    bodyLines: [
      ...visible.map(text => ({
        text: truncateInlineText(text, 56),
        dimColor: false,
      })),
      ...(remaining > 0
        ? [
            {
              text: `... +${remaining} more ${remaining === 1 ? 'match' : 'matches'}`,
              dimColor: true,
            },
          ]
        : []),
    ],
  }
}

function buildEditResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const diffMarker = '\nDiff:\n'
  const lintMarker = '\nLint issues:\n'
  const diffIndex = rawText.indexOf(diffMarker)
  const lintIndex = rawText.indexOf(lintMarker)
  const record = asRecord(rawResult)

  let summaryText = rawText
  let diffText = ''
  let lintText = ''

  if (diffIndex !== -1) {
    summaryText = rawText.slice(0, diffIndex).trim()
    const diffEnd = lintIndex !== -1 ? lintIndex : rawText.length
    diffText = rawText.slice(diffIndex + diffMarker.length, diffEnd).trim()
  }

  if (lintIndex !== -1) {
    lintText = rawText.slice(lintIndex + lintMarker.length).trim()
  }

  const bodyLines: ToolRenderLine[] = []

  if (diffText) {
    bodyLines.push(
      ...normalizeResultLines(diffText, true).map(text => ({
        text,
        color: text.startsWith('+')
          ? 'green'
          : text.startsWith('-')
            ? 'red'
            : undefined,
        dimColor: text.startsWith('@@'),
      })),
    )
  } else {
    const structuredPatch = Array.isArray(record?.structuredPatch)
      ? record.structuredPatch
      : []
    if (structuredPatch.length > 0) {
      for (const hunk of structuredPatch) {
        const hunkRecord = asRecord(hunk)
        const lines = Array.isArray(hunkRecord?.lines)
          ? hunkRecord.lines
          : []
        bodyLines.push(
          ...lines.flatMap(line =>
            typeof line === 'string'
              ? [
                  {
                    text: line,
                    color: line.startsWith('+')
                      ? 'green'
                      : line.startsWith('-')
                        ? 'red'
                        : undefined,
                    dimColor: line.startsWith('@@'),
                  },
                ]
              : [],
          ),
        )
      }
    } else {
      const replacementPreview =
        getToolTextValue(toolInput, ['new_string']) ??
        (typeof record?.content === 'string' ? record.content : null)
      if (replacementPreview) {
        bodyLines.push(
          ...getNumberedResultLines(replacementPreview).map(text => ({
            text,
            dimColor: false,
          })),
        )
      }
    }
  }

  if (lintText) {
    bodyLines.push(
      ...normalizeResultLines(lintText, true).map(text => ({
        text,
        color: 'yellow',
        dimColor: false,
      })),
    )
  }

  const summaryLines = normalizeResultLines(summaryText, true)
  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Edit failed.' : 'Edit completed.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError && bodyLines.length === 0,
    },
    bodyLines: [
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
      ...bodyLines,
    ],
  }
}

function buildTodoResultPresentation(
  rawResult: unknown,
  toolInput: Record<string, unknown> | undefined,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const summaryLines = normalizeResultLines(rawText, true)
  const todos = Array.isArray(toolInput?.todos) ? toolInput.todos : []

  return {
    header: {
      text:
        summaryLines[0] ??
        (isError ? 'Todo update failed.' : 'Todo list updated.'),
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    },
    bodyLines: [
      ...summaryLines.slice(1).map(text => ({
        text,
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      })),
      ...todos.flatMap(todo => {
        const record = asRecord(todo)
        if (!record) {
          return []
        }
        const content = typeof record.content === 'string' ? record.content.trim() : ''
        if (!content) {
          return []
        }
        const status = typeof record.status === 'string' ? record.status : 'pending'
        return [
          {
            text: `[${status}] ${content}`,
            dimColor: true,
          },
        ]
      }),
    ],
  }
}

function buildGenericResultPresentation(
  rawResult: unknown,
  isError: boolean,
): ToolResultPresentation {
  const rawText = unwrapPersistedOutput(stringifyUnknown(rawResult))
  const previewLines = normalizeResultLines(rawText, true)
  if (previewLines.length === 0) {
    return {
      header: {
        text: isError ? 'Tool failed.' : 'Tool completed.',
        color: isError ? 'red' : undefined,
        dimColor: !isError,
      },
      bodyLines: [],
    }
  }

  const [firstLine, ...restLines] = previewLines
  return {
    header: {
      text: firstLine!,
      color: isError ? 'red' : undefined,
      dimColor: !isError && restLines.length === 0,
    },
    bodyLines: restLines.map(text => ({
      text,
      color: isError ? 'red' : undefined,
      dimColor: !isError,
    })),
  }
}

export function buildToolResultPresentation({
  rawResult,
  toolName,
  toolInput,
  isError,
}: {
  rawResult: unknown
  toolName?: string
  toolInput?: Record<string, unknown>
  isError: boolean
}): ToolResultPresentation {
  const normalized = String(toolName ?? '').toLowerCase()

  if (normalized === 'bash' || normalized === 'shell') {
    return buildBashResultPresentation(rawResult, isError)
  }

  if (normalized === 'write') {
    return buildWriteResultPresentation(rawResult, toolInput, isError)
  }

  if (normalized === 'read') {
    return buildReadResultPresentation(rawResult, toolInput, isError)
  }

  if (normalized === 'ls' || normalized === 'glob') {
    return buildListResultPresentation(rawResult, normalized as 'ls' | 'glob', isError)
  }

  if (normalized === 'grep') {
    return buildSearchResultPresentation(rawResult, isError)
  }

  if (
    normalized === 'edit' ||
    normalized === 'multiedit' ||
    normalized === 'notebookedit'
  ) {
    return buildEditResultPresentation(rawResult, toolInput, isError)
  }

  if (normalized === 'todowrite') {
    return buildTodoResultPresentation(rawResult, toolInput, isError)
  }

  return buildGenericResultPresentation(rawResult, isError)
}
