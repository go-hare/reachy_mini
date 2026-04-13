import {
  type CcminiControlResponse,
  type CcminiBridgeEventRecord,
  type CcminiBridgeMessage,
  type CcminiConnectConfig,
  type CcminiRemoteContent,
  type CcminiSendResult,
  type CcminiToolResultInput,
  createCcminiRequestId,
  decodeCcminiBridgeMessage,
  encodeCcminiBridgeMessage,
} from './bridgeTypes.js'

export type CcminiSessionCallbacks = {
  onConnected?: () => void
  onDisconnected?: () => void
  onError?: (error: Error) => void
  onEvent?: (event: CcminiBridgeEventRecord) => void
}

function normalizeRemoteContent(content: CcminiRemoteContent): string {
  if (typeof content === 'string') {
    return content
  }
  if (!Array.isArray(content)) {
    return ''
  }
  return content
    .map(block => {
      if (
        typeof block === 'object' &&
        block !== null &&
        block.type === 'text' &&
        typeof block.text === 'string'
      ) {
        return block.text
      }
      return ''
    })
    .filter(Boolean)
    .join('\n\n')
}

function getBridgeAckText(message: CcminiBridgeMessage): string {
  if (message.type !== 'response') {
    return ''
  }
  return String(message.payload?.text ?? '').trim()
}

function isAcceptedBridgeResponse(message: CcminiBridgeMessage): boolean {
  return getBridgeAckText(message).toLowerCase() === 'accepted'
}

function describeBridgeFailure(message: CcminiBridgeMessage): string {
  if (message.type === 'error') {
    const errorText = String(
      message.payload?.error ?? message.payload?.text ?? '',
    ).trim()
    return errorText || 'ccmini bridge returned an error.'
  }

  const responseText = getBridgeAckText(message)
  if (responseText) {
    return `ccmini bridge replied: ${responseText}`
  }

  return `Unexpected ccmini bridge response type: ${message.type}`
}

function interpretBridgeSendResult(
  message: CcminiBridgeMessage,
): CcminiSendResult {
  const responseText = getBridgeAckText(message).toLowerCase()
  if (responseText === 'accepted') {
    return {
      ok: true,
      status: 'accepted',
    }
  }
  if (responseText === 'busy') {
    return {
      ok: false,
      status: 'busy',
      message: 'ccmini bridge is busy; queued the message for automatic retry.',
    }
  }

  return {
    ok: false,
    status: 'error',
    message: describeBridgeFailure(message),
  }
}

export class CcminiSessionManager {
  private ws: WebSocket | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null
  private lastSequenceNum = 0
  private connected = false
  private readonly pending = new Map<
    string,
    { resolve: (msg: CcminiBridgeMessage) => void; reject: (err: Error) => void }
  >()

  constructor(
    private readonly config: CcminiConnectConfig,
    private readonly callbacks: CcminiSessionCallbacks,
  ) {}

  async connect(): Promise<void> {
    if ((this.config.mode ?? 'ws') === 'polling') {
      this.connected = true
      this.callbacks.onConnected?.()
      this.startPolling()
      return
    }

    await new Promise<void>((resolve, reject) => {
      const wsUrl = this.toWebSocketUrl(
        this.config.websocketUrl ?? this.config.baseUrl,
      )
      const ws = new WebSocket(wsUrl)
      this.ws = ws

      const cleanup = () => {
        ws.removeEventListener('open', onOpen)
        ws.removeEventListener('message', onMessage)
        ws.removeEventListener('close', onClose)
        ws.removeEventListener('error', onError)
      }

      const onOpen = () => {
        ws.send(
          JSON.stringify({
            auth_token: this.config.authToken,
            session_id: this.config.sessionId,
          }),
        )
      }

      const onMessage = (event: MessageEvent) => {
        const raw =
          typeof event.data === 'string' ? event.data : String(event.data)
        try {
          const parsed = JSON.parse(raw) as {
            status?: string
            session_id?: string
          }
          if (parsed.status === 'authenticated') {
            cleanup()
            this.connected = true
            this.attachWebSocketListeners(ws)
            this.callbacks.onConnected?.()
            resolve()
            return
          }
        } catch {
          // Fall through to the regular message loop once authenticated.
        }
      }

      const onClose = () => {
        cleanup()
        reject(new Error('ccmini websocket closed before authentication'))
      }

      const onError = () => {
        cleanup()
        reject(new Error('ccmini websocket connection error'))
      }

      ws.addEventListener('open', onOpen)
      ws.addEventListener('message', onMessage)
      ws.addEventListener('close', onClose)
      ws.addEventListener('error', onError)
    })
  }

  async sendMessage(
    content: CcminiRemoteContent,
    _opts?: { uuid?: string },
  ): Promise<CcminiSendResult> {
    const text = normalizeRemoteContent(content)
    const response = await this.sendBridgeMessage({
      type: 'query',
      payload: { text },
      session_id: this.config.sessionId,
      request_id: createCcminiRequestId(),
    })
    return interpretBridgeSendResult(response)
  }

  async submitToolResults(
    runId: string,
    results: CcminiToolResultInput[],
  ): Promise<boolean> {
    const response = await this.sendBridgeMessage({
      type: 'submit_tool_results',
      payload: {
        run_id: runId,
        results,
      },
      session_id: this.config.sessionId,
      request_id: createCcminiRequestId(),
    })
    return isAcceptedBridgeResponse(response)
  }

