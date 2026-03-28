export function smoothValue(current: number, target: number, smoothingFactor = 0.15) {
  return current + (target - current) * smoothingFactor
}
