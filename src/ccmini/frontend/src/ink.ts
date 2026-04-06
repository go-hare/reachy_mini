import { useContext } from 'react'
import inkRender, {
  createRoot,
  renderSync,
  type Instance,
  type RenderOptions,
  type Root,
} from './ink/root.js'
import { Ansi } from './ink/Ansi.js'
import Box, { type Props as BoxProps } from './ink/components/Box.js'
import Button, {
  type ButtonState,
  type Props as ButtonProps,
} from './ink/components/Button.js'
import Link, { type Props as LinkProps } from './ink/components/Link.js'
import Newline, { type Props as NewlineProps } from './ink/components/Newline.js'
import { NoSelect } from './ink/components/NoSelect.js'
import { RawAnsi } from './ink/components/RawAnsi.js'
import Spacer from './ink/components/Spacer.js'
import Text, { type Props as TextProps } from './ink/components/Text.js'
import { TerminalSizeContext } from './ink/components/TerminalSizeContext.js'
import type { DOMElement } from './ink/dom.js'
import { ClickEvent } from './ink/events/click-event.js'
import { EventEmitter } from './ink/events/emitter.js'
import { Event } from './ink/events/event.js'
import { InputEvent, type Key } from './ink/events/input-event.js'
import { TerminalFocusEvent, type TerminalFocusEventType } from './ink/events/terminal-focus-event.js'
import { FocusManager } from './ink/focus.js'
import { useAnimationFrame } from './ink/hooks/use-animation-frame.js'
import useApp from './ink/hooks/use-app.js'
import useInput from './ink/hooks/use-input.js'
import { useAnimationTimer, useInterval } from './ink/hooks/use-interval.js'
import { useSelection } from './ink/hooks/use-selection.js'
import useStdin from './ink/hooks/use-stdin.js'
import { useTabStatus } from './ink/hooks/use-tab-status.js'
import { useTerminalFocus } from './ink/hooks/use-terminal-focus.js'
import { useTerminalTitle } from './ink/hooks/use-terminal-title.js'
import { useTerminalViewport } from './ink/hooks/use-terminal-viewport.js'
import measureElement from './ink/measure-element.js'
import wrapText from './ink/wrap-text.js'

export type { RenderOptions, Instance, Root, DOMElement, BoxProps, TextProps }
export type { ButtonState, ButtonProps, LinkProps, NewlineProps }
export type { Key, TerminalFocusEventType }

export async function render(
  node: Parameters<typeof inkRender>[0],
  options?: Parameters<typeof inkRender>[1],
): Promise<Instance> {
  return await inkRender(node, options)
}

export function useStdout(): { stdout: NodeJS.WriteStream } {
  const size = useContext(TerminalSizeContext)
  const stdout = process.stdout

  if (size) {
    ;(stdout as NodeJS.WriteStream & { columns?: number; rows?: number }).columns =
      size.columns
    ;(stdout as NodeJS.WriteStream & { columns?: number; rows?: number }).rows =
      size.rows
  }

  return { stdout }
}

export {
  Ansi,
  Box,
  Button,
  ClickEvent,
  createRoot,
  Event,
  EventEmitter,
  FocusManager,
  InputEvent,
  Link,
  measureElement,
  Newline,
  NoSelect,
  RawAnsi,
  renderSync,
  Spacer,
  TerminalFocusEvent,
  Text,
  useAnimationFrame,
  useAnimationTimer,
  useApp,
  useInput,
  useInterval,
  useSelection,
  useStdin,
  useTabStatus,
  useTerminalFocus,
  useTerminalTitle,
  useTerminalViewport,
  wrapText,
}