  async submitControlResponse(
    requestId: string,
    controlResponse: CcminiControlResponse,
  ): Promise<boolean> {
    const message = await this.sendBridgeMessage({
      type: 'control_response',
      payload: {
        id: requestId,
        decision: controlResponse.decision,
        allow: controlResponse.decision === 'allow',
        ...(controlResponse.scope ? { scope: controlResponse.scope } : {}),
        ...(controlResponse.scopePath
          ? { scope_path: controlResponse.scopePath }
          : {}),
      },
      session_id: this.config.sessionId,
      request_id: createCcminiRequestId(),
    })
    return isAcceptedBridgeResponse(message)
  }

  sendInterrupt(): void {
    // ccmini bridge currently does not expose a cancel control channel.
  }

  respondToPermissionRequest(): void {
    // Placeholder for future bridge-side permission support.
  }

  isConnected(): boolean {
    return this.connected
  }

  disconnect(): void {
    const wasConnected = this.connected
    this.connected = false
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    for (const pending of this.pending.values()) {
      pending.reject(new Error('ccmini session disconnected'))
    }
    this.pending.clear()
    if (wasConnected) {
      this.callbacks.onDisconnected?.()
    }
  }

  private attachWebSocketListeners(ws: WebSocket): void {
    ws.addEventListener('message', event => {
      const raw =
        typeof event.data === 'string' ? event.data : String(event.data)
      const message = decodeCcminiBridgeMessage(raw)
      this.handleBridgeMessage(message)
    })

    ws.addEventListener('close', () => {
      const wasConnected = this.connected
      this.connected = false
      if (wasConnected) {
        this.callbacks.onDisconnected?.()
      }
    })

    ws.addEventListener('error', () => {
      this.callbacks.onError?.(new Error('ccmini websocket connection error'))
    })
  }

  private async sendBridgeMessage(
    message: CcminiBridgeMessage,
  ): Promise<CcminiBridgeMessage> {
    if ((this.config.mode ?? 'ws') === 'polling') {
      return this.sendHttpBridgeMessage(message)
    }

    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('ccmini websocket is not connected')
    }

    return await new Promise<CcminiBridgeMessage>((resolve, reject) => {
      const requestId = message.request_id ?? createCcminiRequestId()
      this.pending.set(requestId, { resolve, reject })
      ws.send(
        encodeCcminiBridgeMessage({
          ...message,
          request_id: requestId,
        }),
      )
    })
  }

  private async sendHttpBridgeMessage(
    message: CcminiBridgeMessage,
  ): Promise<CcminiBridgeMessage> {
    const response = await fetch(
      `${this.config.baseUrl.replace(/\/$/, '')}/bridge/message`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.config.authToken}`,
          'content-type': 'application/json',
        },
        body: encodeCcminiBridgeMessage(message),
      },
    )
    const payload = await response.json() as
      | CcminiBridgeMessage
      | { error?: unknown }

    if (!response.ok) {
      const detail =
        typeof payload === 'object' &&
        payload !== null &&
        'error' in payload &&
        typeof payload.error === 'string'
          ? payload.error
          : `${response.status} ${response.statusText}`.trim()
      throw new Error(`ccmini bridge request failed: ${detail}`)
    }

    return payload as CcminiBridgeMessage
  }

  private startPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
    }
    const interval = this.config.pollIntervalMs ?? 500
    this.pollTimer = setInterval(() => {
      void this.fetchEvents().catch(error => {
        this.callbacks.onError?.(
          error instanceof Error ? error : new Error(String(error)),
        )
      })
    }, interval)
  }

  private async fetchEvents(): Promise<void> {
    const response = await this.sendBridgeMessage({
      type: 'events',
      payload: { since: this.lastSequenceNum, limit: 100 },
      session_id: this.config.sessionId,
      request_id: createCcminiRequestId(),
    })

    // In WebSocket mode, the response has already been processed by
    // attachWebSocketListeners() -> handleBridgeMessage().
    if ((this.config.mode ?? 'ws') !== 'polling') {
      return
    }

    const events = Array.isArray(response.payload?.events)
      ? (response.payload.events as CcminiBridgeEventRecord[])
      : []
    for (const event of events) {
      const seq = Number(event.sequence_num ?? 0)
      if (seq > this.lastSequenceNum) {
        this.lastSequenceNum = seq
      }
      this.callbacks.onEvent?.(event)
    }
  }

  private handleBridgeMessage(message: CcminiBridgeMessage): void {
    if (message.type === 'heartbeat') {
      return
    }
    if (message.type === 'events') {
      const events = Array.isArray(message.payload?.events)
        ? (message.payload.events as CcminiBridgeEventRecord[])
        : []
      for (const event of events) {
        const seq = Number(event.sequence_num ?? 0)
        if (seq > this.lastSequenceNum) {
          this.lastSequenceNum = seq
        }
        this.callbacks.onEvent?.(event)
      }
    }
    const requestId = message.request_id ?? ''
    if (requestId && this.pending.has(requestId)) {
      const pending = this.pending.get(requestId)!
      this.pending.delete(requestId)
      pending.resolve(message)
    }
  }

  private toWebSocketUrl(url: string): string {
    if (url.startsWith('ws://') || url.startsWith('wss://')) {
      return url
    }
    if (url.startsWith('http://')) {
      return `ws://${url.slice('http://'.length)}`
    }
    if (url.startsWith('https://')) {
      return `wss://${url.slice('https://'.length)}`
    }
    return `ws://${url}`
  }
}
