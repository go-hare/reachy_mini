import type { Key } from '../ink.js'

export type InlineGhostText = {
  text: string
  fullCommand: string
  insertPosition: number
}

export type TextInputState = {
  onInput: (input: string, key: Key) => void
  renderedValue: string
  offset: number
  setOffset: (offset: number) => void
  cursorLine: number
  cursorColumn: number
  viewportCharOffset: number
  viewportCharEnd: number
}
