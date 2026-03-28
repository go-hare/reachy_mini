export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

export function isZero(value: number, tolerance = 0.001) {
  return Math.abs(value) < tolerance
}
