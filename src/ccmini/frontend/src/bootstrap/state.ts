let lastInteractionTime = Date.now()
let lastScrollActivityTime = 0

export function updateLastInteractionTime(_fromUser = false): void {
  lastInteractionTime = Date.now()
}

export function flushInteractionTime(): void {
  lastInteractionTime = Date.now()
}

export function markScrollActivity(): void {
  lastScrollActivityTime = Date.now()
}

export function getLastInteractionTime(): number {
  return lastInteractionTime
}

export function getLastScrollActivityTime(): number {
  return lastScrollActivityTime
}
