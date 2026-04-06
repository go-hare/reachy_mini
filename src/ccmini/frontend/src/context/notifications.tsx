import * as React from 'react'

type Notification = {
  key: string
  text?: string
  priority?: 'low' | 'medium' | 'high' | 'immediate'
  timeoutMs?: number
}

export function useNotifications(): {
  addNotification: (_content: Notification) => void
  removeNotification: (_key: string) => void
} {
  return React.useMemo(
    () => ({
      addNotification: () => {},
      removeNotification: () => {},
    }),
    [],
  )
}
