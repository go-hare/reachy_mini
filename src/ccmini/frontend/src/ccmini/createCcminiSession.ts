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

  const baseUrl =
    typeof parsed.base_url === 'string'
      ? parsed.base_url
      : serverUrl.replace(/\/$/, '')

  return {
    baseUrl,
    authToken,
    sessionId: parsed.session_id,
    mode: 'ws',
  }
}
