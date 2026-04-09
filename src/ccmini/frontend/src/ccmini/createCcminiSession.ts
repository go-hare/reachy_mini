import type { CcminiConnectConfig } from './bridgeTypes.js'

export async function createCcminiSession({
  serverUrl,
  authToken,
}: {
  serverUrl: string
  authToken: string
}): Promise<CcminiConnectConfig> {
  const response = await fetch(`${serverUrl.replace(/\/$/, '')}/bridge/sessions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${authToken}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ source: 'ccmini-frontend' }),
  })

  if (!response.ok) {
    throw new Error(
      `Failed to create ccmini session: ${response.status} ${response.statusText}`,
    )
  }

  const parsed = await response.json() as {
    session_id?: unknown
    base_url?: unknown
    websocket_url?: unknown
  }
  if (typeof parsed.session_id !== 'string' || parsed.session_id.length === 0) {
    throw new Error('Failed to create ccmini session: response missing session_id')
  }
  if (
    parsed.base_url !== undefined &&
    typeof parsed.base_url !== 'string'
  ) {
    throw new Error('Failed to create ccmini session: invalid base_url')
  }
  if (
    parsed.websocket_url !== undefined &&
    typeof parsed.websocket_url !== 'string'
  ) {
    throw new Error('Failed to create ccmini session: invalid websocket_url')
  }

  const baseUrl =
    typeof parsed.base_url === 'string'
      ? parsed.base_url
      : serverUrl.replace(/\/$/, '')
  const websocketUrl =
    typeof parsed.websocket_url === 'string' && parsed.websocket_url.length > 0
      ? parsed.websocket_url
      : undefined

  return {
    baseUrl,
    websocketUrl,
    authToken,
    sessionId: parsed.session_id,
    // The frontend favors explicit event polling for HTTP bridge URLs because
    // it is resilient to transient WebSocket drops and keeps completion tails
    // from disappearing mid-turn.
    mode: 'polling',
  }
}
