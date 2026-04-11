import { THEME_OPTIONS, type ThemeSetting } from './themeTypes.js'

export function getThemePickerIndex(setting: ThemeSetting): number {
  return Math.max(
    0,
    THEME_OPTIONS.findIndex(option => option.value === setting),
  )
}

export function clampCommandCatalogIndex(
  previousIndex: number,
  entryCount: number,
): number {
  if (entryCount <= 0) {
    return 0
  }

  return Math.min(Math.max(0, previousIndex), entryCount - 1)
}

export function getSelectedCatalogEntry<T>(
  entries: T[],
  selectedIndex: number,
): T | null {
  if (entries.length === 0) {
    return null
  }

  return entries[clampCommandCatalogIndex(selectedIndex, entries.length)] ?? null
}
