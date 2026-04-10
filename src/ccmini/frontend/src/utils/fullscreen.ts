import { isEnvTruthy } from './envUtils.js'

export function isFullscreenEnvEnabled(): boolean {
  return (
    isEnvTruthy(process.env.CCMINI_FULLSCREEN) ||
    isEnvTruthy(process.env.CLAUDE_CODE_NO_FLICKER)
  )
}

export function isMouseTrackingEnabled(): boolean {
  return !isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_MOUSE)
}

export function isMouseClicksDisabled(): boolean {
  return isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_MOUSE_CLICKS)
}

export function isFullscreenActive(): boolean {
  return isFullscreenEnvEnabled()
}

export async function maybeGetTmuxMouseHint(): Promise<string | null> {
  return null
}
