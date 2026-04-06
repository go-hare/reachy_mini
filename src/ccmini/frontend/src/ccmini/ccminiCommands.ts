import type { Command } from './commandTypes.js'

export const CCMINI_CLI_HELP = [
  'ccmini frontend',
  '',
  'Usage:',
  '  ccmini-frontend [server-url] [--auth-token <token>]',
  '  ccmini-frontend ccmini [server-url] [--auth-token <token>]',
  '  ccmini-frontend --local-backend',
  '',
  'Options:',
  '  -h, --help               Show help',
  '  --auth-token <token>    Bearer token for ccmini bridge auth',
  '  --local-backend         Start a local ccmini bridge host automatically',
  '',
  'Notes:',
  '  If server-url/auth-token are omitted, ccmini frontend first tries ~/.ccmini/config.json, ~/.mini_agent/config.json, .ccmini.json, and .mini-agent.json.',
  '  Use --local-backend when you want the frontend to start a local ccmini backend itself.',
  '  Headless print mode is not exposed by ccmini frontend.',
].join('\n')

export const CCMINI_REPL_HELP = [
  'ccmini frontend commands:',
  '  /help  Show this message',
  '  /help <command>  Show extracted donor command details',
  '  / or /commands  Browse extracted donor commands',
  '  /theme Open the theme picker',
  '  /exit  Exit the frontend',
  '',
  'Launch:',
  '  ccmini-frontend [server-url] [--auth-token <token>]',
  '  ccmini-frontend ccmini [server-url] [--auth-token <token>]',
  '  ccmini-frontend --local-backend',
].join('\n')

const help: Command = {
  type: 'local',
  name: 'help',
  description: 'Show ccmini frontend help',
  supportsNonInteractive: false,
  load: async () => ({
    call: async () => ({
      type: 'text',
      value: CCMINI_REPL_HELP,
    }),
  }),
}

const exit: Command = {
  type: 'local',
  name: 'exit',
  aliases: ['quit'],
  description: 'Exit the frontend',
  immediate: true,
  supportsNonInteractive: false,
  load: async () => ({
    call: async () => ({
      type: 'skip',
    }),
  }),
}

export function getCcminiCommands(): Command[] {
  return [help, exit]
}
