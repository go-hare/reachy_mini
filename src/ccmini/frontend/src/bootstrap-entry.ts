#!/usr/bin/env bun
import { ensureBootstrapMacro } from './bootstrapMacro'
import { CCMINI_CLI_HELP } from './ccmini/ccminiCommands.js'

ensureBootstrapMacro()
const macro = globalThis as typeof globalThis & {
  MACRO?: {
    VERSION?: string
  }
}

const args = process.argv.slice(2)

if (
  args.length === 1 &&
  (args[0] === '--version' || args[0] === '-v' || args[0] === '-V')
) {
  console.log(`${macro.MACRO?.VERSION ?? '0.0.0'} (ccmini frontend)`)
  process.exit(0)
}

if (args.includes('--help') || args.includes('-h')) {
  process.stdout.write(`${CCMINI_CLI_HELP}\n`)
  process.exit(0)
}

const unsupportedTopLevelCommands = new Set([
  'agents',
  'executor',
  'executor-server',
  'server',
  'ssh',
  'open',
  'remote-control',
  'rc',
  'remote',
  'sync',
  'bridge',
  'auth',
  'setup-token',
  'doctor',
  'install',
  'update',
  'upgrade',
  'rollback',
  'log',
  'error',
  'completion',
])

if (args[0] && unsupportedTopLevelCommands.has(args[0])) {
  process.stderr.write(
    `Command "${args[0]}" is not exposed by ccmini frontend.\n`
      + 'Use the Python-side ccmini runtime/bridge for host features, '
      + 'and use this frontend for UI-facing session flows.\n',
  )
  process.exit(1)
}

await import('./main.tsx')
