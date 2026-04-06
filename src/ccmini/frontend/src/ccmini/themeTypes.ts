export type ThemeSetting =
  | 'auto'
  | 'dark'
  | 'light'
  | 'dark-daltonized'
  | 'light-daltonized'
  | 'dark-ansi'
  | 'light-ansi'

export const THEME_OPTIONS: Array<{
  label: string
  value: ThemeSetting
}> = [
  { label: 'Auto (match terminal)', value: 'auto' },
  { label: 'Dark mode', value: 'dark' },
  { label: 'Light mode', value: 'light' },
  { label: 'Dark mode (colorblind-friendly)', value: 'dark-daltonized' },
  { label: 'Light mode (colorblind-friendly)', value: 'light-daltonized' },
  { label: 'Dark mode (ANSI colors only)', value: 'dark-ansi' },
  { label: 'Light mode (ANSI colors only)', value: 'light-ansi' },
]

export function isThemeSetting(value: unknown): value is ThemeSetting {
  return THEME_OPTIONS.some(option => option.value === value)
}
