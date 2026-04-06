export type ModifierKey = 'shift' | 'command' | 'control' | 'option'

export function prewarmModifiers(): void {
  // no-op in ccmini frontend
}

export function isModifierPressed(_modifier: ModifierKey): boolean {
  return false
}
