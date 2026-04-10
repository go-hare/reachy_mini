import React from 'react'
import { render, type Instance } from './ink.js'
import { CCMINI_CLI_HELP } from './ccmini/ccminiCommands.js'
import { createCcminiSession } from './ccmini/createCcminiSession.js'
import { loadConfiguredCcminiConnection } from './ccmini/loadCcminiConfig.js'
import { AlternateScreen } from './ink/components/AlternateScreen.js'
import {
  type EmbeddedCcminiHost,
  startEmbeddedCcminiHost,
} from './ccmini/startEmbeddedCcminiHost.js'
import { CcminiRepl } from './screens/CcminiRepl.js'
import { isFullscreenEnvEnabled } from './utils/fullscreen.js'

type ParsedArgs = {
  serverUrl?: string
  authToken?: string
  localBackend?: boolean
}

function parseArgs(argv: string[]): ParsedArgs {
  const args = [...argv]
  if (args[0] === 'ccmini') {
    args.shift()
  }

  const parsed: ParsedArgs = {}

  for (let index = 0; index < args.length; index += 1) {
    const value = args[index]!

    if (value === '--auth-token') {
      const token = args[index + 1]
      if (!token) {
        throw new Error('Missing value for --auth-token')
      }
      parsed.authToken = token
      index += 1
      continue
    }

    if (value.startsWith('--auth-token=')) {
      parsed.authToken = value.slice('--auth-token='.length)
      continue
    }

    if (value === '--local-backend') {
      parsed.localBackend = true
      continue
    }

    if (value.startsWith('-')) {
      throw new Error(`Unknown option: ${value}`)
    }

    if (!parsed.serverUrl) {
      parsed.serverUrl = value
      continue
    }

    throw new Error(`Unexpected argument: ${value}`)
  }

  return parsed
}

function exitWithUsage(message: string): never {
  process.stderr.write(`${message}\n\n${CCMINI_CLI_HELP}\n`)
  process.exit(1)
}

async function main(): Promise<void> {
  const parsed = parseArgs(process.argv.slice(2))
  const configured = loadConfiguredCcminiConnection()
  let embeddedHost: EmbeddedCcminiHost | null = null
  try {
    let serverUrl = parsed.serverUrl ?? configured.serverUrl
    let authToken = parsed.authToken ?? configured.authToken

    if (parsed.localBackend) {
      embeddedHost = await startEmbeddedCcminiHost()
      serverUrl = embeddedHost.serverUrl
      authToken = embeddedHost.authToken
    }

    if (!serverUrl) {
      exitWithUsage('Missing server URL. Pass [server-url] or configure ccmini_host/ccmini_port.')
    }
    if (!authToken) {
      exitWithUsage('Missing auth token. Pass --auth-token or configure ccmini_auth_token.')
    }

    const ccminiConnectConfig = await createCcminiSession({
      serverUrl,
      authToken,
    })

    let app: Instance | null = null
    let exiting = false
    const handleExit = async (): Promise<void> => {
      if (exiting) {
        return
      }
      exiting = true
      embeddedHost?.kill()
      app?.unmount()
      await embeddedHost?.stop()
      process.exit(0)
    }

    process.once('SIGINT', () => {
      void handleExit()
    })
    process.once('SIGTERM', () => {
      void handleExit()
    })
    process.once('exit', () => {
      embeddedHost?.kill()
    })

    const repl = (
      <CcminiRepl
        ccminiConnectConfig={ccminiConnectConfig}
        initialThemeSetting={configured.themeSetting}
        onExit={handleExit}
      />
    )

    app = await render(
      isFullscreenEnvEnabled()
        ? (
            <AlternateScreen mouseTracking={false}>
              {repl}
            </AlternateScreen>
          )
        : repl,
    )
  } catch (error) {
    embeddedHost?.kill()
    if (embeddedHost) {
      await embeddedHost.stop().catch(() => {})
    }
    throw error
  }
}

void main().catch(error => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`${message}\n`)
  process.exit(1)
})
