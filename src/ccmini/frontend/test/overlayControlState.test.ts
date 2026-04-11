import { describe, expect, test } from 'bun:test'
import {
  clampCommandCatalogIndex,
  getSelectedCatalogEntry,
  getThemePickerIndex,
} from '../src/ccmini/overlayControlState.js'

describe('getThemePickerIndex', () => {
  test('maps theme setting to valid theme picker index', () => {
    expect(getThemePickerIndex('auto')).toBe(0)
    expect(getThemePickerIndex('light')).toBe(2)
    expect(getThemePickerIndex('light-ansi')).toBe(6)
  })
})

describe('clampCommandCatalogIndex', () => {
  test('keeps selected index inside available range', () => {
    expect(clampCommandCatalogIndex(-1, 3)).toBe(0)
    expect(clampCommandCatalogIndex(1, 3)).toBe(1)
    expect(clampCommandCatalogIndex(9, 3)).toBe(2)
    expect(clampCommandCatalogIndex(4, 0)).toBe(0)
  })
})

describe('getSelectedCatalogEntry', () => {
  test('returns selected entry or null for empty lists', () => {
    expect(getSelectedCatalogEntry(['a', 'b', 'c'], 1)).toBe('b')
    expect(getSelectedCatalogEntry(['a', 'b', 'c'], 8)).toBe('c')
    expect(getSelectedCatalogEntry([], 0)).toBeNull()
  })
})
