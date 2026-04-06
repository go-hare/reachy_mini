// @ts-nocheck
export default function throttle<T extends (...args: any[]) => void>(
  fn: T,
  waitMs: number,
  options: {
    leading?: boolean
    trailing?: boolean
  } = {},
): T & { cancel: () => void } {
  let timeout: ReturnType<typeof setTimeout> | null = null
  let lastRun = 0
  let pendingArgs: Parameters<T> | null = null

  const run = (args: Parameters<T>) => {
    lastRun = Date.now()
    pendingArgs = null
    fn(...args)
  }

  const throttled = ((...args: Parameters<T>) => {
    const now = Date.now()
    const remaining = waitMs - (now - lastRun)

    if (lastRun === 0 && options.leading === false) {
      pendingArgs = args
      if (!timeout && options.trailing !== false) {
        timeout = setTimeout(() => {
          timeout = null
          if (pendingArgs) {
            run(pendingArgs)
          }
        }, waitMs)
      }
      return
    }

    if (remaining <= 0) {
      if (timeout) {
        clearTimeout(timeout)
        timeout = null
      }
      run(args)
      return
    }

    pendingArgs = args
    if (!timeout && options.trailing !== false) {
      timeout = setTimeout(() => {
        timeout = null
        if (pendingArgs) {
          run(pendingArgs)
        }
      }, remaining)
    }
  }) as T & { cancel: () => void }

  throttled.cancel = () => {
    if (timeout) {
      clearTimeout(timeout)
      timeout = null
    }
    pendingArgs = null
  }

  return throttled
}
