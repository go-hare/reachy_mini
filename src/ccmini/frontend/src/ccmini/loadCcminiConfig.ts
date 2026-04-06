import { homedir } from 'os'
import { join } from 'path'
import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import { isThemeSetting, type ThemeSetting } from './themeTypes.js'

type RawCcminiConfig = {
  ccmini_host?: unknown
  ccmini_port?: unknown
  ccmini_auth_token?: unknown
  theme?: unknown
}

export type ResolvedCcminiConnection = {
  serverUrl?: string
  authToken?: string
  themeSetting?: ThemeSetting
}

function getCcminiConfigHome(): string {
  const override = process.env.CCMINI_HOME?.trim()
  if (override) {
    return override
  }
  const legacyOverride = process.env.MINI_AGENT_HOME?.trim()
  if (legacyOverride) {
    return legacyOverride
  }
  return join(homedir(), '.ccmini')
}

function readConfigCandidate(path: string): RawCcminiConfig {
  try {
    const content = readFileSync(path, 'utf8')
    const parsed = JSON.parse(content) as unknown
    if (!parsed || typeof parsed !== 'object') {
      return {}
    }
    return parsed as RawCcminiConfig
  } catch {
    return {}
  }
}

function getConfiguredPort(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10)
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed
    }
  }
  return undefined
}

export function loadConfiguredCcminiConnection(
  cwd: string = process.cwd(),
): ResolvedCcminiConnection {
  const globalConfig = {
    ...readConfigCandidate(join(homedir(), '.mini_agent', 'config.json')),
    ...readConfigCandidate(join(getCcminiConfigHome(), 'config.json')),
  }
  const projectConfig = {
    ...readConfigCandidate(join(cwd, '.mini-agent.json')),
    ...readConfigCandidate(join(cwd, '.ccmini.json')),
  }
  const merged = {
    ...globalConfig,
    ...projectConfig,
  }

  const host =
    typeof merged.ccmini_host === 'string' && merged.ccmini_host.trim()
      ? merged.ccmini_host.trim()
      : undefined
  const port = getConfiguredPort(merged.ccmini_port)
  const authToken =
    typeof merged.ccmini_auth_token === 'string' &&
    merged.ccmini_auth_token.trim()
      ? merged.ccmini_auth_token.trim()
      : undefined
  const themeSetting = isThemeSetting(merged.theme) ? merged.theme : undefined

  return {
    serverUrl:
      host && port ? `http://${host}:${port}` : undefined,
    authToken,
    themeSetting,
  }
}

export function saveConfiguredTheme(setting: ThemeSetting): void {
  const configDir = getCcminiConfigHome()
  const configPath = join(configDir, 'config.json')
  const existing = readConfigCandidate(configPath)
  mkdirSync(configDir, { recursive: true })
  writeFileSync(
    configPath,
    `${JSON.stringify({ ...existing, theme: setting }, null, 2)}\n`,
    'utf8',
  )
}
