export type LocalCommandResult =
  | { type: 'text'; value: string }
  | { type: 'skip' }

export type LocalCommandModule = {
  call: (..._args: unknown[]) => Promise<LocalCommandResult>
}

export type CommandAvailability = never

export type CommandResultDisplay = 'skip' | 'system' | 'user'

export type LocalJSXCommandContext = never

export type PromptCommand = never

export type ResumeEntrypoint = never

export type CommandBase = {
  description: string
  name: string
  aliases?: string[]
  hasUserSpecifiedDescription?: boolean
  whenToUse?: string
  disableModelInvocation?: boolean
  source?: 'builtin' | 'mcp' | 'plugin' | 'bundled' | 'managed'
  loadedFrom?: 'mcp' | 'plugin' | 'bundled' | 'managed'
  kind?: 'workflow'
  pluginInfo?: {
    pluginManifest: {
      name: string
    }
    repository: string
  }
  immediate?: boolean
  userFacingName?: () => string
}

export type Command = CommandBase & {
  type: 'local'
  supportsNonInteractive: boolean
  load: () => Promise<LocalCommandModule>
}

export function getCommandName(cmd: CommandBase): string {
  return cmd.userFacingName?.() ?? cmd.name
}

export function isCommandEnabled(_cmd: CommandBase): boolean {
  return true
}
